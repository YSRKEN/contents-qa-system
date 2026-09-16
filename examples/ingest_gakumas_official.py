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
import urllib.parse
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
    ("seesaawiki.jp/gakumasu", "wiki_index"),
    ("note.com/makura_1210", "fan_chronicle"),
    ("note.com/kaido_729", "fan_chronicle"),
    ("cedil.cesa.or.jp", "official_site"),
    ("cgworld.jp", "article"),
    ("gamemakers.jp", "article"),
    ("learning.unity3d.jp", "article"),
    ("www.inside-games.jp", "article"),
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
    ("note.com/makura_1210", "親愛度コミュ"),
    ("note.com/kaido_729", "制作・現実側"),
    ("cedil.cesa.or.jp", "制作・現実側"),
    ("cgworld.jp", "制作・現実側"),
    ("gamemakers.jp", "制作・現実側"),
    ("learning.unity3d.jp", "制作・現実側"),
    ("www.inside-games.jp", "制作・現実側"),
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
    ("雨夜燕", "character", ("雨夜", "燕")),
    ("十王邦夫", "character", ("邦夫",)),
    ("根緒亜紗里", "character", ("亜紗里", "根緒")),
    ("犬束静紅", "character", ("犬束", "静紅")),
    ("十王龍正", "character", ("龍正",)),
    ("owl", "character", ("オウル",)),
    ("村雨愁佳", "character", ("村雨", "愁佳")),
    ("はつみちゃん", "character", ("はつみ",)),
    ("真城優", "character", ("真城",)),
    ("賀陽継", "character", ()),        # 「継」は他語の一部になるので別名にしない
    ("氷渡香名江", "character", ("氷渡", "香名江")),
    ("花岡ミヤビ", "character", ("花岡", "ミヤビ")),
    ("内園わこ", "character", ("内園", "わこ")),
    ("黒井崇男", "character", ("黒井", "崇男")),
    ("賀陽燐羽", "character", ("燐羽", "りんちゃん")),
    ("藍井撫子", "character", ("藍井", "撫子")),
    ("白草四音", "character", ("四音",)),
    ("白草月花", "character", ("月花",)),
    ("灰川楓乃", "character", ("楓乃",)),
    ("灰川鈴吏", "character", ("鈴吏",)),
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
    # 制作・技術の用語。CEDECの講演とその報告記事に実際に現れたものだけ。
    # 「影」「髪」「汗」「Go」は単字・短語で他の語の一部になるので入れない。
    ("Unity", "term", ()), ("URP", "term", ()), ("レンダリングパイプライン", "term", ()),
    ("Timeline", "term", ()), ("シェーダー", "term", ()), ("ライティング", "term", ()),
    ("法線", "term", ()), ("ポストエフェクト", "term", ()), ("アウトライン", "term", ()),
    ("反射", "term", ()), ("モーションキャプチャー", "term", ()), ("モーション", "term", ()),
    ("振り付け", "term", ()), ("ボーン", "term", ()), ("フェイシャル", "term", ()),
    ("表情", "term", ()), ("目線", "term", ()), ("揺れもの", "term", ()),
    ("テクスチャ", "term", ()), ("ポリゴン", "term", ()), ("最適化", "term", ()),
    ("解像度", "term", ()), ("GPU", "term", ()), ("メモリ", "term", ()),
    ("マスターデータ", "term", ()), ("スプレッドシート", "term", ()),
    ("自動生成", "term", ()), ("自動テスト", "term", ()), ("ゲームAI", "term", ()),
    ("強化学習", "term", ()), ("バランス調整", "term", ()), ("デッキ探索", "term", ()),
    ("グレーボックス最適化", "term", ()), ("シミュレーション", "term", ()),
    ("バックエンド", "term", ()), ("基盤システム", "term", ()),
    ("初星コミュ", "term", ()), ("縦画面コミュ", "term", ()), ("横画面コミュ", "term", ()),
    ("演出", "term", ()), ("カメラワーク", "term", ()), ("3Dモデル", "term", ()),
    ("背景", "term", ()), ("衣装", "term", ()), ("CEDEC", "term", ()),
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

