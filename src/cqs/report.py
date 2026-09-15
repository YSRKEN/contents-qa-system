"""他AIのDeep Research結果の取り込み。

方針: 報告そのものは根拠にしない。報告から (URL, 主張) の対を取り出して候補に積み、
実際にURLを取得して主張がそのページに書かれているかを照合してから知識層に入れる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import textutil

_URL_RE = re.compile(r"https?://[^\s<>()\[\]「」、。　]+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")
# 参考文献欄: 「[1]: https://...」「[1] https://...」「1. https://...」
_REF_DEF = re.compile(r"^\s*[\[\(]?(\d{1,3})[\]\)]?\s*[:.\-–]?\s*(?:\S.*?\s)?(https?://\S+)\s*$")
# 本文中の参照マーカー: [1] [^1] ※1 （※1）
_REF_MARK = re.compile(r"\[\^?(\d{1,3})\]|[※\*](\d{1,3})")


@dataclass
class ParsedItem:
    claim_text: str
    url: str | None
    note: str | None = None


def _strip_markup(s: str) -> str:
    s = _MD_LINK.sub(r"\1", s)
    s = _URL_RE.sub("", s)
    s = _REF_MARK.sub("", s)
    s = re.sub(r"^\s*[-*+•]\s*", "", s)
    s = re.sub(r"^\s*#{1,6}\s*", "", s)
    s = re.sub(r"\*\*|__|`", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" 　:：-—、。") if s.strip() in ("", "-") else s.strip()


def parse_report(report_text: str) -> list[ParsedItem]:
    """調査報告のテキストから (主張, URL) の対を機械的に取り出す。

    取りこぼしや誤対応は前提。ここでの結果は候補にすぎず、照合を通るまで知識層へは入らない。
    """
    lines = report_text.replace("\r\n", "\n").split("\n")

    # 1) 参考文献欄の定義を集める
    refs: dict[str, str] = {}
    ref_lines: set[int] = set()
    for i, ln in enumerate(lines):
        m = _REF_DEF.match(ln)
        if m:
            refs[m.group(1)] = m.group(2)
            ref_lines.add(i)

    items: list[ParsedItem] = []
    pending_no_url: list[tuple[int, str]] = []

    for i, ln in enumerate(lines):
        if i in ref_lines or not ln.strip():
            continue
        urls = [m.group(2) for m in _MD_LINK.finditer(ln)]
        urls += [u for u in _URL_RE.findall(ln) if u not in urls]
        marks = [g1 or g2 for g1, g2 in _REF_MARK.findall(ln)]
        urls += [refs[m] for m in marks if m in refs and refs[m] not in urls]

        body = _strip_markup(ln)
        # URLだけの行は、直前の主張への出典とみなす
        if not body and urls:
            while pending_no_url:
                _, prev = pending_no_url.pop()
                items.append(ParsedItem(prev, urls[0]))
            continue
        if len(body) < 8:
            continue

        sentences = [s for s in textutil.split_sentences(body, min_len=8)] or [body]
        if urls:
            for s in sentences:
                items.append(ParsedItem(s, urls[0], note=None if len(urls) == 1 else f"他候補URL: {', '.join(urls[1:])}"))
        else:
            for s in sentences:
                pending_no_url.append((i, s))

    # URLに結び付かなかった主張も候補に残す（黙って捨てない）
    for _, s in pending_no_url:
        items.append(ParsedItem(s, None, note="報告内でURLに結び付かなかった主張"))

    # 重複を落とす
    seen: set[tuple[str, str | None]] = set()
    out: list[ParsedItem] = []
    for it in items:
        key = (re.sub(r"\s+", "", it.claim_text), it.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# --- 照合 -------------------------------------------------------------------

# 照合に使う特徴語: 漢字・カタカナ・英数の連なり
_TOKEN_RE = re.compile(r"[一-龥々]{2,}|[ァ-ヴー]{2,}|[A-Za-z0-9]{2,}")


def claim_tokens(claim: str) -> list[str]:
    toks = _TOKEN_RE.findall(textutil.normalize_query(claim))
    # 長い語ほど識別力が高いので、長い順に見る
    return sorted(set(toks), key=len, reverse=True)


def match_score(claim: str, page_text: str) -> dict:
    """主張がページ本文に書かれているかの目安を返す。

    これは「照合の補助」であって判定そのものではない。値が高くても、
    知識層へ入れるかは人またはLLMが本文を見て決める。
    """
    toks = claim_tokens(claim)
    if not toks:
        return {"score": 0.0, "matched": [], "missing": [], "tokens": 0}
    page = textutil.normalize_query(page_text)
    matched = [t for t in toks if t in page]
    missing = [t for t in toks if t not in page]
    return {
        "score": round(len(matched) / len(toks), 3),
        "matched": matched[:20],
        "missing": missing[:20],
        "tokens": len(toks),
    }
