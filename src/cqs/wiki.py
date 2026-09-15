"""Wikiの脚注を扱う。

設計上、Wikiの第一の役割は「出典欄から原資料を辿るための索引」だが、
出典が書籍しかない記述は辿る先がネット上に無い。そこでWikiの記述も伝聞として
知識層に入れられるようにし、代わりに**どの脚注に基づく記述か**を主張に残す。
"""

from __future__ import annotations

import html as html_mod
import re
import urllib.parse
from typing import Iterable

from .store import WorkStore

# MediaWiki (Parsoid) の脚注: <li about="#cite_note-3" id="cite_note-3" data-mw-footnote-number="1" ...>
_LI = re.compile(
    r'<li\b[^>]*id="cite_note-[^"]*"[^>]*data-mw-footnote-number="(?P<no>\d+)"[^>]*>(?P<body>.*?)</li>',
    re.S,
)
_GROUP = re.compile(r'data-mw-group="([^"]*)"')
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href="(https?://[^"]+)"')
_BACKLINK = re.compile(r'<span class="mw-cite-backlink".*?</span>', re.S)
_MARKER = re.compile(r"\[(\d{1,3})\]")

_SKIP_HOSTS = ("wikipedia.org", "wikimedia.org", "wikidata.org", "mediawiki.org")


def _clean(fragment: str) -> str:
    fragment = _BACKLINK.sub("", fragment)
    text = html_mod.unescape(_TAG.sub(" ", fragment))
    return re.sub(r"\s+", " ", text).strip(" ↑")


def parse_citations(raw_html: str) -> dict[str, dict]:
    """脚注番号 → {text, urls} を返す。

    注釈グループ（data-mw-group="注"）は本文中で [注 1] と表示され、
    出典グループとは番号が別系統なので分けて扱う。ここでは出典グループだけを返す。
    """
    out: dict[str, dict] = {}
    for m in _LI.finditer(raw_html):
        body = m.group("body")
        group = _GROUP.search(m.group(0))
        if group and group.group(1):        # 注釈グループは対象外
            continue
        urls = []
        for u in _HREF.findall(body):
            u = html_mod.unescape(u)
            host = urllib.parse.urlsplit(u).netloc
            if any(s in host for s in _SKIP_HOSTS):
                continue
            if "web.archive.org" in host:
                tail = re.search(r"(https?://(?!web\.archive)\S+)$", u)
                if tail:
                    u = urllib.parse.unquote(tail.group(1))
            if u not in urls:
                urls.append(u)
        out[m.group("no")] = {"text": _clean(body)[:400], "urls": urls}
    return out


def reference_urls(raw_html: str) -> list[str]:
    """出典欄にある外部URLを、重複を除いて出現順に返す（原資料を辿るための入口）。"""
    seen: set[str] = set()
    urls: list[str] = []
    for c in parse_citations(raw_html).values():
        for u in c["urls"]:
            key = u.split("?")[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            urls.append(u)
    return urls


def annotate_claims(store: WorkStore, source_version_id: int) -> dict:
    """ある版から抽出した主張に、本文中の [N] が指す脚注を書き添える。

    URLのある脚注は「この主張は本来どの原資料に当たるべきか」を示す。
    URLの無い脚注（書籍のみ）は、ネット上に辿る先が無いことを示す。
    """
    raw = store.get_raw_html(source_version_id)
    if not raw:
        return {"error": "生HTMLが保存されていません", "annotated": 0}
    cites = parse_citations(raw)
    rows = store.conn.execute(
        "SELECT id, text, note FROM claims WHERE source_version_id = ?", (source_version_id,)
    ).fetchall()

    annotated = 0
    with_url = 0
    book_only = 0
    for r in rows:
        nums = [n for n in dict.fromkeys(_MARKER.findall(r["text"])) if n in cites]
        if not nums:
            continue
        parts = []
        for n in nums:
            c = cites[n]
            parts.append(f"[{n}] {c['text']}" + (f" → {c['urls'][0]}" if c["urls"] else " （URLなし）"))
            if c["urls"]:
                with_url += 1
            else:
                book_only += 1
        note = (r["note"] or "").split(" / 脚注:")[0]
        store.conn.execute(
            "UPDATE claims SET note = ?, updated_at = datetime('now') WHERE id = ?",
            ((note + " / 脚注: " + " ; ".join(parts)).strip(" /"), r["id"]),
        )
        annotated += 1
    store.conn.commit()
    store.log("wiki_annotate", {"source_version_id": source_version_id, "annotated": annotated})
    return {"citations": len(cites), "annotated": annotated,
            "markers_with_url": with_url, "markers_book_only": book_only}


def pending_reference_urls(store: WorkStore, source_version_id: int) -> list[str]:
    """脚注のURLのうち、まだ原文層に取り込んでいないものを返す。"""
    raw = store.get_raw_html(source_version_id)
    if not raw:
        return []
    known = {
        (r["url"] or "").split("?")[0].rstrip("/")
        for r in store.conn.execute("SELECT url FROM sources WHERE url IS NOT NULL")
    }
    return [u for u in reference_urls(raw) if u.split("?")[0].rstrip("/") not in known]


def fetch_references(
    store: WorkStore, source_version_id: int, *, limit: int = 20, urls: Iterable[str] | None = None
) -> list[dict]:
    """脚注のリンク先を原資料として取り込む（設計上のWikiの本来の使い方）。"""
    from . import ingest

    targets = list(urls) if urls is not None else pending_reference_urls(store, source_version_id)[:limit]
    out = []
    for u in targets:
        try:
            out.append(ingest.ingest_url(store, u))
        except Exception as e:
            out.append({"url": u, "error": str(e)})
    return out