# 非公式wiki（seesaawiki）。人物ページに STEP1〜4 の構成・紹介文・親愛度の解放条件・
# 交友関係・家族関係・好物が、出どころ付きで並んでいる。本文はEUC-JP、URLもEUC-JPで
# パーセント符号化する。ページ一覧は100件で頭打ちなので、名前を控えて持つ。
SEESAA_BASE = "https://seesaawiki.jp/gakumasu/d/"
SEESAA_PAGES = [
    "【標】有村麻央", "【ガラクタロード】十王星南", "【「ねえ、言っちゃうよ。」】秦谷美鈴",
    "もうすぐ本番ですね", "【ときめきエモーション】葛城リーリヤ", "【冠菊】葛城リーリヤ",
    "用語集", "スキルカード一覧/Pアイドル固有", "プロデュースカード雛形",
    "【「ねえ、言っちゃうよ。」】十王星南", "H.I.F", "【「ねえ、言っちゃうよ。」】月村手毬",
    "おやすみのふたり", "【赤裸々】十王星南", "【自己肯定感爆上げ↑↑しゅきしゅきソング】藤田ことね",
    "プロデューサーランキング", "【一体いつから】月村手毬", "楽曲一覧",
    "owl", "村雨愁佳", "MenuBar1",
    "トップページ", "学マス公式配信", "小ネタ集",
    "【真っ白いページと水彩の主人公】花海佑芽", "アイドルへの道", "イベント/さいごの文化祭",
    "【ENDLESS DANCE】花海佑芽", "【ENDLESS DANCE】十王星南", "サポートカード一覧",
    "アチーブメント", "プロデュースアイドル固有早見表", "Pアイテム一覧",
    "十王星南", "プロデュースアイドル一覧", "ガシャ一覧",
    "Pドリンク一覧", "N.I.A/親愛度/好印象", "イクラ〜♪　ウニ〜♪",
    "ぜったいに取るんだ！", "【ときめきエモーション】紫雲清夏", "次の曲は〜ッあの曲だ！！",
    "インタビューお願いします", "食レポ、得意かも！", "スキルカード一覧",
    "奇遇な必然", "姫崎莉波", "花海咲季",
    "花海佑芽", "秦谷美鈴", "篠澤広",
    "紫雲清夏", "倉本千奈", "葛城リーリヤ",
    "有村麻央", "雨夜燕", "藤田ことね",
    "月村手毬", "こんにゃくなきもだめし", "【Yellow Big Bang！】藤田ことね",
    "【L.U.V】姫崎莉波", "【グースーピー】花海佑芽", "【Superlative】秦谷美鈴",
    "【Our Chant】十王星南", "【コントラスト】篠澤広", "【カクシタワタシ】紫雲清夏",
    "【日々、発見的ステップ！】倉本千奈", "【極光】葛城リーリヤ", "【Feel Jewel Dream】有村麻央",
    "【アイヴイ】月村手毬", "【Boom Boom Pow】花海咲季", "Q&A",
    "イベント/毎日学マ水曜日 ファイナルシーズン", "プロデュースアイドル", "1人たりとも欠ける事なく",
    "キラキラして綺麗〜っ！", "ふわふわでワクワク", "サポートカード",
    "プロデュースって大変ね", "盛り上げてこー！", "お母さんか！",
    "わたしと美鈴、超仲良し", "【GO MY WAY!!】花海咲季", "オシャレもメイクも♪",
    "【がむしゃらに行こう！】藤田ことね", "【White Night! White Wish!】藤田ことね", "【キミとセミブルー】有村麻央",
    "さあ、もう一戦！", "みいつけた。", "バレンタイン&#9825;会議中ーっ！",
    "ゆるるんあくび顔", "イベント/十王邦夫のアイドル強化月間〜星々のきらめき〜", "パーティー楽しみだねっ！",
    "【Wildest Flower】花海咲季", "ショップ", "【標】紫雲清夏",
    "【標】倉本千奈", "定期公演『初』/レジェンド", "レッスン・試験詳細",
    "【クライアイ】雨夜燕", "プロデュースについて", "プロデュースメモリー",
    "イベント一覧", "定期公演『初』", "十王邦夫",
    "根緒亜紗里", "WEB記事一覧", "ライブ一覧",
    "出版物一覧", "イベント/親愛度ブーストキャンペーン", "リセマラランキング",
    "初星課題・P課題", "PLv", "最終プロデュース評価",
    "定期公演『初』/花海咲季", "定期公演『初』/月村手毬", "定期公演『初』/藤田ことね",
    "定期公演『初』/雨夜燕", "定期公演『初』/有村麻央", "定期公演『初』/葛城リーリヤ",
    "定期公演『初』/倉本千奈", "定期公演『初』/紫雲清夏", "定期公演『初』/篠澤広",
    "定期公演『初』/十王星南", "定期公演『初』/秦谷美鈴", "定期公演『初』/花海佑芽",
    "定期公演『初』/姫崎莉波", "N.I.A", "N.I.A/親愛度攻略",
    "N.I.A/親愛度/センス", "N.I.A/親愛度/やる気", "N.I.A/親愛度/強気",
    "N.I.A/親愛度/全力", "N.I.A/マスター", "コンテスト",
    "プロデュース編成", "編成一覧", "サポートカード能力早見表",
    "メモリー", "ステージメモリー", "スキルカード一覧/サポートカード固有",
    "スキルカード一覧/その他", "アップデート履歴", "メンテナンス履歴",
    "ミッション", "効果＆強化/低下状態(バフ/デバフ)一覧", "真城優",
    "ダンストレーナー", "ボーカルトレーナー", "ビジュアルトレーナー",
    "賀陽燐羽", "藍井撫子", "白草四音",
    "白草月花", "黒井崇男", "十王龍正",
    "氷渡香名江", "キャラクター一覧", "声優一覧",
    "アイドル別通知集", "衣装ギャラリー/花海咲季", "衣装ギャラリー/月村手毬",
    "衣装ギャラリー/藤田ことね", "衣装ギャラリー/雨夜燕", "衣装ギャラリー/有村麻央",
    "衣装ギャラリー/葛城リーリヤ", "衣装ギャラリー/倉本千奈", "衣装ギャラリー/紫雲清夏",
    "衣装ギャラリー/篠澤広", "衣装ギャラリー/十王星南", "衣装ギャラリー/秦谷美鈴",
    "衣装ギャラリー/花海佑芽", "衣装ギャラリー/姫崎莉波", "衣装ギャラリー/根緒亜紗里",
    "学マス4コマ", "1コマ漫画", "CD一覧",
    "楽曲制作者一覧", "ラジオ番組一覧", "動画",
    "初星コミュ", "コラボ情報まとめ", "学マス関係者一覧",
    "キャンペーン一覧", "初星学園パンフレット", "2024/03/11発表 グッズ",
    "2024/05/02発表 グッズ(全体)", "グッズ(DEBUT LIVE)",
]

