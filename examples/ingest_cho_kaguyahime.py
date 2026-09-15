#!/usr/bin/env python3
"""作品『超かぐや姫！』のデータ投入スクリプト（作品固有の取り込み例）。

このリポジトリのコード本体は作品に依存しない。作品ごとの事情
（どのURLを見るか、公式ページのどこがキャラクター欄か）は、
このような使い捨てのスクリプトに寄せ、DBファイル側に結果だけを残す。

    python examples/ingest_cho_kaguyahime.py           # 取得して投入
    python examples/ingest_cho_kaguyahime.py --offline # 取得済みDBに対して抽出だけやり直す

取得した本文スナップショットは data/<slug>.db に入る。公開リポジトリには含めない。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cqs import config, ingest, textutil  # noqa: E402
from cqs.store import StoreError  # noqa: E402

SLUG = "cho-kaguyahime"
TITLE = "超かぐや姫！"
NOTE = "Netflix映画。2026年1月22日より世界独占配信。監督: 山下清悟。"

# URLから出典種別を決めるルール（作品ごとにDBへ保存される）
KIND_RULES = [
    ("cho-kaguyahime.com", "official_site"),
    ("netflix.com", "official_site"),
    ("mantan-web.jp", "interview"),
    ("ananweb.jp", "interview"),
    ("animatetimes.com", "interview"),
    ("gamer.ne.jp", "interview"),
    ("buzzfeed.com", "interview"),
    ("colorido.co.jp", "article"),
    ("eiga.com", "article"),
    ("nijimen", "article"),
    ("twinengine.jp", "article"),
    ("denfaminicogamer.jp", "article"),
    ("natalie.mu", "article"),
    ("kadokawa.co.jp", "official_site"),      # 版元の商品ページ（小説・漫画・公式ガイドブック）
    ("realsound.jp", "article"),
    ("moguravr.com", "article"),
    ("animeanime.jp", "article"),
    ("ciatr.jp", "article"),
    ("purunlife.com", "fan_note"),
    ("midnight-sweets.com", "fan_note"),
    ("tyerukun.com", "fan_note"),
    # 本編の読み取り（日付・地理・命名）を扱うので「解釈」ではなく「伝聞」側に置く
    ("yuno-phar.hatenablog.com", "fan_chronicle"),
    # 公式アカウントはホスト名ではなくアカウント名まで指定する
    ("x.com/Cho_KaguyaHime", "official_sns"),
    ("x.com/studiochromato", "official_sns"),
    ("x.com/takacho_01", "fan_chronicle"),
]

URLS = [
    # 公式（再取得対象）
    "https://www.cho-kaguyahime.com/",
    "https://www.cho-kaguyahime.com/news/",
    "https://www.cho-kaguyahime.com/music/",
    "https://www.cho-kaguyahime.com/movie/",
    "https://www.cho-kaguyahime.com/theater/",
    # インタビュー・記事（1回取得で確定）
    "https://mantan-web.jp/article/20260124dog00m200003000c.html",
    "https://mantan-web.jp/article/20260129dog00m200001000c.html",
    "https://ananweb.jp/categories/entertainment/93795",
    "https://www.animatetimes.com/news/details.php?id=1768959910",
    "https://www.buzzfeed.com/jp/munenoriumeki/cho-kaguyahime-interview",
    "https://eiga.com/movie/105011/",
    "https://www.gamer.ne.jp/news/202602190031/",
    "https://nijimen.kusuguru.co.jp/topics/600120",
    "https://news.denfaminicogamer.jp/news/2606192x",
    # Wiki（探索用インデックス。主張の根拠にはしない）
    "https://ja.wikipedia.org/wiki/%E8%B6%85%E3%81%8B%E3%81%90%E3%82%84%E5%A7%AB!",
    # 公式SNS（FxTwitter 経由。タイムラインは辿れないので投稿URLを個別に指定する）
    "https://x.com/Cho_KaguyaHime",
    "https://x.com/Cho_KaguyaHime/status/2077362794022785400",
    "https://x.com/Cho_KaguyaHime/status/2046968215088369935",
    "https://x.com/Cho_KaguyaHime/status/2063123720097468464",
    "https://x.com/Cho_KaguyaHime/status/2019380735870697734",
    "https://x.com/Cho_KaguyaHime/status/2033831030550106371",
    # 版元の商品ページ（小説版・漫画版・公式ガイドブック）
    "https://www.kadokawa.co.jp/product/322509000503/",
    "https://www.kadokawa.co.jp/product/322509000193/",
    "https://www.kadokawa.co.jp/product/322510000048/",
    # 本編に踏み込んだ記事・考察
    "https://ciatr.jp/topics/335940",
    "https://realsound.jp/movie/2026/02/post-2297135.html",
    "https://realsound.jp/movie/2026/01/post-2269728_2.html",
    "https://s.animeanime.jp/article/2026/02/21/96219.html",
    "https://www.moguravr.com/vrchat-kaguya-hime-live-report/",
    # 本編の読み取り（時系列・地理・命名）
    "https://yuno-phar.hatenablog.com/entry/2026/05/19/203241",
    "https://yuno-phar.hatenablog.com/entry/2026/04/19/140307",
    "https://yuno-phar.hatenablog.com/entry/2026/05/07/095608",
    "https://yuno-phar.hatenablog.com/entry/2026/05/11/133355",
    "https://x.com/takacho_01/status/2029166435440967784",
    # 考察note（解釈として扱う）
    "https://note.com/tubu78/n/n60b7c170496c",
    "http://purunlife.com/cho-kaguya-hime-spoiler-explanation/",
    "https://midnight-sweets.com/entry/cho-kaguyahime-explanation",
    "https://tyerukun.com/tyokaguyahime-last-kousatu/",
    # 感想note（ファン解釈として扱う）
    "https://note.com/zenjituloku/n/n705f05631a44",
    "https://note.com/kohatazuke/n/ne448c35ab2f9",
    "https://note.com/tony72/n/n26e355172b11",
    "https://note.com/fuchikado/n/n41115b82b822",
    "https://note.com/tandz/n/n414744b5a4b3",
]

# 名前・別名。別名は本文中の表記ゆれを正規名へ寄せるために使う。
ENTITIES: list[tuple[str, str, list[str]]] = [
    ("かぐや", "character", ["かぐや姫", "KAGUYA"]),
    ("酒寄彩葉", "character", ["彩葉", "IROHA SAKAYORI"]),
    ("月見ヤチヨ", "character", ["ヤチヨ", "YACHIYO RUNAMI"]),
    ("帝アキラ", "character", ["アキラ", "AKIRA MIKADO"]),
    ("駒沢雷", "character", ["RAI KOMAZAWA"]),
    ("駒沢乃依", "character", ["乃依", "NOI KOMAZAWA"]),
    ("綾紬芦花", "character", ["芦花", "ROKA AYATSUMUGI"]),
    ("諌山真実", "character", ["MAMI ISAYAMA"]),
    ("FUSHI", "character", []),
    ("忠犬オタ公", "character", ["オタ公", "OTAKO CHUKEN"]),
    ("乙事照琴", "character", ["照琴", "KOTO OKKOTERU"]),
    ("犬DOGE", "character", ["ドウジ", "INUDOGE"]),
    ("ツクヨミ", "term", []),
    ("ブラックオニキス", "term", []),
    ("山下清悟", "person", []),
    ("スタジオコロリド", "organization", []),
    ("スタジオクロマト", "organization", []),
    ("超かぐや姫！", "work", ["超かぐや姫", "超かぐや姫!", "CosmicPrincessKaguya"]),
    ("酒寄朝久", "character", ["朝久"]),
    ("酒寄紅葉", "character", ["紅葉"]),
    ("月人", "term", ["つきじん"]),
    ("KASSEN", "term", ["神戦"]),
    ("ヤチヨカップ", "term", ["YachiyoCUP"]),
    ("スマートコンタクト", "term", ["スマコン"]),
    ("ふじゅ～", "term", ["ふじゅ〜Pay", "ふじゅ～Pay"]),
    ("もと光る竹", "term", []),
    ("立川市", "place", []),
    ("夏生さえり", "person", []),
    ("コーニッシュ", "person", []),
    ("桐山なると", "person", []),
    ("米田タロウ", "person", []),
]

# 公式サイトの登場人物欄: [画像: 名前] → 英字表記 → CV → PROFILE → プロフィール本文
CHARACTER_BLOCK = re.compile(
    r"\[画像: (?P<name>[^\]]+)\]\s*\n\s*(?P<romaji>[A-Za-z0-9 ()\.]+)\s*\n\s*CV(?P<cv>[^\n]+)\n\s*PROFILE\s*\n"
    r"(?P<profile>.+?)(?=\n\s*\[画像: |\n\s*PREV|\n\s*STAFF)",
    re.S,
)


def surface_map(store) -> dict[str, str]:
    m: dict[str, str] = {}
    for e in store.list_entities():
        m[e["name"]] = e["name"]
        for a in e.get("aliases", ()):
            m.setdefault(a, e["name"])
    return m


def ingest_character_section(store, source_version_id: int) -> int:
    """公式サイトの登場人物欄から、キャラクターに正しく紐づけた主張を作る。

    文単位の自動抽出だと「彩葉のことが大好き。」がどのキャラクターの記述か分からない。
    節の構造が分かっている出典では、こうして帰属を付けたほうが知識層の質が上がる。
    """
    v = store.source_excerpt(source_version_id, offset=0, length=10**7)
    text = v["excerpt"]
    surfaces = surface_map(store)
    n = 0
    for m in CHARACTER_BLOCK.finditer(text):
        name = m.group("name").strip()
        canonical = surfaces.get(name) or surfaces.get(name.split("(")[0]) or name
        cv, romaji = m.group("cv").strip(), m.group("romaji").strip()
        store.add_claim(
            text=f"{canonical}の声優は{cv}である。",
            source_version_id=source_version_id, entities=[canonical],
            locator=f"[画像: {name}] {romaji} CV{cv}", note="公式サイトの登場人物欄より",
        )
        store.add_claim(
            text=f"{canonical}の英字表記は {romaji} である。",
            source_version_id=source_version_id, entities=[canonical], locator=romaji,
        )
        n += 2
        for s in textutil.split_sentences(m.group("profile").strip(), min_len=6):
            others = sorted({c for k, c in surfaces.items() if k in s} | {canonical})
            store.add_claim(
                text=f"{canonical}: {s}", source_version_id=source_version_id,
                entities=others, locator=s, note="公式サイトの登場人物プロフィールより",
            )
            n += 1
    return n


def ingest_story_section(store, source_version_id: int) -> int:
    """イントロダクション〜あらすじ（登場人物欄より前）の文を主張にする。"""
    v = store.source_excerpt(source_version_id, offset=0, length=10**7)
    text = v["excerpt"]
    head = text[: text.find("[画像: ")] if "[画像: " in text else text
    surfaces = surface_map(store)
    n = 0
    from cqs import extract

    for c in extract.dedupe(extract.candidate_sentences(head, entities=list(surfaces))):
        ents = sorted({surfaces[s] for s in c["entities"]})
        if not ents:
            continue
        store.add_claim(
            text=c["text"], source_version_id=source_version_id,
            entities=ents, locator=c["text"], note="公式サイトのイントロダクション・あらすじより",
        )
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="URL取得を行わず、取得済みの版から抽出だけやり直す")
    ap.add_argument("--no-robots", action="store_true", help="robots.txt を確認せずに取得する")
    args = ap.parse_args()

    path = config.work_path(SLUG)
    if path.exists():
        store = config.open_work(SLUG)
        print(f"既存のDBを使います: {path}")
    else:
        store = config.create_work(TITLE, slug=SLUG, note=NOTE)
        print(f"作成しました: {path}")

    with store:
        for pattern, kind in KIND_RULES:
            store.set_kind_rule(pattern, kind)
        for name, kind, aliases in ENTITIES:
            store.ensure_entity(name, kind=kind, aliases=aliases)
        print(f"エンティティ {len(ENTITIES)} 件を登録しました")

        official_top = None
        secondary: list[tuple[int, str]] = []
        if args.offline:
            for s in store.list_sources():
                v = store.latest_version(s["id"])
                if not v:
                    continue
                if s["url"] == URLS[0]:
                    official_top = int(v["id"])
                elif s["kind"] in ("interview", "article", "fan_note", "fan_chronicle", "official_sns"):
                    secondary.append((int(v["id"]), s["kind"]))
        else:
            for url in URLS:
                try:
                    r = ingest.ingest_url(store, url, respect_robots=not args.no_robots)
                except Exception as e:
                    print(f"  × {url}: {e}")
                    continue
                print(f"  ○ [{r['kind_label']}] {r['text_length']:>6}字 v{r['version_no']} {url}")
                if url == URLS[0]:
                    official_top = r["source_version_id"]
                elif r["kind"] in ("interview", "article", "fan_note", "fan_chronicle", "official_sns"):
                    secondary.append((r["source_version_id"], r["kind"]))

        if official_top is None:
            print("公式トップページを取得できていないため、知識層の構築を中止します。")
            return 1

        if store.search_claims(limit=1):
            print("主張がすでに登録されているため、抽出はスキップします。")
        else:
            n = ingest_character_section(store, official_top)
            print(f"登場人物欄から {n} 件の主張を登録しました")
            n = ingest_story_section(store, official_top)
            print(f"イントロダクション・あらすじから {n} 件の主張を登録しました")
            total = 0
            for svid, kind in secondary:
                try:
                    # 公式SNSの告知は人物名が出ないことが多いので、エンティティ必須を外す
                    total += len(
                        ingest.register_proposed(
                            # 公式SNSの告知と時系列の記述は人物名が出ないことが多い
                            store, svid, limit=250,
                            require_entity=kind not in ("official_sns", "fan_chronicle"),
                        )
                    )
                except StoreError as e:
                    print(f"  × source_version {svid}: {e}")
            print(f"二次情報・公式SNSから {total} 件の主張を登録しました")

        s = store.stats()
        print(
            f"\n{s['title']}: 出典 {s['sources']} / 版 {s['versions']} / "
            f"主張 {s['claims_active']} / エンティティ {s['entities']}"
        )
        print("  確認状態: " + ", ".join(f"{k} {v}" for k, v in s["claims_by_verification"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
