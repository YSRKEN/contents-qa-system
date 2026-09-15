"""HTML→本文テキスト変換、検索クエリ整形、文分割。

原文層は「不変」なので、ここで行うのは保存前の一度きりの整形だけにする。
検索のための正規化は、保存済みテキストを書き換えずクエリ側で吸収する。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from html.parser import HTMLParser
from typing import Sequence

# 本文として扱わない要素
_DROP_TAGS = {
    "script", "style", "noscript", "template", "svg", "canvas",
    "iframe", "head", "nav", "footer", "form", "select", "button",
}
# 前後に改行を入れる要素
_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "main", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "td", "th",
    "dt", "dd", "blockquote", "pre", "figcaption", "table", "ul", "ol", "dl",
}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self._drop_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            if a.get("property") == "og:title" and not self.title:
                self.title = a["content"].strip() if a.get("content") else None
        if tag == "img":
            a = {k.lower(): (v or "") for k, v in attrs}
            alt = a.get("alt", "").strip()
            if alt:
                self.parts.append(f"[画像: {alt}]\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data.strip()
            return
        if self._drop_depth:
            return
        if data.strip():
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str | None]:
    """HTML文字列を (本文テキスト, タイトル) に変換する。"""
    p = _Extractor()
    try:
        p.feed(html)
        p.close()
    except Exception:  # 壊れたHTMLでもそこまでの結果を使う
        pass
    text = "".join(p.parts)
    return clean_text(text), (p.title.strip() if p.title else None)


def clean_text(text: str) -> str:
    """行内の空白を畳み、空行の連続を2行までに抑える。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank <= 1 and out:
                out.append("")
    return "\n".join(out).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 括弧の対応。作品名が「超かぐや姫！」のように終止符を含むため、
# 括弧の内側にある。！？では文を切らない。
_BRACKETS = {"「": "」", "『": "』", "（": "）", "(": ")", "〈": "〉", "《": "》", "【": "】", "“": "”"}
_TERMINATORS = "。！？!?"


def join_wrapped_lines(text: str) -> str:
    """読点で終わる行を次の行と繋ぐ。

    HTMLの <br> で折り返された1文が、行ごとに切れて断片になるのを防ぐ。
    箇条書きやメニューの行は読点で終わらないので巻き込まない。
    """
    lines = text.split("\n")
    out: list[str] = []
    for ln in lines:
        if out and out[-1].endswith(("、", "，", "，")):
            out[-1] += ln.strip()
        else:
            out.append(ln)
    return "\n".join(out)


def split_sentences(text: str, *, min_len: int = 6) -> list[str]:
    """本文を文に割る。主張候補の単位として使う。

    括弧の内側にある終止符では切らない。作品名『超かぐや姫！』のように
    タイトル自体が「！」を含む場合、単純に切ると断片だらけになるため。
    """
    out: list[str] = []
    for line in join_wrapped_lines(text).split("\n"):
        line = line.strip()
        if not line:
            continue
        buf: list[str] = []
        stack: list[str] = []
        for ch in line:
            buf.append(ch)
            if ch in _BRACKETS:
                stack.append(_BRACKETS[ch])
            elif stack and ch == stack[-1]:
                stack.pop()
            elif ch in _TERMINATORS and not stack:
                s = "".join(buf).strip()
                if len(s) >= min_len:
                    out.append(s)
                buf = []
        rest = "".join(buf).strip()
        if len(rest) >= min_len:
            out.append(rest)
    return out


# --- 検索クエリ -------------------------------------------------------------

# FTS5 の trigram トークナイザは3文字未満を索引化しない。
MIN_TRIGRAM = 3


def normalize_query(q: str) -> str:
    """全角/半角ゆれを吸収する。クエリ側だけに適用し、保存済み本文は触らない。"""
    return unicodedata.normalize("NFKC", q).strip()


def query_terms(q: str) -> list[str]:
    """クエリを語に割る。鉤括弧・引用符で囲まれた部分は1語として扱う。"""
    q = normalize_query(q)
    terms: list[str] = []
    for m in re.finditer(r'"([^"]+)"|「([^」]+)」|(\S+)', q):
        term = m.group(1) or m.group(2) or m.group(3)
        term = term.strip()
        if term:
            terms.append(term)
    return terms


def split_terms(q: str) -> tuple[list[str], list[str]]:
    """クエリの語を「索引で引ける語（3文字以上）」と「引けない語」に分ける。

    日本語の人名・用語には2文字のものが多い（例: 彩葉、芦花）。
    trigram はそれらを索引化しないので、短い語は LIKE で併せて絞り込む。
    """
    long_terms, short_terms = [], []
    for t in query_terms(q):
        (long_terms if len(t) >= MIN_TRIGRAM else short_terms).append(t)
    return long_terms, short_terms


def fts_match_expression(q: str) -> str | None:
    """FTS5 の MATCH 式を組み立てる。索引で引ける語が無ければ None を返す。

    trigram では各語をフレーズとして与える必要がある（語境界の概念がないため）。
    """
    long_terms, _ = split_terms(q)
    return match_expression_for(long_terms)


def match_expression_for(terms: Sequence[str]) -> str | None:
    if not terms:
        return None
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)


def like_pattern(term: str) -> str:
    esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def like_patterns(q: str) -> list[str]:
    """LIKE フォールバック用のパターン（全語ぶん）。"""
    return [like_pattern(t) for t in query_terms(q)]


def excerpt(text: str, needle: str, *, width: int = 240) -> str:
    """text の中から needle の周辺を切り出す。見つからなければ先頭を返す。"""
    if not text:
        return ""
    idx = -1
    for t in query_terms(needle):
        idx = text.find(t)
        if idx >= 0:
            break
    if idx < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, idx - width // 3)
    end = min(len(text), start + width)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