# 親愛度コミュのあらすじ（話数・台詞つき）。解釈ではなく本編の読み取りなので fan_chronicle。
# 10名 × STEP1〜3。姫崎莉波・秦谷美鈴・雨夜燕ぶんはまだ無い。
# 制作・技術。CEDECの講演ページ（概要・講演者・メッセージ）と、その報告記事。
# QualiArtsの技術ブログには学マスを題に採った記事が無く、技術の詳細は
# CEDECの講演と、それを詳しく書き起こした媒体の記事にある。
TECH_URLS = [
    # 講演そのもの
    "https://cedec.cesa.or.jp/2024/session/detail/s660138bbdf4c1/",   # 3Dキャラクター・背景制作
    "https://cedec.cesa.or.jp/2024/session/detail/s6601280795fb3/",   # レンダリングパイプライン
    "https://cedec.cesa.or.jp/2024/session/detail/s66040e2aeca6e/",   # ゲームAIによるバランス調整
    "https://cedec.cesa.or.jp/2024/session/detail/s660150b358c6d/",   # Timelineを使ったライブ制作
    "https://cedec.cesa.or.jp/2025/timetable/detail/s67ae9c5291bcc/", # コミュができるまで
    "https://cedec.cesa.or.jp/2025/timetable/detail/s67a5b1155ef14/", # バックエンドの基盤
    "https://cedec.cesa.or.jp/2025/timetable/detail/s679c66603fcbd/", # 開発ラインの量産
    "https://cedec.cesa.or.jp/2025/timetable/detail/s67a303bc65ad8/", # 自動テストの運用
    "https://cedil.cesa.or.jp/cedil_sessions/view/2936",
    "https://cedil.cesa.or.jp/cedil_sessions/view/2963",
    "https://cedil.cesa.or.jp/cedil_sessions/view/3000",
    "https://cedil.cesa.or.jp/cedil_sessions/view/3196",
    "https://technote.qualiarts.jp/article/81/",
    # 講演の報告記事。講演ページより本文が厚い
    "https://cgworld.jp/article/202410-cedec-imas.html",
    "https://gamemakers.jp/article/2025_01_29_90729/",
    "https://gamemakers.jp/article/2025_11_07_119247/",
    "https://game.watch.impress.co.jp/docs/kikaku/2033720.html",
    "https://game.watch.impress.co.jp/docs/kikaku/1617554.html",
    "https://www.gamer.ne.jp/news/202507260018/",
    "https://www.inside-games.jp/article/2025/08/23/170854.html",
    "https://www.4gamer.net/games/778/G077853/20250729037/",
    "https://www.4gamer.net/games/778/G077853/20240822037/",
    "https://www.4gamer.net/games/778/G077853/20240822052/",
    "https://www.famitsu.com/article/202408/14977",
    "https://www.famitsu.com/article/202408/15013",
    "https://www.famitsu.com/article/202408/15047",
    "https://news.denfaminicogamer.jp/news/240909a",
    "https://learning.unity3d.jp/10126/",
    # 技術系資料の索引（ここから上のURLを辿った）
    "https://note.com/kaido_729/n/n1ca9dc9bf9df",
]

