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
    r"ご協力ください|ノートを参照|過剰な記述|網羅するものではありません|出典を追加して|"
    r"^\s*\d+\s*$|^[\s|/･・>»-]+$|^↑)",
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


# 節見出し。<卒業ライブ> 【おそらく逆転の要因】 ## 見出し のような形。
_HEADING_WRAPPED = re.compile(r"^[<＜【〔［\[]\s*(.+?)\s*[>＞】〕］\]]$")
_HEADING_MD = re.compile(r"^#{1,6}\s+(.+)$")
_LEADING_SYMBOLS = re.compile(r"^[^\w\u3040-\u30ff\u4e00-\u9fff]+")
# 見出しの位置に現れるが見出しではない語（Wikiの編集リンクなど）
_NOT_A_HEADING = re.compile(r"^(編集|ソースを編集|続きを読む|目次|関連記事|広告|スポンサーリンク|PR)$")
# 見出しの末尾に付くWikiの編集リンク（「概要[編集]」）
_EDIT_LINK = re.compile(r"\s*\[(編集|ソースを編集|edit)\]\s*$")
# 見出しのすぐ下に単独行で入る編集リンク（Wikipediaの「[編集]」）。
# 見出しでも本文でもないので、節の連鎖を切らないよう無かったことにする。
_EDIT_LINE = re.compile(r"^\[?(編集|ソースを編集|edit)\]?$")
# 行頭の箇条書き記号
_BULLET = re.compile(r"^[・･\-*+•●○◆▶▼]\s*(.+)$")
# 作品についての記述ではなく、書誌情報が並ぶ節（Wikiの末尾）
_REFERENCE_SECTION = re.compile(r"(^|/ )\s*(出典|注釈|脚注|参考文献|外部リンク|関連項目)\s*$")


def in_reference_section(section: str | None) -> bool:
    return bool(section and _REFERENCE_SECTION.search(section))


# HTMLで見出しと書かれていたことを示す印（textutil.html_to_text が付ける）。
# 印がある行は、長さや句読点を見ずに見出しとして扱う。上限だけは置く。
_MARKED_MAX_LEN = 90


def heading_of(line: str, *, max_len: int = 26) -> str | None:
    """その行が節見出しなら見出し文字列を返す。

    見出しは、そこから先の文が「何についての記述か」を決める。
    文だけを取り出すと「メールの日付から8月16日だと分かります」のように
    主語を失うので、見出しを覚えておいて主張に引き継ぐ。
    """
    if _IMAGE_LINE.match(line):      # 画像の代替テキストは見出しではない
        return None
    m = _HEADING_WRAPPED.match(line) or _HEADING_MD.match(line)
    if m:
        core = _EDIT_LINK.sub("", m.group(1)).strip()
        if not core or len(core) > _MARKED_MAX_LEN or _NOT_A_HEADING.match(core):
            return None
        return core
    if len(line) > max_len or line.endswith(_SENTENCE_END) or re.search(r"[。、．，]", line):
        return None
    core = _LEADING_SYMBOLS.sub("", line).strip()
    if core and len(core) <= max_len and _HAS_JA.search(core) and not _NOT_A_HEADING.match(core):
        return core
    return None


def candidate_sentences(
    text: str,
    *,
    entities: Sequence[str] = (),
    min_len: int = 10,
    max_len: int = 400,
    track_sections: bool = True,
) -> list[dict]:
    """主張候補の文を返す。entities を渡すと、いずれかに触れる文だけに絞る。

    返り値の各要素は
    {"text": 文, "section": 直前の節見出し, "entities": 言及したエンティティ, "offset": 本文中の位置}。
    """
    out: list[dict] = []
    cursor = 0
    section: str | None = None
    chain: list[str] = []
    after_heading = False
    for line in textutil.join_wrapped_lines(text).split("\n"):
        line = line.strip()
        if not line or _EDIT_LINE.match(line):
            continue
        bullet = _BULLET.match(line)
        if track_sections and not bullet:
            head = heading_of(line)
            if head is not None:
                if is_noise(head):
                    continue
                # 見出しが続けて現れる場合は入れ子とみなして繋ぐ
                chain = (chain + [head])[-2:] if after_heading else [head]
                section = " / ".join(chain)
                after_heading = True
                continue
        after_heading = False

        # 節見出しの下にある文は、見出しが文脈を補うので短くても意味を持つ
        # （「おめかしの魔女: 性質は「ご招待」。」）。見出しが無ければ短文は断片のことが多い。
        floor = 6 if section else min_len

        if bullet:
            # 箇条書きは1項目で1つの主張。句点で終わらなくても落とさない
            pieces = [(bullet.group(1).strip(), True)]
        else:
            pieces = [(s, False) for s in textutil.split_sentences(line, min_len=floor)]

        for s, is_item in pieces:
            idx = text.find(s, cursor)
            if idx >= 0:
                cursor = idx + len(s)
            if len(s) < floor or len(s) > max_len or is_noise(s):
                continue
            if not is_item and is_fragment(s):
                continue
            if is_item and (not _HAS_JA.search(s) or _IMAGE_LINE.match(s)):
                continue
            if in_reference_section(section):
                continue
            flat = textutil.flatten(s)
            hit = [e for e in entities if e and (e in s or textutil.flatten(e) in flat)]
            # 節見出しが人物名なら、その節の文はその人物についての記述。
            # 日本語の地の文は主語を繰り返さないので、文だけを見ると
            # 「劇中でその内面や過去が明かされていく」のような、
            # 誰について書かれているかが見出しにしか無い文が丸ごと落ちる。
            flat_section = textutil.flatten(section or "")
            from_section = [
                e for e in entities
                if e and e not in hit and (e in (section or "") or textutil.flatten(e) in flat_section)
            ]
            if entities and not hit and not from_section:
                continue
            out.append({
                "text": s, "section": section, "entities": [*hit, *from_section],
                "entities_in_text": hit,
                "offset": idx if idx >= 0 else None,
            })
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
