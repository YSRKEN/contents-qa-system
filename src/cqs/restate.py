"""原文の記述を、独立して読める主張に言い直す。

機械的な文分割は、説明文には効くが物語には効かない。地の文とセリフは主語を
繰り返さないので、文だけを取り出すと

    「アイドルとしての才能は欠片もない。」
    「その努力は、無駄だというのに。」

のように、誰が誰に何をしたのか分からない断片になる。知識層に入っても、
検索語も当たらず、読んでも意味が取れない。

ここでは原文の一区切りをLLMに渡し、**それだけを読んで意味が通る主張**へ
言い直させる。人物名・場面・作用を文の中に入れさせる。

    白草四音は、N.I.A編で葛城リーリヤとそのプロデューサーに対し
    「アイドルとしての才能は欠片もない」と侮辱した（第17話）。

言い直しはLLMに任せるが、**確認状態は出典種別からの機械的規則のまま**にする
（設計上の決めごと）。作り話を防ぐため、各主張には原文の一節をそのまま写した
`locator` を返させ、原文に無ければその主張を捨てる。

APIキーが無くても使える。`prompts()` が出すプロンプトを手元のAIチャット
（Claude Code、ChatGPTなど）に貼り、返ってきたJSONを `apply()` に渡せばよい。
キーがあるなら `restate_version()` が同じことを自動で行う。
"""

from __future__ import annotations

import json
from typing import Sequence

from . import llm, textutil
from .store import WorkStore

NOTE = "原文から言い直した主張"

_PROMPT = """作品『{title}』の資料から、知識ベースに入れる主張を作る。

次の規則を守ること。

1. 一つの主張は、**それだけを読んで意味が通る**こと。誰が、誰に、何を、
   どの場面でしたのかを文の中に書く。「彼女は」「相手のアイドルは」のような
   指示語で済ませない。資料の中で名前が分かるなら、その名前に置き換える。
2. 資料に書かれていないことを足さない。推測で補わない。人物の関係や動機は、
   資料にそう書かれている場合だけ書く。
3. 印象的な発言は、鍵括弧でそのまま引用して主張の中に入れる。
4. 一つの出来事を細切れにしない。やり取りの一往復、ひとつの行為を1件にまとめる。
5. 見出し・目次・ナビゲーション・コメント欄・広告からは作らない。

各主張について次を返す。

    text     : 言い直した主張（120字程度まで）
    locator  : その主張の根拠になった箇所を、**資料から一字一句そのまま**写したもの
               （20〜60字程度。改行は空白に置き換えてよい）
    entities : その主張が扱う対象の名前。次の一覧にあるものだけを使う

対象の一覧: {entities}

JSON配列だけを返す。例:
[{{"text":"白草四音は葛城リーリヤのプロデューサーを「見る目がない」と侮辱した。","locator":"あなたはなぜこれを選んだのですか？","entities":["白草四音","葛城リーリヤ"]}}]

--- 資料ここから（節見出しは「## 」で始まる） ---
{chunk}
--- 資料ここまで ---"""