NOTE_BASE = "https://note.com/makura_1210/n/"
NOTE_KEYS = [
    "n70c9ba38e690", "n9cd66403d880", "n71b0cc38d94e", "n6ae46b0d3e45", "nf4d169551f03",
    "n55bd15fed1e7", "n8cdd556dfd1b", "n1ed1461283a5", "nd90bcf725d62", "n49e9b84aff30",
    "nfac53a9147be", "nce01de2422a2", "n0ecec95d6083", "n1d7f53da7404", "necd2dc543073",
    "n4692c6aaaa92", "nabea699c6dbb", "n4f89a9053479", "nffd644530bbc", "nd83436cd34ab",
    "n70452827af9b", "n9f5af571a272", "nae5f15393e72", "n0901c3640860", "nae1e9c2e4c6f",
    "n98c7ffabce49", "n944c07284177", "n4a49228e7d17", "n286ec6c73e46", "nb44ad0be5b5c",
]


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


# 人物について書かれたページ。ページ全体が1人の記述なので、本文には名前が出ない。
# 「広にとっては『初めてできた友達』。」のような文は、題名を見ないと誰の話か分からない。
SUBJECT_PATTERNS = [
    # seesaawiki の人物ページ（題名がそのまま人物名）
    (re.compile(r"^(.+?) - 学園アイドルマスターwiki"), 1),
    # note の親愛度コミュ振り返り（「…振り返り│篠澤広・STEP2｜まくら」）
    (re.compile(r"振り返り│(.+?)・STEP"), 1),
]


