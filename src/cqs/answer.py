"""回答層。質問→検索→出典種別に応じた扱いを指示した上での回答。

LLMを呼ぶかどうかは任意。APIキーが無い環境では、検索した材料と
そのまま貼れるプロンプトを返すところまでを担う（Claude Desktop / Claude Code へ渡す想定）。
"""

from __future__ import annotations

import os
from typing import Any, Sequence

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
    hits: list[str] = []
    for e in store.list_entities():
        names = [e["name"], *e.get("aliases", [])]
        if any(n and n in question for n in names):
            hits.append(e["name"])
    return hits


_VERIF_RANK = {"official": 0, "article": 1, "secondhand": 2, "fan_interpretation": 3,
               "needs_recheck": 4, "unverified": 5}


def retrieve(
    store: WorkStore,
    question: str,
    *,
    max_claims: int = 40,
    max_sources: int = 5,
    extra_terms: Sequence[str] = (),
) -> dict[str, Any]:
    """質問に対する材料を集める。

    エンティティ一致だけで埋めると、人物名を含まない記述（日付や設定の文など）が
    まったく出てこない。エンティティ一致と語一致を足し合わせた点数で並べる。
    extra_terms には、質問の言い換えや出来事名など、外から足したい検索語を渡す。
    """
    ents = mentioned_entities(store, question)
    terms = [t for t in extra_terms if t.strip()]

    scored: dict[int, tuple[int, dict]] = {}

    def bump(c: dict, points: int) -> None:
        prev = scored.get(c["id"])
        scored[c["id"]] = (prev[0] + points if prev else points, c)

    for name in ents:
        for c in store.search_claims(entity=name, limit=max_claims * 2):
            bump(c, 4)
    for q in [question, *terms]:
        for c in store.search_claims(query=q, limit=max_claims):
            bump(c, 6)

    ranked = sorted(
        scored.values(),
        key=lambda x: (-x[0], _VERIF_RANK.get(x[1]["verification"], 9), -x[1]["priority"], x[1]["id"]),
    )
    claims = [c for _, c in ranked][:max_claims]

    related = {n: store.related_entities(n) for n in ents}
    sources: list[dict] = []
    seen_src: set[int] = set()
    for q in [question, *terms]:
        for s in store.search_sources(q, limit=max_sources):
            if s["source_version_id"] not in seen_src:
                seen_src.add(s["source_version_id"])
                sources.append(s)
    return {
        "question": question,
        "entities": ents,
        "terms": terms,
        "related_entities": related,
        "claims": claims,
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
    max_claims: int = 40,
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
