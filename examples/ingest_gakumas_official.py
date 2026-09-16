#!/usr/bin/env python3
"""作品『学園アイドルマスター』の公式サイト取り込み（作品固有の取り込み例）。

公式サイトの学園名簿は、プロフィールが「見出し／値」の対で組まれている。
文単位の自動抽出に任せると「15歳」「AB型」が誰の記述か分からなくなるので、
HTMLの構造を直接読んで、人物に紐づけた主張を作る。

    python examples/ingest_gakumas_official.py            # 取得して投入
    python examples/ingest_gakumas_official.py --offline  # 取得済みの版から抽出だけやり直す

取得した本文スナップショットは data/<slug>.db に入る。公開リポジトリには含めない。
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cqs import config, ingest, textutil  # noqa: E402

SLUG = "gakuen-idolmaster"
TITLE = "学園アイドルマスター"
NOTE = "バンダイナムコエンターテインメント／QualiArts。2024年5月16日配信のアイドル育成シミュレーション。"

BASE = "https://gakuen.idolmaster-official.jp"

# 学園名簿のURL断片。花海咲季だけは /idol/ 自身が本人のページ（data-current="saki"）。
IDOL_PATHS = [
    ("/idol/", "花海咲季"),
    ("/idol/temari/", "月村手毬"),
    ("/idol/kotone/", "藤田ことね"),
    ("/idol/mao/", "有村麻央"),
    ("/idol/lilja/", "葛城リーリヤ"),
    ("/idol/china/", "倉本千奈"),
    ("/idol/sumika/", "紫雲清夏"),
    ("/idol/hiro/", "篠澤広"),
    ("/idol/rinami/", "姫崎莉波"),
    ("/idol/ume/", "花海佑芽"),
    ("/idol/misuzu/", "秦谷美鈴"),
    ("/idol/sena/", "十王星南"),
    ("/idol/tsubame/", "雨夜燕"),
    ("/idol/kunio/", "十王邦夫"),
    ("/idol/asari/", "根緒亜紗里"),
]

# 学園名簿以外の公式ページ（自動抽出に回す）
OTHER_PATHS = ["/", "/introduction/", "/media/"]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _one(raw: str, cls: str) -> str | None:
    m = re.search(r'class="[^"]*\b' + cls + r'\b[^"]*"[^>]*>(.*?)</', raw, re.S)
    return _text(m.group(1)) if m else None


def _profile_pairs(raw: str) -> list[tuple[str, str]]:
    """<p class="idol-info__data-head">項目</p><p class="...__data-text">値</p> の対を順に拾う。"""
    pat = re.compile(
        r'class="[^"]*idol-info__data-head[^"]*"[^>]*>(.*?)</p>\s*'
        r'<p[^>]*class="[^"]*idol-info__data-text[^"]*"[^>]*>(.*?)</p>',
        re.S,
    )
    out = []
    for head, val in pat.findall(raw):
        h, v = _text(head), _text(val)
        if h and v:
            out.append((h, v))
    return out


class Locator:
    """原文（版の本文）の中から、空白の違いを無視して位置を探す。

    主張の本文は組み立てたものなので原文には現れない。位置（offset）は
    原文での並び順に使うため、locator ではなく抽出元の断片から求める。
    """

    def __init__(self, text: str) -> None:
        self.text = text
        norm, index = [], []
        for i, ch in enumerate(text):
            if ch.isspace():
                if norm and norm[-1] == " ":
                    continue
                norm.append(" ")
            else:
                norm.append(ch)
            index.append(i)
        self.norm = "".join(norm)
        self.index = index
        self.cursor = 0

    def find(self, fragment: str) -> int | None:
        needle = re.sub(r"\s+", " ", fragment).strip()
        if not needle:
            return None
        at = self.norm.find(needle, self.cursor)
        if at < 0:
            at = self.norm.find(needle)
        if at < 0:
            return None
        self.cursor = at + len(needle)
        return self.index[at]


def surface_map(store) -> dict[str, str]:
    m: dict[str, str] = {}
    for e in store.list_entities():
        m[e["name"]] = e["name"]
        for a in e.get("aliases", ()):
            m.setdefault(a, e["name"])
    return m


def ingest_idol_page(store, version_id: int, canonical: str) -> int:
    """学園名簿の1ページから、その人物に帰属させた主張を作る。"""
    raw = store.get_raw_html(version_id)
    if not raw:
        return 0
    n = 0
    surfaces = surface_map(store)
    loc = Locator(store.get_version(version_id)["text"])

    romaji = _one(raw, "idol-info__en") or _one(raw, "idol-info__eng")
    if romaji:
        store.add_claim(
            text=f"{canonical}の英字表記は {romaji} である。",
            source_version_id=version_id, entities=[canonical], locator=romaji,
            offset=loc.find(romaji), note="公式サイトの学園名簿より",
        )
        n += 1

    cv = _one(raw, "idol-info__cv")
    if cv:
        cv_name = re.sub(r"^CV[：:]\s*", "", cv).strip()
        if cv_name:
            store.ensure_entity(cv_name, kind="person", note="声優")
            store.add_claim(
                text=f"{canonical}の声優は{cv_name}である。",
                source_version_id=version_id, entities=[canonical, cv_name], locator=cv,
                offset=loc.find(cv), note="公式サイトの学園名簿より",
            )
            n += 1

    for head, val in _profile_pairs(raw):
        store.add_claim(
            text=f"{canonical}の{head}は{val}である。",
            source_version_id=version_id, entities=[canonical], locator=f"{head}: {val}",
            offset=loc.find(head), note="公式サイトの学園名簿（プロフィール）より",
        )
        n += 1

    serif = _one(raw, "idol__serif-text")
    if serif:
        store.add_claim(
            text=f"{canonical}の公式サイト掲載の台詞:「{serif}」",
            source_version_id=version_id, entities=[canonical], locator=serif,
            offset=loc.find(serif), note="公式サイトの学園名簿（トップの台詞）より",
        )
        n += 1

    desc = _one(raw, "idol-info__text")
    if desc:
        for s in textutil.split_sentences(desc, min_len=6):
            others = sorted({c for k, c in surfaces.items() if k in s} | {canonical})
            store.add_claim(
                text=f"{canonical}: {s}",
                source_version_id=version_id, entities=others, locator=s,
                offset=loc.find(s), note="公式サイトの学園名簿（紹介文）より",
            )
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="URL取得を行わず、取得済みの版から抽出だけやり直す")
    ap.add_argument("--robots", action="store_true", help="robots.txt を確認して取得する")
    args = ap.parse_args()

    path = config.work_path(SLUG)
    store = config.open_work(SLUG) if path.exists() else config.create_work(TITLE, slug=SLUG, note=NOTE)
    print(f"DB: {path}")

    with store:
        by_url = {}
        if args.offline:
            for s in store.list_sources():
                v = store.latest_version(s["id"])
                if v:
                    by_url[s["url"]] = int(v["id"])
        else:
            for p in OTHER_PATHS + [p for p, _ in IDOL_PATHS]:
                url = BASE + p
                try:
                    r = ingest.ingest_url(store, url, respect_robots=args.robots)
                except Exception as e:
                    print(f"  × {url}: {e}")
                    continue
                print(f"  ○ {r['text_length']:>5}字 v{r['version_no']} {url}")
                by_url[url] = r["source_version_id"]

        total = 0
        for p, canonical in IDOL_PATHS:
            vid = by_url.get(BASE + p)
            if not vid:
                print(f"  × 版が無いので飛ばします: {p}")
                continue
            k = ingest_idol_page(store, vid, canonical)
            print(f"  {canonical}: {k} 件")
            total += k
        print(f"学園名簿から {total} 件の主張を登録しました")

        n = 0
        for p in OTHER_PATHS:
            vid = by_url.get(BASE + p)
            if vid:
                n += len(ingest.register_proposed(store, vid, limit=250, require_entity=False))
        print(f"その他の公式ページから {n} 件の主張を登録しました")

        s = store.stats()
        print(f"\n{s['title']}: 出典 {s['sources']} / 版 {s['versions']} / "
              f"主張 {s['claims_active']} / エンティティ {s['entities']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