def subject_of(store, title: str | None) -> str | None:
    """その出典が誰について書かれたものかを、題名から決める。"""
    if not title:
        return None
    for pattern, group in SUBJECT_PATTERNS:
        m = pattern.search(title)
        if not m:
            continue
        name = m.group(group).strip()
        # 「定期公演『初』/篠澤広」「衣装ギャラリー/月村手毬」のように、
        # 題名の後ろに人物名が付く形もある
        for candidate in (name, name.rsplit("/", 1)[-1]):
            e = store.resolve_entity(candidate)
            if e and e["kind"] == "character":
                return str(e["name"])
    return None


def ingest_subject_page(store, version_id: int, canonical: str) -> int:
    """1人について書かれたページの文を、その人物に帰属させて登録する。

    自動抽出に任せると、本文に名前が出ない文が落ちるか、
    たまたま同じ文に出てきた別の名前だけに紐づいてしまう。
    """
    if has_claims(store, version_id):
        return 0
    v = store.get_version(version_id)
    surfaces = surface_map(store)
    n = 0
    from cqs import extract

    for c in extract.dedupe(extract.candidate_sentences(v["text"], entities=list(surfaces))):
        if extract.in_reference_section(c["section"]):
            continue
        ents = sorted({surfaces[s] for s in c["entities"]} | {canonical})
        head = f"{c['section']}: " if c["section"] else ""
        store.add_claim(
            text=f"{canonical} / {head}{c['text']}",
            source_version_id=version_id, entities=ents, locator=c["text"],
            offset=c["offset"], note="その人物について書かれたページより",
        )
        n += 1
    return n


# seesaawiki のページ名から区分（シナリオ種別）を決める。URLがEUC-JPの符号なので題名で見る。
SEESAA_SYSTEM = {
    "用語集", "アチーブメント", "ショップ", "ガシャ一覧", "サポートカード", "サポートカード一覧",
    "プロデュースアイドル", "プロデュースアイドル一覧", "プロデュースアイドル固有早見表",
    "プロデュースカード雛形", "プロデュースについて", "プロデュースメモリー",
    "Pアイテム一覧", "Pドリンク一覧", "レッスン・試験詳細", "アイドルへの道",
    "N.I.A/親愛度/好印象", "プロデューサーランキング", "MenuBar1", "トップページ",
}
SEESAA_WHOLE = {"楽曲一覧", "学マス公式配信", "小ネタ集", "Q&A", "イベント一覧"}


def seesaa_title(version) -> str:
    return ((version["title"] or "") if version else "").split(" - 学園アイドルマスターwiki")[0].strip()


def classify_seesaa(store, title: str, characters: set[str]) -> str:
    if title.startswith("イベント/") or title.startswith("【"):
        return "イベント・サポカコミュ"
    if title.startswith(("定期公演『初』", "H.I.F")):
        return "初星シナリオ"
    if title in SEESAA_SYSTEM or title.startswith("スキルカード一覧"):
        return "ゲームシステム"
    if title in SEESAA_WHOLE or title in characters:
        return "作品全体"
    return "イベント・サポカコミュ"      # サポートカードの個別ページ


# 一覧・履歴・ギャラリーなどの索引ページはカード名ではない。区切りを含む題名
# （「定期公演『初』/篠澤広」）も個別カードではない。題名の切り出しに失敗した
# ページ（「… - 学園アイドルマスターwiki」が残る）もここで落ちる。
_NOT_A_CARD = re.compile(r"(一覧|履歴|ギャラリー|攻略|雛形|集|ランキング)$|/| - ")


def _looks_like_card(title: str) -> bool:
    return bool(title) and not _NOT_A_CARD.search(title)


