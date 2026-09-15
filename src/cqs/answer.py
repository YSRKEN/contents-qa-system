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
- 一部の主張には「区分」が付いている。同じ作品世界でも、どの作品（TVシリーズ／劇場版／スピンオフなど）についての記述かを表す。
  区分の違う主張を、区分を伏せたまま同じ作品の事実として並べない。答える際はどの区分の記述かを示し、
  区分をまたぐ食い違いは矛盾ではなく「作品ごとの違い」として扱う。質問が区分を指定している場合は、その区分の主張を優先する。
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


def _best_window(positions: Sequence[int], hits: dict[int, float], width: int) -> tuple[int, int, float]:
    """当たりが最も密集している、幅 width の区間を返す。

    出典を丸ごと1つの塊として点を付けると、Wikipediaのように何百件も主張のある出典は
    「当たりの総和は大きいが、当たりどうしが記事の端から端まで散っている」状態になり、
    正規化で割り引くと今度は該当節ごと沈む。実際に渡すのは連続した一区間なので、
    その一区間が持つ当たりの量で比べる。
    """
    scores = [hits.get(cid, 0.0) for cid in positions]
    n = len(scores)
    if n <= width:
        return 0, n, sum(scores)
    cur = sum(scores[:width])
    best = (0, width, cur)
    for i in range(1, n - width + 1):
        cur += scores[i + width - 1] - scores[i - 1]
        if cur > best[2]:
            best = (i, i + width, cur)
    return best


def _claims_in_order(store: WorkStore) -> dict[int, list[int]]:
    """出典ごとの主張IDを、原文での並び順（=ID順）で返す。"""
    order: dict[int, list[int]] = {}
    for r in store.conn.execute(
        "SELECT id, source_version_id AS sv FROM claims "
        "WHERE status='active' AND source_version_id IS NOT NULL ORDER BY id"
    ):
        order.setdefault(int(r["sv"]), []).append(int(r["id"]))
    return order


def _fetch_claims(store: WorkStore, ids: Sequence[int]) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [
        store._claim_row(r)
        for r in store.conn.execute(
            store._CLAIM_SELECT + f" WHERE c.id IN ({marks}) ORDER BY c.id", tuple(ids)
        )
    ]


