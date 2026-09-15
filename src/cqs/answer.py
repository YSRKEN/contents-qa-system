"""回答層。質問→検索→出典種別に応じた扱いを指示した上での回答。

LLMを呼ぶかどうかは任意。APIキーが無い環境では、検索した材料と
そのまま貼れるプロンプトを返すところまでを担う（Claude Desktop / Claude Code へ渡す想定）。
"""

from __future__ import annotations

import math
import os
from typing import Any, Sequence

from . import report, textutil
from .constants import VERIFICATION_HANDLING, verification_label
from .store import WorkStore

DEFAULT_MODEL = os.environ.get("CQS_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """あなたは特定の作品について、蓄積された出典付きの主張だけを根拠に答える。

守ること:
- 与えられた材料に無いことは答えない。推測で補わない。材料が足りなければ「この知識層には無い」と述べる。
- 各主張には確認状態が付いている。確認状態ごとの扱いを必ず守る:
{handling}
- 出典の優先順は 公式サイト・公式SNS ＞ インタビュー・紹介記事 ＞ 感想note。
  下位の出典が上位と食い違う場合は、両方を並べ、どちらがどの出典かを示す。
- 主張どうしが矛盾している場合は、片方を選ばず両方を提示する。
- 同じ出典から取り出した主張は、claim ID の小さい順が原文での並び順に対応する。時系列を組み立てるときの手がかりにしてよい。
- 根拠にした主張は、末尾で claim ID と出典URLを挙げる。
- 感想noteは「そう感じた人がいる」という事実の根拠にはなるが、作品内の事実の根拠にはしない。
"""


def _handling_block() -> str:
    return "\n".join(
        f"  - {verification_label(k)}（{k}）: {v}" for k, v in VERIFICATION_HANDLING.items()
    )


def mentioned_entities(store: WorkStore, question: str) -> list[str]:
    """質問文に含まれる登録済みエンティティ名（別名含む）を拾う。"""
    flat = textutil.flatten(question)
    hits: list[str] = []
    for e in store.list_entities():
        names = [e["name"], *e.get("aliases", [])]
        if any(n and (n in question or textutil.flatten(n) in flat) for n in names):
            hits.append(e["name"])
    return hits


_VERIF_RANK = {"official": 0, "article": 1, "secondhand": 2, "fan_interpretation": 3,
               "needs_recheck": 4, "unverified": 5}


def question_terms(question: str) -> list[str]:
    """質問文から検索語を取り出す。

    質問文をそのまま全文検索に渡すと、文全体が1つのフレーズとして扱われ、
    ほぼ必ず0件になる。漢字・カタカナ・英数の連なりを語として拾う
    （助詞はひらがななので自然に落ちる）。
    """
    return [t for t in report.claim_tokens(question) if len(t) >= 2]


def term_weight(store: WorkStore, term: str, total: int) -> float:
    """検索語の重み。多くの主張に当たる語ほど軽くする。

    「作品」のような語は数百件に当たるため、そのままでは点数を支配してしまい、
    「時系列」のような効く語がかき消される。
    """
    df = store.count_claims(term)
    if df <= 0 or total <= 1:
        return 0.0
    return round(8.0 * math.log(max(total / df, 1.0)) / math.log(total), 2)


def _run_from_source(store: WorkStore, source_version_id: int, ids: Sequence[int], limit: int) -> list[dict]:
    """当たりが集まった出典を、原文の並び順（=claim IDの順）ごと取り出す。

    「時系列を出せ」「あらすじを出せ」のような質問は、語が一致する文を拾い集めても
    答えにならない。順序そのものが答えなので、節を丸ごと渡す。
    """
    rows = [
        store._claim_row(r)
        for r in store.conn.execute(
            store._CLAIM_SELECT
            + " WHERE c.source_version_id = ? AND c.status = 'active' ORDER BY c.id",
            (source_version_id,),
        )
    ]
    if len(rows) <= limit:
        return rows
    # 収まらない場合は、当たりが集まっているあたりを中心に切り出す
    center = (min(ids) + max(ids)) // 2
    order = sorted(range(len(rows)), key=lambda i: abs(rows[i]["id"] - center))
    keep = sorted(order[:limit])
    return [rows[i] for i in keep]


def retrieve(
    store: WorkStore,
    question: str,
    *,
    max_claims: int = 120,
    max_sources: int = 5,
    extra_terms: Sequence[str] = (),
) -> dict[str, Any]:
    """質問に対する材料を集める。

    三段構え。
    1. エンティティ一致と語一致の合算で主張に点を付ける。
    2. 当たりが集まった出典については、その区間を原文の並び順ごと取り出す
       （時系列やあらすじのように、順序そのものが答えになる質問のため）。
    3. 矛盾関係にある主張は、片方が入ったらもう片方も必ず入れる。
    """
    ents = mentioned_entities(store, question)
    terms = list(dict.fromkeys([*(t.strip() for t in extra_terms if t.strip()), *question_terms(question)]))

    scored: dict[int, tuple[int, dict]] = {}

    def bump(c: dict, points: int) -> None:
        prev = scored.get(c["id"])
        scored[c["id"]] = (prev[0] + points if prev else points, c)

    for name in ents:
        for c in store.search_claims(entity=name, limit=max_claims * 2):
            bump(c, 4)
    total = store.stats()["claims_active"]
    weights = {t: term_weight(store, t, total) for t in terms}
    # ほぼ全件に当たる語は点数を支配するだけなので落とす。ただし全部落ちると
    # 何も返らなくなるので、その場合は重みの大きい順に2語だけ残す。
    useful = [t for t, w in weights.items() if w > 0.5]
    if not useful:
        useful = [t for t, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w > 0][:2]
    for t in useful:
        for c in store.search_claims(query=t, limit=max_claims * 3):
            bump(c, max(weights[t], 0.5))

    ranked = sorted(
        scored.values(),
        key=lambda x: (-x[0], _VERIF_RANK.get(x[1]["verification"], 9), -x[1]["priority"], x[1]["id"]),
    )

    # --- 2. 当たりが集まった出典を、並び順ごと取り出す
    by_source: dict[int, list[tuple[int, dict]]] = {}
    for score, c in ranked:
        if c["source_version_id"]:
            by_source.setdefault(int(c["source_version_id"]), []).append((score, c))
    # 出典ごとの主張の総数。大きい出典ほど弱い当たりが積み上がって有利になるので割る。
    sizes = {
        int(r["sv"]): int(r["n"])
        for r in store.conn.execute(
            "SELECT source_version_id AS sv, COUNT(*) AS n FROM claims "
            "WHERE status='active' AND source_version_id IS NOT NULL GROUP BY source_version_id"
        )
    }

    def title_bonus(items: list[tuple[float, dict]]) -> float:
        """出典のタイトル自体が検索語を含むなら、その出典が答えそのものである可能性が高い。"""
        title = (items[0][1].get("source_title") or "") + " " + (items[0][1].get("url") or "")
        return sum(max(weights.get(t, 0), 1.0) * 4 for t in terms if t and t in title)

    def source_score(svid: int, items: list[tuple[float, dict]]) -> float:
        base = sum(s for s, _ in items) / math.sqrt(max(sizes.get(svid, 1), 1)) + title_bonus(items)
        # 出典の優先順を効かせる。感想noteの節が公式・記事の節を押しのけないように
        return base * (0.5 + items[0][1].get("priority", 0) / 200)

    runs: list[dict] = []
    used_sources: list[int] = []
    chosen = [
        (svid, items)
        for svid, items in sorted(by_source.items(), key=lambda kv: -source_score(kv[0], kv[1]))[:3]
        if len(items) >= 3 or title_bonus(items) > 0
    ]
    # 節が材料を食い尽くさないように、全体の6割までに抑える
    run_budget = max(8, int(max_claims * 0.6 / max(len(chosen), 1)))
    for svid, items in chosen:
        runs.extend(_run_from_source(store, svid, [c["id"] for _, c in items], run_budget))
        used_sources.append(svid)

    # 並び: まず当たりの強い主張（点で答える質問のため）、次に節を原文の並び順で
    # （順序が答えになる質問のため）、最後に残り。
    claims: list[dict] = []
    seen: set[int] = set()
    head_budget = max(4, int(max_claims * 0.4))
    for _, c in ranked[:head_budget]:
        seen.add(c["id"])
        claims.append(c)
    for c in runs:
        if c["id"] not in seen:
            seen.add(c["id"])
            claims.append(c)
    for _, c in ranked:
        if len(claims) >= max_claims:
            break
        if c["id"] not in seen:
            seen.add(c["id"])
            claims.append(c)

    # --- 3. 矛盾する相手は必ず一緒に入れる（片方だけでは「両方を並べる」が守れない）
    for c in list(claims):
        for other in c.get("contradicts") or []:
            if other in seen:
                continue
            o = store.get_claim(int(other))
            if o:
                seen.add(int(other))
                claims.append(o)

    related = {n: store.related_entities(n) for n in ents}
    sources: list[dict] = []
    seen_src: set[int] = set()
    for t in terms:
        for s in store.search_sources(t, limit=max_sources):
            if s["source_version_id"] not in seen_src:
                seen_src.add(s["source_version_id"])
                sources.append(s)
    return {
        "question": question,
        "entities": ents,
        "terms": terms,
        "term_weights": weights,
        "related_entities": related,
        "claims": claims,
        "runs_from_sources": used_sources,
        "sources": sources[:max_sources],
    }


def format_context(ctx: dict[str, Any]) -> str:
    """LLMに渡す材料を、確認状態と出典が見える形で整形する。"""
    lines: list[str] = []
    if ctx.get("terms"):
        lines.append(f"# 使った検索語: {', '.join(ctx['terms'])}")
    if ctx["entities"]:
        lines.append(f"# 質問に現れた登録済みエンティティ: {', '.join(ctx['entities'])}")
        for name, rel in ctx["related_entities"].items():
            if rel:
                pairs = ", ".join(f"{r['name']}({r['shared']}件)" for r in rel[:10])
                lines.append(f"  - {name} と同じ主張に現れる: {pairs}")
        lines.append("")

    lines.append("# 知識層の主張")
    if not ctx["claims"]:
        lines.append("（該当なし）")
    for c in ctx["claims"]:
        src = c.get("url") or c.get("source_title") or "（出典なし）"
        extra = f" / 矛盾: claim {', '.join(str(x) for x in c['contradicts'])}" if c["contradicts"] else ""
        lines.append(
            f"- [claim {c['id']}] {c['text']}\n"
            f"    確認状態: {c['verification_label']}（{c['verification']}） / "
            f"出典種別: {c['kind_label']} / 出典: {src}{extra}"
        )

    lines.append("")
    lines.append("# 原文層の該当箇所（知識層に未登録の記述を拾うため）")
    if not ctx["sources"]:
        lines.append("（該当なし）")
    for s in ctx["sources"]:
        note = "" if s["citable"] else "  ※この出典種別は根拠にできない（候補どまり）"
        lines.append(
            f"- [source_version {s['source_version_id']}] {s.get('source_title') or s.get('title') or ''}"
            f" / {s['kind_label']} / {s.get('url') or ''}{note}\n    …{s['excerpt']}…"
        )
    return "\n".join(lines)


def build_prompt(ctx: dict[str, Any]) -> dict[str, str]:
    system = SYSTEM_PROMPT.format(handling=_handling_block())
    user = f"{format_context(ctx)}\n\n# 質問\n{ctx['question']}"
    return {"system": system, "user": user}


def answer(
    store: WorkStore,
    question: str,
    *,
    model: str | None = None,
    max_claims: int = 120,
    use_llm: bool = True,
    extra_terms: Sequence[str] = (),
) -> dict[str, Any]:
    """質問に答える。APIキーが無い/use_llm=False の場合は材料とプロンプトだけを返す。"""
    ctx = retrieve(store, question, max_claims=max_claims, extra_terms=extra_terms)
    prompt = build_prompt(ctx)
    result: dict[str, Any] = {"context": ctx, "prompt": prompt, "answer": None, "model": None}

    if not use_llm:
        result["reason"] = "LLM呼び出しを行わない指定です。"
        return result
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        result["reason"] = (
            "ANTHROPIC_API_KEY が未設定のため、回答生成は行いませんでした。"
            "上のプロンプトを Claude Desktop / Claude Code に貼るか、MCPサーバー経由で使ってください。"
        )
        return result
    try:
        import anthropic  # type: ignore
    except ImportError:
        result["reason"] = (
            "anthropic パッケージが未インストールです（pip install 'contents-qa-system[llm]'）。"
        )
        return result

    client = anthropic.Anthropic()
    model = model or DEFAULT_MODEL
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=prompt["system"],
            messages=[{"role": "user", "content": prompt["user"]}],
        )
    except anthropic.APIStatusError as e:
        result["reason"] = f"APIエラー ({e.status_code}): {e.message}"
        return result
    except anthropic.APIConnectionError as e:
        result["reason"] = f"接続エラー: {e}"
        return result

    if resp.stop_reason == "refusal":
        result["reason"] = "モデルが応答を拒否しました。"
        return result
    result["answer"] = "\n".join(b.text for b in resp.content if b.type == "text")
    result["model"] = resp.model
    return result
