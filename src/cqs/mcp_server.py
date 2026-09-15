"""MCPサーバー。Claude Desktop / Claude Code から作品DBを直接引く。

設計メモの3ツール（主張検索・原文検索・出典参照）を中核に、
「検索を伴わない直接入力」と「他AI調査結果の取り込み」を書き込み側として足している。
返却値には確認状態・出典種別・その扱い方を必ず含め、
回答側がその値に基づいて表現を変えられるようにする。
"""

from __future__ import annotations

from typing import Any

from . import answer as answer_mod
from . import config, ingest
from .constants import SOURCE_KINDS, VERIFICATION_HANDLING, VERIFICATIONS

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore

INSTRUCTIONS = """作品ごとの知識層・原文層を引くためのツール群。

使い方:
1. list_works で作品を確認し、以降のツールに work（スラッグ）を渡す。
2. まず search_claims（知識層）を引く。足りなければ search_sources（原文層）で拾う。
3. 原文の前後が必要なら get_source で読む。

回答時の約束:
- 返却値の verification（確認状態）と handling に従う。
  公式確認は断定してよいが、記事のみは出典付きで提示し、ファン解釈は「そう解釈する感想がある」
  という形でのみ述べる。要再確認・未検証は根拠に使わない。
- 出典の優先順は 公式サイト・公式SNS ＞ インタビュー・紹介記事 ＞ 感想note。
  食い違う場合は片方を選ばず両方を並べる。
- contradicts に他の主張IDがある場合、その主張と矛盾している。両方を提示する。
"""

mcp = _Server(name="contents-qa-system", instructions=INSTRUCTIONS)


def _open(work: str):
    return config.open_work(work)


@mcp.tool(description="登録されている作品の一覧を返す。他のツールに渡す work はここの slug。")
def list_works() -> list[dict]:
    return config.list_works()


@mcp.tool(
    description=(
        "知識層の主張を検索する。エンティティ名（人物・用語・出来事）での絞り込みが基本。"
        "確認状態と出典種別が必ず付くので、その値に従って回答の書き方を変えること。"
    )
)
def search_claims(
    work: str,
    query: str | None = None,
    entity: str | None = None,
    kind: str | None = None,
    verification: str | None = None,
    include_superseded: bool = False,
    limit: int = 30,
) -> dict[str, Any]:
    with _open(work) as st:
        rows = st.search_claims(
            query=query,
            entity=entity,
            kind=kind,
            verification=verification,
            status=None if include_superseded else "active",
            limit=limit,
        )
        return {
            "work": work,
            "count": len(rows),
            "claims": rows,
            "handling": VERIFICATION_HANDLING,
        }


@mcp.tool(
    description=(
        "原文層（取得済みスナップショット）を全文検索し、該当箇所の抜粋と出典IDを返す。"
        "知識層にまだ主張として登録されていない記述を拾いたいときに使う。"
    )
)
def search_sources(work: str, query: str, kind: str | None = None, limit: int = 10) -> dict[str, Any]:
    with _open(work) as st:
        rows = st.search_sources(query, kind=kind, limit=limit)
        return {"work": work, "count": len(rows), "results": rows}


@mcp.tool(
    description=(
        "出典参照。source_version_id で指定した版の本文を、位置を指定して読む。"
        "search_sources や search_claims が返した該当箇所の前後を確かめるのに使う。"
    )
)
def get_source(work: str, source_version_id: int, offset: int = 0, length: int = 2000) -> dict[str, Any]:
    with _open(work) as st:
        v = st.source_excerpt(source_version_id, offset=offset, length=length)
        if not v:
            return {"error": f"出典版が見つかりません: {source_version_id}"}
        return v


@mcp.tool(description="エンティティ（人物・用語・出来事）の一覧と、登録されている主張の件数を返す。")
def list_entities(work: str) -> dict[str, Any]:
    with _open(work) as st:
        return {"work": work, "entities": st.list_entities()}


@mcp.tool(
    description=(
        "指定エンティティと同じ主張に現れる他のエンティティを、共有件数の多い順に返す。"
        "交流関係・関連人物を調べる起点。関係の中身は search_claims で確認すること。"
    )
)
def related_entities(work: str, name: str, limit: int = 20) -> dict[str, Any]:
    with _open(work) as st:
        return {"work": work, "name": name, "related": st.related_entities(name, limit=limit)}


@mcp.tool(
    description=(
        "資料を原文層に直接入れる（検索も取得もせず、渡したテキストをそのまま保存する）。"
        "手元の資料や書き起こしを入れる用途。kind には出典種別を指定する。"
    )
)
def add_document(
    work: str, title: str, text: str, kind: str = "manual", url: str | None = None, note: str | None = None
) -> dict[str, Any]:
    if kind not in SOURCE_KINDS:
        return {"error": f"未知の出典種別: {kind}", "valid": sorted(SOURCE_KINDS)}
    with _open(work) as st:
        return ingest.ingest_text(st, text, kind=kind, title=title, url=url, note=note)