def split_section(text: str) -> tuple[str, str]:
    """主張は「節見出し: 本文」の形で入っている。見出しと本文に分ける。"""
    head, sep, body = text.partition(": ")
    if sep and len(head) <= 80:
        return head, body
    return "", text


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

    def bump(c: dict, points: float) -> None:
        prev = scored.get(c["id"])
        scored[c["id"]] = (prev[0] + points if prev else points, c)

    def matched_weight(c: dict, needles: Sequence[str], points: float) -> float:
        """本文に当たったのか、節見出しにだけ当たったのかで重みを変える。

        名言集の主張は「By 鹿目まどか / ○○の名言: <セリフ>」の形で入る。セリフ本体は
        誰のことも名指ししていないのに、見出しのおかげでエンティティ一致として満点を取り、
        「関係は？」のような質問の材料を数十件単位で食い潰していた。
        見出しは「この主張が何の話題の下にあるか」であって「何を述べているか」ではないので、
        見出しだけの一致は本文の一致より軽くする。
        """
        _, body = split_section(c["text"])
        flat_body = textutil.flatten(body)
        if any(textutil.flatten(n) in flat_body for n in needles):
            return points
        return points * 0.25

    alias_map = {e["name"]: [e["name"], *e.get("aliases", [])] for e in store.list_entities()}
    for name in ents:
        names = alias_map.get(name, [name])
        # エンティティ一致は索引を引くだけで安いので、打ち切らずに全部見る。
        # ここを max_claims の数倍で打ち切ると、主人公のように何百件も付く人物では
        # 打ち切りの内側に入った出典だけが候補になり、出典の選び方が偶然で決まる。
        for c in store.search_claims(entity=name, limit=100000):
            bump(c, matched_weight(c, names, 4))
    total = store.stats()["claims_active"]
    weights = {t: term_weight(store, t, total) for t in terms}
    # ほぼ全件に当たる語は点数を支配するだけなので落とす。ただし全部落ちると
    # 何も返らなくなるので、その場合は重みの大きい順に2語だけ残す。
    useful = [t for t, w in weights.items() if w > 0.5]
    if not useful:
        useful = [t for t, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w > 0][:2]
    for t in useful:
        for c in store.search_claims(query=t, limit=max_claims * 3):
            bump(c, matched_weight(c, [t], max(weights[t], 0.5)))

    ranked = sorted(
        scored.values(),
        key=lambda x: (-x[0], _VERIF_RANK.get(x[1]["verification"], 9), -x[1]["priority"], x[1]["id"]),
    )

    # --- 2. 当たりが集まった出典を、並び順ごと取り出す
    by_source: dict[int, list[tuple[int, dict]]] = {}
    for score, c in ranked:
        if c["source_version_id"]:
            by_source.setdefault(int(c["source_version_id"]), []).append((score, c))
    def title_bonus(items: list[tuple[float, dict]]) -> float:
        """出典のタイトル自体が検索語を含むなら、その出典が答えそのものである可能性が高い。

        ただし「関係」「作品」のようなありふれた語はどの記事の見出しにも出るので、
        語の重みをそのまま使い、加点の総量にも上限を置く。ここを大きくすると、
        中身がほとんど当たっていない出典が題名だけで節ごと選ばれてしまう。
        """
        title = (items[0][1].get("source_title") or "") + " " + (items[0][1].get("url") or "")
        return min(sum(weights.get(t, 0) * 2 for t in terms if t and t in title), 8.0)

    # 節が材料を食い尽くさないように、全体の6割までに抑える
    run_budget = max(8, int(max_claims * 0.6 / 3))
    in_order = _claims_in_order(store)
    windows: dict[int, tuple[int, int, float]] = {}
    for svid, items in by_source.items():
        hits = {c["id"]: score for score, c in items}
        windows[svid] = _best_window(in_order.get(svid, []), hits, run_budget)

    def source_score(svid: int, items: list[tuple[float, dict]]) -> float:
        # 渡すのは連続した一区間なので、その区間が持つ当たりの量で比べる。
        # 幅が同じなので出典の大きさで割る必要はない。
        base = windows[svid][2] / math.sqrt(run_budget) + title_bonus(items)
        # 出典の優先順を効かせる。感想noteの節が公式・記事の節を押しのけないように
        return base * (0.5 + items[0][1].get("priority", 0) / 200)

    runs: list[dict] = []
    used_sources: list[int] = []
    chosen = [
        (svid, items)
        for svid, items in sorted(by_source.items(), key=lambda kv: -source_score(kv[0], kv[1]))[:3]
        if len(items) >= 3 or title_bonus(items) > 0
    ]
    for svid, _ in chosen:
        start, end, _score = windows[svid]
        runs.extend(_fetch_claims(store, in_order.get(svid, [])[start:end]))
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
        seg = f" / 区分: {c['segment']}" if c.get("segment") else ""
        lines.append(
            f"- [claim {c['id']}] {c['text']}\n"
            f"    確認状態: {c['verification_label']}（{c['verification']}） / "
            f"出典種別: {c['kind_label']}{seg} / 出典: {src}{extra}"
        )

    lines.append("")
    lines.append("# 原文層の該当箇所（知識層に未登録の記述を拾うため）")
    if not ctx["sources"]:
        lines.append("（該当なし）")
    for s in ctx["sources"]:
        note = "" if s["citable"] else "  ※この出典種別は根拠にできない（候補どまり）"
        ssg = f" / 区分: {s['segment']}" if s.get("segment") else ""
        lines.append(
            f"- [source_version {s['source_version_id']}] {s.get('source_title') or s.get('title') or ''}"
            f" / {s['kind_label']}{ssg} / {s.get('url') or ''}{note}\n    …{s['excerpt']}…"
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
