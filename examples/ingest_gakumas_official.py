#!/usr/bin/env python3
"""作品『学園アイドルマスター』の取り込み（作品固有の取り込み例）。

このリポジトリのコード本体は作品に依存しない。作品ごとの事情
（どのURLを見るか、公式ページのどこが人物欄か）はこのスクリプトに寄せ、
DBファイル側には結果だけを残す。DBは公開リポジトリに含めないので、
作り直せる状態をここに残しておく。

公式サイトの学園名簿は、プロフィールが「見出し／値」の対で組まれている。
文単位の自動抽出に任せると「15歳」「AB型」が誰の記述か分からなくなるので、
HTMLの構造を直接読んで、人物に紐づけた主張を作る。

    python examples/ingest_gakumas_official.py            # 取得して投入
    python examples/ingest_gakumas_official.py --offline  # 取得済みの版から抽出だけやり直す
    python examples/ingest_gakumas_official.py --no-refs  # Wikipediaの脚注を辿らない
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cqs import config, ingest, textutil, wiki  # noqa: E402
from cqs.store import StoreError  # noqa: E402

SLUG = "gakuen-idolmaster"
TITLE = "学園アイドルマスター"
NOTE = "バンダイナムコエンターテインメント／QualiArts。2024年5月16日配信のアイドル育成シミュレーション。"

BASE = "https://gakuen.idolmaster-official.jp"

# 索引として使う。本文より脚注の外部リンクのほうが本体（docs/sourcing.md 2節）
WIKIPEDIA = (
    "https://ja.wikipedia.org/wiki/"
    "%E5%AD%A6%E5%9C%92%E3%82%A2%E3%82%A4%E3%83%89%E3%83%AB%E3%83%9E%E3%82%B9%E3%82%BF%E3%83%BC"
)

# URLから出典種別を決めるルール（先に入れたものが優先される）
KIND_RULES = [
    ("gakuen.idolmaster-official.jp", "official_site"),
    ("idolmaster-official.jp", "official_site"),
    ("bandainamcoent.co.jp", "official_site"),
    ("asobistore.jp", "official_site"),
    ("x.com/gkmas_official", "official_sns"),
    ("technote.qualiarts.jp", "official_site"),      # 開発元の技術ブログ
    ("developers.cyberagent.co.jp", "official_site"),
    ("www.qualiarts.jp", "official_site"),
    ("cedec.cesa.or.jp", "official_site"),
    ("www.akitashoten.co.jp", "official_site"),      # 版元（GOLD RUSH）
    ("prtimes.jp", "official_site"),                 # 企業のプレスリリース
    ("s.mxtv.jp", "official_site"),
    ("ja.wikipedia.org", "wiki_index"),
    ("gamerch.com/gakumasu", "wiki_index"),
    ("denfaminicogamer.jp", "interview"),
    ("famitsu.com", "article"),
    ("4gamer.net", "article"),
    ("game.watch.impress.co.jp", "article"),
    ("www.gamer.ne.jp", "article"),
    ("www.lisani.jp", "article"),
    ("animatetimes.com", "article"),
    ("realsound.jp", "article"),
    ("gamebiz.jp", "article"),
    ("oricon.co.jp", "article"),
    ("inside-games.jp", "article"),
    ("times.abema.tv", "article"),
    ("natalie.mu", "article"),
    ("japan.cnet.com", "article"),
    ("blog.google", "article"),
]

# 区分＝シナリオ種別。作中のどの語りから来た記述かを分ける
SEGMENT_RULES = [
    ("gakuen.idolmaster-official.jp/system", "ゲームシステム"),
    ("gakuen.idolmaster-official.jp", "作品全体"),
    ("ja.wikipedia.org", "作品全体"),
    ("gamerch.com/gakumasu", "ゲームシステム"),
    ("technote.qualiarts.jp", "制作・現実側"),
    ("developers.cyberagent.co.jp", "制作・現実側"),
    ("www.qualiarts.jp", "制作・現実側"),
    ("cedec.cesa.or.jp", "制作・現実側"),
    ("idolmaster-official.jp", "制作・現実側"),      # ポータルの告知・ライブ情報
    ("www.akitashoten.co.jp", "制作・現実側"),
    ("prtimes.jp", "制作・現実側"),
    ("asobistore.jp", "制作・現実側"),
    ("s.mxtv.jp", "制作・現実側"),
    ("denfaminicogamer.jp", "制作・現実側"),
    ("famitsu.com", "制作・現実側"),
    ("4gamer.net", "制作・現実側"),
    ("game.watch.impress.co.jp", "制作・現実側"),
    ("www.gamer.ne.jp", "制作・現実側"),
    ("www.lisani.jp", "制作・現実側"),
    ("animatetimes.com", "制作・現実側"),
    ("realsound.jp", "制作・現実側"),
    ("gamebiz.jp", "制作・現実側"),
    ("oricon.co.jp", "制作・現実側"),
    ("inside-games.jp", "制作・現実側"),
    ("times.abema.tv", "制作・現実側"),
    ("natalie.mu", "制作・現実側"),
    ("japan.cnet.com", "制作・現実側"),
    ("blog.google", "制作・現実側"),
    ("x.com", "制作・現実側"),
]

# 「その作品を説明する資料」ではなく「言及しているだけの資料」。触れる文だけを採る。
# アイマス他ブランドとの合同イベントは、出演者一覧が他シリーズで埋まる。
ENTITY_ONLY = [
    "live_event/newyear2026",
    "live_event/mr_idolworld2026",
    "live_event/idolworld2025",
    "live_event/orchestra_concert",
    "blog.google",
]

# 抽出は節見出しの名前を文に引き継ぐ。登録されていない名前は節ごと落ちるので先に入れる。
# （公式サイトの学園名簿＋Wikipediaの登場人物節・スタッフ節から）
ENTITIES: list[tuple[str, str, tuple[str, ...]]] = [
    ("花海咲季", "character", ("咲季",)),
    ("月村手毬", "character", ("手毬", "月村")),
    ("藤田ことね", "character", ("ことね", "藤田")),
    ("有村麻央", "character", ("麻央", "有村")),
    ("葛城リーリヤ", "character", ("リーリヤ", "葛城")),
    ("倉本千奈", "character", ("千奈", "倉本")),
    ("紫雲清夏", "character", ("清夏", "紫雲")),
    ("篠澤広", "character", ("篠澤",)),          # 「広」は単字なので別名にしない
    ("姫崎莉波", "character", ("莉波", "姫崎")),
    ("花海佑芽", "character", ("佑芽",)),        # 「花海」は咲季と衝突するので別名にしない
    ("秦谷美鈴", "character", ("美鈴", "秦谷")),
    ("十王星南", "character", ("星南",)),        # 「十王」は邦夫・龍正と衝突する
    ("雨夜燕", "character", ("雨夜",)),
    ("十王邦夫", "character", ("邦夫",)),
    ("根緒亜紗里", "character", ("亜紗里", "根緒")),
    ("犬束静紅", "character", ("犬束",)),
    ("十王龍正", "character", ()),
    ("owl", "character", ("オウル",)),
    ("村雨愁佳", "character", ("村雨",)),
    ("はつみちゃん", "character", ("はつみ",)),
    ("真城優", "character", ("真城",)),
    ("賀陽継", "character", ()),
    ("氷渡香名江", "character", ("氷渡",)),
    ("花岡ミヤビ", "character", ("花岡",)),
    ("内園わこ", "character", ("内園",)),
    ("黒井崇男", "character", ("黒井",)),
    ("賀陽燐羽", "character", ()),
    ("藍井撫子", "character", ("藍井", "撫子")),
    ("白草四音", "character", ()),
    ("白草月花", "character", ()),
    ("灰川楓乃", "character", ()),
    ("灰川鈴吏", "character", ()),
    ("初星学園", "term", ()),
    ("極月学園", "org", ()),
    ("100プロダクション", "org", ()),
    ("ASOBINOTES", "org", ()),
    ("QualiArts", "org", ()),
    ("サイバーエージェント", "org", ()),
    ("バンダイナムコエンターテインメント", "org", ()),
    ("学園アイドルマスター", "work", ("学マス",)),
    ("学園アイドルマスター GOLD RUSH", "work", ("GOLD RUSH",)),
    ("初星学園音楽部", "work", ()),
    ("初星学園放送部", "work", ()),
    ("佐藤大地", "person", ()),
    ("山本亮", "person", ()),
    ("佐藤貴文", "person", ()),
    ("伏見つかさ", "person", ()),
    ("志瑞祐", "person", ()),
    ("雨宮和希", "person", ()),
    ("南野あき", "person", ()),
    ("へちま", "person", ()),
    ("小美野日出文", "person", ()),
    ("アイドル科", "term", ()),
    ("プロデューサー科", "term", ()),
    ("プロデューサー", "term", ()),
    ("学園長", "term", ()),
    ("初星コミュ", "term", ()),
    ("特別教育棟", "term", ()),
    ("本校舎", "term", ()),
    ("ダンスレッスン室", "term", ()),
    ("ビジュアルレッスン室", "term", ()),
    ("ボイスレッスン室", "term", ()),
    ("中庭ステージ", "term", ()),
    ("プロデュース", "term", ()),
    ("ガシャ", "term", ()),
    ("コミュ", "term", ()),
    ("コンテスト", "term", ()),
    ("トレーナー", "term", ()),
    ("ボーカルトレーナー", "term", ()),
    ("ダンストレーナー", "term", ()),
    ("ビジュアルトレーナー", "term", ()),
    # ゲームシステムの用語。攻略Wikiの本文に実際に現れたものだけを入れている。
    # 「プロ」「マスター」「初星」のような他語の一部になる短い語は、
    # 誤って何にでも当たるので登録しない（難易度は「難易度プロ」の形で持つ）。
    ("体力", "term", ()), ("元気", "term", ()), ("好調", "term", ()),
    ("絶好調", "term", ()), ("好印象", "term", ()), ("やる気", "term", ()),
    ("集中", "term", ()), ("温存", "term", ()), ("全力", "term", ()),
    ("消費体力減少", "term", ()), ("パラメータ", "term", ()),
    ("ボーカル", "term", ()), ("ダンス", "term", ()), ("ビジュアル", "term", ()),
    ("レッスン", "term", ()), ("授業", "term", ()), ("おでかけ", "term", ()),
    ("相談", "term", ()), ("活動支給", "term", ()), ("追い込みレッスン", "term", ()),
    ("中間試験", "term", ()), ("最終試験", "term", ()), ("休み", "term", ()),
    ("トラブル", "term", ()), ("スキルカード", "term", ()),
    ("スキルカード強化", "term", ()), ("アクティブスキルカード", "term", ()),
    ("メンタルスキルカード", "term", ()), ("Pアイテム", "term", ()),
    ("Pドリンク", "term", ()), ("サポートカード", "term", ("サポカ",)),
    ("プロデュースアイドル", "term", ("Pアイドル",)), ("メモリー", "term", ()),
    ("センス", "term", ()), ("ロジック", "term", ()), ("アノマリー", "term", ()),
    ("難易度プロ", "term", ()), ("難易度マスター", "term", ()),
    ("Sランク", "term", ()), ("A+", "term", ()),
    ("親愛度", "term", ()), ("トゥルーエンド", "term", ()), ("記録の鍵", "term", ()),
    ("フラワー", "term", ()), ("サポート強化ポイント", "term", ()), ("PLv", "term", ()),
    ("コンテスト", "term", ()), ("サークル", "term", ()), ("アイドルへの道", "term", ()),
    ("NIA", "term", ()), ("レジェンダリーノート", "term", ()),
    ("アナザーアイドル", "term", ()), ("特訓", "term", ()),
    ("ガシャ", "term", ("ガチャ",)), ("リセマラ", "term", ()),
    ("再生成", "term", ()), ("厳選", "term", ()), ("デッキ", "term", ()),
    ("編成", "term", ()),
]

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

# 攻略Wiki（Gamerch）のゲームシステム解説。公式の /system/ はJS描画で本文が取れない。
# 掲示板（招待コード・フレンド募集・雑談・不具合報告）は入れない。
WIKI_SYSTEM = [
    852953, 851410, 851360, 855252, 850392, 850252, 849768, 858511, 856359,
    853443, 852000, 859882, 864485, 851965, 851082, 856785, 894812, 922503,
    855850, 849767, 851157, 856352, 850640, 872292, 855477, 857325, 851140,
    849307, 938185, 938186, 938187, 938188, 938189, 922954, 923030, 849770,
    849308, 850520,
]
WIKI_BASE = "https://gamerch.com/gakumasu/"


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


def has_claims(store, version_id: int) -> bool:
    return store.conn.execute(
        "SELECT 1 FROM claims WHERE source_version_id = ? LIMIT 1", (version_id,)
    ).fetchone() is not None


def ingest_idol_page(store, version_id: int, canonical: str) -> int:
    """学園名簿の1ページから、その人物に帰属させた主張を作る。"""
    raw = store.get_raw_html(version_id)
    if not raw or has_claims(store, version_id):
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
    ap.add_argument("--no-refs", action="store_true", help="Wikipediaの脚注を辿らない")
    args = ap.parse_args()

    path = config.work_path(SLUG)
    store = config.open_work(SLUG) if path.exists() else config.create_work(TITLE, slug=SLUG, note=NOTE)
    print(f"DB: {path}")

    with store:
        for pattern, kind in KIND_RULES:
            store.set_kind_rule(pattern, kind)
        for pattern, segment in SEGMENT_RULES:
            store.set_segment_rule(pattern, segment)
        for name, kind, aliases in ENTITIES:
            store.ensure_entity(name, kind=kind, aliases=aliases)
        print(f"ルール {len(KIND_RULES)}／{len(SEGMENT_RULES)} 件、エンティティ {len(ENTITIES)} 件を登録しました")

        by_url: dict[str, int] = {}
        if args.offline:
            for s in store.list_sources():
                v = store.latest_version(s["id"])
                if v:
                    by_url[s["url"]] = int(v["id"])
        else:
            urls = [BASE + p for p in OTHER_PATHS + [q for q, _ in IDOL_PATHS]]
            urls += [WIKIPEDIA] + [f"{WIKI_BASE}{i}" for i in WIKI_SYSTEM]
            for url in urls:
                try:
                    r = ingest.ingest_url(store, url, respect_robots=args.robots)
                except Exception as e:
                    print(f"  × {url}: {e}")
                    continue
                print(f"  ○ {r['text_length']:>6}字 v{r['version_no']} {url}")
                by_url[url] = r["source_version_id"]

            if not args.no_refs and WIKIPEDIA in by_url:
                # 記事本文より脚注の外部リンクのほうが本体。未取得ぶんを原資料として取り込む
                got = wiki.fetch_references(store, by_url[WIKIPEDIA], limit=200)
                ok = [g for g in got if not g.get("error")]
                print(f"Wikipediaの脚注から {len(ok)} 件を取り込みました（失敗 {len(got) - len(ok)} 件）")
                for g in ok:
                    if g.get("url"):
                        by_url[g["url"]] = g["source_version_id"]

        store.apply_segment_rules()
        for s in store.list_sources():
            if any(pat in (s["url"] or "") for pat in ENTITY_ONLY):
                store.set_source_extract(int(s["id"]), "entity_only")

        total = 0
        for p, canonical in IDOL_PATHS:
            vid = by_url.get(BASE + p)
            if not vid:
                print(f"  × 版が無いので飛ばします: {p}")
                continue
            total += ingest_idol_page(store, vid, canonical)
        print(f"学園名簿から {total} 件の主張を登録しました")

        # 学園名簿以外は自動抽出に回す。公式の告知や一覧は人物名を含まない文も意味を持つ
        idol_versions = {by_url.get(BASE + p) for p, _ in IDOL_PATHS}
        n = 0
        for s in store.list_sources():
            v = store.latest_version(s["id"])
            if not v or int(v["id"]) in idol_versions or has_claims(store, int(v["id"])):
                continue
            try:
                n += len(ingest.register_proposed(store, int(v["id"]), limit=400))
            except StoreError as e:
                print(f"  × source_version {v['id']}: {e}")
        print(f"その他の出典から {n} 件の主張を登録しました")

        x = store.stats()
        print(f"\n{x['title']}: 出典 {x['sources']} / 版 {x['versions']} / "
              f"主張 {x['claims_active']} / エンティティ {x['entities']}")
        print("  確認状態: " + ", ".join(f"{k} {v}" for k, v in x["claims_by_verification"].items()))
        rows = store.conn.execute(
            "SELECT COALESCE(segment, '（未分類）') AS segment, COUNT(*) AS n "
            "FROM sources GROUP BY segment ORDER BY n DESC").fetchall()
        print("  区分: " + ", ".join(f"{r['segment']} {r['n']}" for r in rows))
    print("\n位置（offset）を埋めるには: python examples/backfill_offsets.py " + SLUG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