@mcp.tool(
    description=(
        "URLを取得して原文層に版を積む。既に登録済みのURLなら新しい版として追加し、"
        "本文が変化していればその出典に依存する主張を自動で『要再確認』に落とす。"
    )
)
def fetch_url(work: str, url: str, kind: str | None = None) -> dict[str, Any]:
    with _open(work) as st:
        try:
            return ingest.ingest_url(st, url, kind=kind)
        except Exception as e:
            return {"error": str(e), "url": url}


@mcp.tool(
    description=(
        "他AIの調査報告（Deep Research結果など）を貼り付けて取り込む。"
        "報告自体は根拠にならない。(URL, 主張) の対を候補として積むだけで、"
        "実際にURLを取得して照合するまで知識層には入らない。照合は verify_candidates。"
    )
)
def add_ai_report(work: str, title: str, text: str) -> dict[str, Any]:
    with _open(work) as st:
        return ingest.ingest_ai_report(st, text, title=title)


@mcp.tool(description="未照合の候補を一覧する。")
def list_candidates(work: str, status: str = "pending", limit: int = 50) -> dict[str, Any]:
    with _open(work) as st:
        return {"work": work, "candidates": st.list_candidates(status=None if status == "all" else status, limit=limit)}


@mcp.tool(
    description=(
        "候補のURLを実際に取得し、主張がそのページに書かれているかを照合する。"
        "promote_threshold を与えた場合のみ、一致率がそれ以上の候補を知識層へ登録する。"
        "既定では照合結果を記録するだけなので、結果を見てから add_claim で登録するのがよい。"
    )
)
def verify_candidates(
    work: str, candidate_ids: list[int] | None = None, limit: int = 10, promote_threshold: float | None = None
) -> dict[str, Any]:
    with _open(work) as st:
        rows = ingest.verify_candidates(
            st, candidate_ids=candidate_ids, limit=limit, promote_threshold=promote_threshold
        )
        return {"work": work, "results": rows}


@mcp.tool(
    description=(
        "知識層に主張を1件登録する。source_version_id は根拠にした版のID。"
        "確認状態を省略すると出典種別から機械的に決まる（公式サイト→公式確認、感想note→ファン解釈）。"
        "supersedes に既存の主張IDを渡すと、その主張を消さずに上書き扱いにする。"
    )
)
def add_claim(
    work: str,
    text: str,
    source_version_id: int | None = None,
    entities: list[str] | None = None,
    verification: str | None = None,
    locator: str | None = None,
    note: str | None = None,
    supersedes: int | None = None,
) -> dict[str, Any]:
    with _open(work) as st:
        try:
            cid = st.add_claim(
                text=text,
                source_version_id=source_version_id,
                verification=verification,
                entities=entities or (),
                locator=locator,
                note=note,
                supersedes=supersedes,
            )
        except Exception as e:
            return {"error": str(e), "valid_verifications": sorted(VERIFICATIONS)}
        return {"claim_id": cid, "claim": st.get_claim(cid)}


@mcp.tool(
    description=(
        "主張どうしを関係づける。type は supersedes（上書き）/ contradicts（矛盾）/ supports（補強）。"
        "矛盾は主張の属性ではなく関係として持つので、片方を消さずに両方を残す。"
    )
)
def link_claims(work: str, from_claim_id: int, to_claim_id: int, type: str, note: str | None = None) -> dict[str, Any]:
    with _open(work) as st:
        try:
            st.link_claims(from_claim_id, to_claim_id, type, note=note)
        except Exception as e:
            return {"error": str(e)}
        return {"ok": True}


@mcp.tool(
    description=(
        "エンティティを登録する（表記ゆれを別名として登録できる）。"
        "主張の対象として使うほか、質問文からの検索の当たりをよくする。"
    )
)
def add_entity(
    work: str, name: str, kind: str | None = None, aliases: list[str] | None = None, note: str | None = None
) -> dict[str, Any]:
    with _open(work) as st:
        eid = st.ensure_entity(name, kind=kind, aliases=aliases or (), note=note)
        return {"entity_id": eid, "name": name}


@mcp.tool(
    description=(
        "ある版の本文から主張候補の文を切り出す（登録はしない）。"
        "候補を読んで、作品内の事実として意味のあるものだけを add_claim で登録する。"
    )
)
def propose_claims(
    work: str, source_version_id: int, entities: list[str] | None = None, limit: int = 50
) -> dict[str, Any]:
    with _open(work) as st:
        try:
            rows = ingest.propose_claims(st, source_version_id, entities=entities or (), limit=limit)
        except Exception as e:
            return {"error": str(e)}
        return {"work": work, "source_version_id": source_version_id, "candidates": rows}


@mcp.tool(
    description=(
        "質問に対する材料（主張・原文抜粋・関連エンティティ）をまとめて集める。"
        "個別にツールを引く前の見取り図として使う。回答は集まった材料に基づいて自分で書く。"
    )
)
def gather_for_question(work: str, question: str, max_claims: int = 40) -> dict[str, Any]:
    with _open(work) as st:
        ctx = answer_mod.retrieve(st, question, max_claims=max_claims)
        return {**ctx, "handling": VERIFICATION_HANDLING}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
