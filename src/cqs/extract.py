"""原文層から主張候補を切り出す補助。

抽出そのものはLLMに任せてよいが、確認状態の付与は出典種別からの機械的規則に限る。
ここでは「文に割って、対象エンティティに触れている文だけを残す」までを行う。
"""

from __future__ import annotations

import re
from typing import Sequence

from . import textutil

# ナビゲーション・定型文など、主張になりにくい行
_NOISE = re.compile(
    r"(Copyright|All Rights Reserved|©|プライバシーポリシー|利用規約|クッキー|Cookie|"
    r"お問い合わせ|サイトマップ|ページの先頭|メニュー|検索|シェア|フォロー|"
    r"フリー百科事典|ウィキペディア|この記事には|ノートページ|出典検索|"
    r"出典がまったく示されていない|独自研究|ファンサイト的な内容|改善やノートページ|"
    r"^\s*\d+\s*$|^[\s|/･・>»-]+$)",
    re.I,
)


# 文の終わりらしさ。日本語の本文はほぼ句点・感嘆符・疑問符で終わる。
_SENTENCE_END = ("。", "！", "？", "!", "?", "」", "』")
_IMAGE_LINE = re.compile(r"^\[画像: ")
_HAS_JA = re.compile(r"[ぁ-んァ-ヴ一-龥]")


def is_noise(sentence: str) -> bool:
    return bool(_NOISE.search(sentence))


def is_fragment(sentence: str) -> bool:
    """見出し・メニュー・画像の代替テキストなど、主張になり得ない断片か。"""
    if _IMAGE_LINE.match(sentence):
        return True
    if not _HAS_JA.search(sentence):   # ローマ字表記だけの行など
        return True
    return not sentence.endswith(_SENTENCE_END)


def candidate_sentences(
    text: str,
    *,
    entities: Sequence[str] = (),
    min_len: int = 10,
    max_len: int = 400,
) -> list[dict]:
    """主張候補の文を返す。entities を渡すと、いずれかに触れる文だけに絞る。

    返り値の各要素は {"text": 文, "entities": 言及したエンティティ, "offset": 本文中の位置}。
    offset は出典参照（原文のどこから来たか）に使う。
    """
    out: list[dict] = []
    cursor = 0
    for s in textutil.split_sentences(text, min_len=min_len):
        idx = text.find(s, cursor)
        if idx >= 0:
            cursor = idx + len(s)
        if len(s) > max_len or is_noise(s) or is_fragment(s):
            continue
        hit = [e for e in entities if e and e in s]
        if entities and not hit:
            continue
        out.append({"text": s, "entities": hit, "offset": idx if idx >= 0 else None})
    return out


def dedupe(candidates: list[dict]) -> list[dict]:
    """同一文の重複を落とす。"""
    seen: set[str] = set()
    out = []
    for c in candidates:
        key = re.sub(r"\s+", "", c["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