def harvest_entities(store) -> dict[str, int]:
    """seesaawiki のページ名と楽曲一覧から、カード名・サポカ名・イベント名・曲名を拾う。

    抽出は登録済みの名前しか拾わないので、主張を作る前に入れておく必要がある。
    「初」「標」「見て」のように他の語の一部になる名前は、何にでも当たるので入れない。
    """
    too_short = {"初", "標", "見て", "ふわふわ"}
    characters = {e["name"] for e in store.list_entities() if e["kind"] == "character"}
    found = {"card": set(), "event": set(), "song": set()}
    songs_text = ""
    for src in store.list_sources():
        if "seesaawiki.jp/gakumasu" not in (src["url"] or ""):
            continue
        v = store.latest_version(src["id"])
        t = seesaa_title(v)
        if t == "楽曲一覧":
            songs_text = v["text"]
        if not t or t in SEESAA_SYSTEM or t in SEESAA_WHOLE or t in characters:
            continue
        if t.startswith("イベント/"):
            found["event"].add(t[len("イベント/"):])
        elif m := re.match(r"^【(.+?)】", t):
            found["card"].add(m.group(1))
        elif _looks_like_card(t):
            found["card"].add(t)          # サポートカードの個別ページ
    for name in re.findall(r"曲名: (.+?) / 作詞", songs_text):
        found["song"].add(name.rstrip("?"))   # 未作成リンクの「?」を落とす
    for kind, names in found.items():
        for n in names - too_short - characters:
            store.ensure_entity(n, kind=kind)
    return {k: len(v - too_short - characters) for k, v in found.items()}


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
            urls += [SEESAA_BASE + urllib.parse.quote(n.encode("euc_jp")) for n in SEESAA_PAGES]
            urls += [NOTE_BASE + k for k in NOTE_KEYS] + TECH_URLS
            for url in urls:
                try:
                    r = ingest.ingest_url(store, url, respect_robots=args.robots)
                except Exception as e:
                    print(f"  × {url}: {e}")
                    continue
                print(f"  ○ {r['text_length']:>6}字 v{r['version_no']} {url}")
                by_url[url] = r["source_version_id"]

            if not args.no_refs:
                extra = [u for u in index_links(store, by_url) if u not in by_url]
                print(f"索引ページから外部リンク {len(extra)} 件")
                for url in extra:
                    try:
                        r = ingest.ingest_url(store, url, respect_robots=args.robots)
                    except Exception as e:
                        print(f"  × {url}: {e}")
                        continue
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

        characters = {e["name"] for e in store.list_entities() if e["kind"] == "character"}
        for s in store.list_sources():
            if "seesaawiki.jp/gakumasu" not in (s["url"] or ""):
                continue
            t = seesaa_title(store.latest_version(s["id"]))
            store.set_source_segment(int(s["id"]), classify_seesaa(store, t, characters))
        print("拾ったエンティティ: " + ", ".join(f"{k} {n}" for k, n in harvest_entities(store).items()))

        total = 0
        for p, canonical in IDOL_PATHS:
            vid = by_url.get(BASE + p)
            if not vid:
                print(f"  × 版が無いので飛ばします: {p}")
                continue
            total += ingest_idol_page(store, vid, canonical)
        print(f"学園名簿から {total} 件の主張を登録しました")

        # 1人について書かれたページは、題名の人物に帰属させて登録する
        idol_versions = {by_url.get(BASE + p) for p, _ in IDOL_PATHS}
        subject = 0
        for s in store.list_sources():
            v = store.latest_version(s["id"])
            if not v or int(v["id"]) in idol_versions:
                continue
            who = subject_of(store, v["title"])
            if who:
                subject += ingest_subject_page(store, int(v["id"]), who)
        print(f"人物ページから {subject} 件の主張を登録しました")

        # 残りは自動抽出に回す。公式の告知や一覧は人物名を含まない文も意味を持つ
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
