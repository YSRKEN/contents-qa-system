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
        # 表は行と列の対応が意味を持つ。セルを行ごとに集めてから1行のテキストに畳む。
        self._table_depth = 0
        self._rows: list[tuple[bool, list[str]]] = []
        self._row: list[str] | None = None
        self._row_is_header = True
        self._cell: list[str] | None = None
        self._caption: list[str] | None = None

    # --- 表 ---
    def _start_table(self) -> None:
        self._table_depth += 1
        if self._table_depth == 1:
            self._rows, self._row, self._cell, self._caption = [], None, None, None

    def _end_table(self) -> None:
        if self._table_depth == 0:
            return
        self._table_depth -= 1
        if self._table_depth == 0:
            self.parts.append("\n" + render_table(self._caption_text(), self._rows) + "\n")
            self._rows, self._row, self._cell, self._caption = [], None, None, None

    def _caption_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._caption or [])).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return
        if tag == "table":
            self._start_table()
            return
        if self._table_depth:
            if tag == "caption":
                self._caption = []
            elif tag == "tr":
                self._row, self._row_is_header = [], True
            elif tag in ("td", "th"):
                self._cell = []
                if tag == "td":
                    self._row_is_header = False
            elif tag == "br" and self._cell is not None:
                self._cell.append(" ")
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
        if tag == "table":
            self._end_table()
            return
        if self._table_depth:
            if tag == "caption":
                pass
            elif tag in ("td", "th") and self._cell is not None:
                if self._row is None:
                    self._row = []
                self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
                self._cell = None
            elif tag == "tr" and self._row is not None:
                self._rows.append((self._row_is_header, self._row))
                self._row = None
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
        if self._table_depth:
            if self._cell is not None:
                self._cell.append(data)
            elif self._caption is not None:
                self._caption.append(data)
            return
        if data.strip():
            self.parts.append(data)


def render_table(caption: str, rows: list[tuple[bool, list[str]]]) -> str:
    """表を、行ごとに1行のテキストへ畳む。

    セルを縦に並べただけでは行と列の対応が失われ、「5位」「14.81」といった
    値だけが本文に散らばる。見出し行があれば「見出し: 値」の対で書き出す。
    """
    body = [cells for is_header, cells in rows if any(c.strip() for c in cells)]
    if not body:
        return ""
    header: list[str] | None = None
    if rows and rows[0][0]:                       # 先頭行がすべて見出しセルなら列名として使う
        candidate = rows[0][1]
        if len(candidate) >= 2 and any(c.strip() for c in candidate):
            header = candidate
            body = body[1:]
    lines: list[str] = []
    if caption:
        lines.append(f"【表: {caption}】")
    for cells in body:
        cells = [c.strip() for c in cells]
        if header and len(cells) == len(header):
            pairs = [f"{h.strip()}: {c}" for h, c in zip(header, cells) if c]
        else:
            pairs = [c for c in cells if c]
        if pairs:
            lines.append("・" + " / ".join(pairs))
    return "\n".join(lines)


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
# 終止符の直後にこれらが続く場合、そこは文の切れ目ではない
# （例:「プロゲーマーとFPS？でガチンコ対決し」）
_CONTINUATIONS = "でとがをにはのもやへ」』）)、，"


def join_wrapped_lines(text: str) -> str:
    """読点で終わる行を次の行と繋ぐ。

    HTMLの <br> で折り返された1文が、行ごとに切れて断片になるのを防ぐ。
    箇条書きやメニューの行は読点で終わらないので巻き込まない。
    """
    lines = text.split("\n")
    out: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        # 箇条書きや見出しで始まる行は、前の行の続きではない
        starts_block = bool(re.match(r"^[・･\-*+•●○◆▶▼<＜【〔#]", stripped))
        if out and out[-1].endswith(("、", "，")) and stripped and not starts_block:
            out[-1] += stripped
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
        for i, ch in enumerate(line):
            buf.append(ch)
            if ch in _BRACKETS:
                stack.append(_BRACKETS[ch])
            elif stack and ch == stack[-1]:
                stack.pop()
            elif ch in _TERMINATORS and not stack:
                nxt = line[i + 1] if i + 1 < len(line) else ""
                if nxt in _CONTINUATIONS:
                    continue
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