def chunks(text: str, *, size: int = 3000, overlap: int = 300) -> list[tuple[int, str]]:
    """原文を、行の切れ目で区切って渡す大きさに分ける。(原文中の位置, 本文)。

    直前の節見出しを次の塊の先頭に付け直す。塊の途中で場面が変わると、
    その場面が何の話か分からなくなるため。
    """
    out: list[tuple[int, str]] = []
    pos, heading = 0, ""
    while pos < len(text):
        end = min(pos + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", pos + size // 2, end)
            if cut > pos:
                end = cut
        body = text[pos:end]
        out.append((pos, (heading + "\n" + body) if heading else body))
        for line in body.split("\n"):
            if line.startswith("## "):
                heading = line
        nxt = end - overlap
        pos = nxt if nxt > pos else end        # 進まなくなるなら重なりを諦める
    return out


def _entity_names(store: WorkStore) -> list[str]:
    return [e["name"] for e in store.list_entities() if (e["claims"] or 0) or e["kind"] == "character"]


def _names_in(chunk: str, names: Sequence[str]) -> list[str]:
    """その塊に実際に出てくる名前だけを渡す。

    作品全体の一覧（数百件）をそのまま渡すと、プロンプトの大半が一覧で埋まり、
    その場面と関係のない名前を選ばせる誘いにもなる。別名でも当たるように
    空白の違いは無視して見る。
    """
    flat = textutil.flatten(chunk)
    return [n for n in names if n in chunk or textutil.flatten(n) in flat]


def restate_version(
    store: WorkStore, version_id: int, *, size: int = 3000, model: str | None = None,
    max_chunks: int | None = None,
) -> list[dict]:
    """その版の原文から、言い直した主張の候補を作る。登録はしない。"""
    v = store.get_version(version_id)
    if not v:
        raise ValueError(f"出典版が見つかりません: {version_id}")
    names = _entity_names(store)
    title = store.meta.get("title", "")
    out: list[dict] = []
    seen: set[str] = set()
    parts = chunks(v["text"], size=size)
    if max_chunks:
        parts = parts[:max_chunks]
    for offset, chunk in parts:
        got = llm.chat_json(
            _PROMPT.format(title=title, entities="、".join(_names_in(chunk, names)), chunk=chunk),
            max_tokens=4000, model=model,
        )
        if not isinstance(got, list):
            continue
        for item in got:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            locator = str(item.get("locator") or "").strip()
            if not text or not locator:
                continue
            # 作り話を捨てる。根拠の一節が原文に無ければ採らない
            at = _find(v["text"], locator)
            if at is None:
                continue
            key = textutil.flatten(text)
            if key in seen:
                continue
            seen.add(key)
            ents = [str(x) for x in (item.get("entities") or []) if str(x) in names]
            out.append({"text": text, "locator": locator, "entities": ents, "offset": at})
    out.sort(key=lambda c: c["offset"])
    return out


def _find(text: str, needle: str) -> int | None:
    """原文から根拠の一節を探す。空白と改行の違いは無視する。"""
    return textutil.find_flat(text, needle)


def prompts(store: WorkStore, version_id: int, *, size: int = 3000) -> list[str]:
    """LLMに渡すプロンプトを、塊ごとに作って返す。

    APIキーが無い環境のための道。出したプロンプトを手元のAIチャットに貼り、
    返ってきたJSONを apply() に渡す。キーがあるかどうかで結果は変わらない。
    """
    v = store.get_version(version_id)
    if not v:
        raise ValueError(f"出典版が見つかりません: {version_id}")
    names = _entity_names(store)
    title = store.meta.get("title", "")
    return [_PROMPT.format(title=title, entities="、".join(_names_in(chunk, names)), chunk=chunk)
            for _, chunk in chunks(v["text"], size=size)]


def apply(store: WorkStore, version_id: int, items: Sequence[dict]) -> dict:
    """AIチャットが返したJSONを取り込む。原文に無い根拠のものは捨てる。

    返り値: {"proposals": 採ったもの, "dropped": 捨てたもの}
    """
    v = store.get_version(version_id)
    if not v:
        raise ValueError(f"出典版が見つかりません: {version_id}")
    names = set(_entity_names(store))
    kept: list[dict] = []
    dropped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            dropped.append({"item": item, "why": "形が違う"})
            continue
        text = str(item.get("text") or "").strip()
        locator = str(item.get("locator") or "").strip()
        if not text or not locator:
            dropped.append({"item": item, "why": "text か locator が空"})
            continue
        at = _find(v["text"], locator)
        if at is None:
            dropped.append({"item": item, "why": "根拠の一節が原文に無い"})
            continue
        key = textutil.flatten(text)
        if key in seen:
            dropped.append({"item": item, "why": "同じ主張が既にある"})
            continue
        seen.add(key)
        kept.append({"text": text, "locator": locator, "offset": at,
                     "entities": [str(x) for x in (item.get("entities") or []) if str(x) in names]})
    kept.sort(key=lambda c: c["offset"])
    return {"proposals": kept, "dropped": dropped}


def register_restated(
    store: WorkStore, version_id: int, proposals: Sequence[dict], *, note: str = NOTE
) -> list[int]:
    """言い直した主張を知識層へ入れる。確認状態は出典種別から機械的に決まる。"""
    ids = []
    for c in proposals:
        ids.append(store.add_claim(
            text=c["text"], source_version_id=version_id, entities=c.get("entities", ()),
            locator=c.get("locator"), offset=c.get("offset"), note=note,
        ))
    return ids


def dumps(proposals: Sequence[dict]) -> str:
    return json.dumps(list(proposals), ensure_ascii=False, indent=2)
