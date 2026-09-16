from cqs import ingest


def test_ingest_text_and_propose(store):
    store.ensure_entity("彩葉")
    r = ingest.ingest_text(
        store, "彩葉は17歳の高校生である。かぐやは月から来た。Copyright 2026", kind="official_site", title="紹介"
    )
    cands = ingest.propose_claims(store, r["source_version_id"], entities=["彩葉"],
                                  require_entity=True)
    assert [c["text"] for c in cands] == ["彩葉は17歳の高校生である。"]


def test_ingest_ai_report_creates_candidates_but_no_claims(store):
    r = ingest.ingest_ai_report(
        store, "- かぐやの声優は誰それである。(https://example.com/cast)", title="調査結果"
    )
    assert r["candidates"] == 1 and r["with_url"] == 1
    assert store.stats()["claims_active"] == 0
    assert store.list_candidates()[0]["status"] == "pending"


def test_propose_without_entity_requirement(store):
    """公式SNSの告知のように人物名が出ない出典でも文を拾えること。"""
    store.ensure_entity("彩葉")
    r = ingest.ingest_text(store, "9月18日より復活上映が決定しました。", kind="official_sns", title="告知")
    assert ingest.propose_claims(store, r["source_version_id"]) == []
    got = ingest.propose_claims(store, r["source_version_id"], require_entity=False)
    assert [c["text"] for c in got] == ["9月18日より復活上映が決定しました。"]


def test_register_proposed_does_not_duplicate_on_rerun(store):
    store.ensure_entity("彩葉")
    r = ingest.ingest_text(store, "彩葉は17歳である。作中は2030年の夏である。", kind="fan_chronicle", title="時系列")
    first = ingest.register_proposed(store, r["source_version_id"])
    assert len(first) == 1                       # 人物名のある文だけ
    second = ingest.register_proposed(store, r["source_version_id"], require_entity=False)
    assert len(second) == 1                      # 追加ぶんだけ。既存は重複しない
    assert store.stats()["claims_active"] == 2


def test_section_heading_is_carried_into_claims(store):
    """節見出しを落とすと「8月16日に何があったか」が分からなくなる。"""
    text = (
        "<かぐやを拾った日>\n"
        "決済の記録から7/11だと分かります。\n"
        "<帝が挑戦状を吹っかけてきた日>\n"
        "メールの日付から8月16日だと分かります。\n"
        "まとめ\n"
        "・彩葉とかぐやが一緒に居たのは7/11〜9/12\n"
    )
    r = ingest.ingest_text(store, text, kind="fan_chronicle", title="時系列")
    ids = ingest.register_proposed(store, r["source_version_id"], require_entity=False)
    texts = [store.get_claim(i)["text"] for i in ids]
    assert "帝が挑戦状を吹っかけてきた日: メールの日付から8月16日だと分かります。" in texts
    # 句点で終わらない箇条書きも落とさない
    assert "まとめ: 彩葉とかぐやが一緒に居たのは7/11〜9/12" in texts
    # locator には見出しを冠さない元の文が残る
    assert store.get_claim(ids[0])["locator"] == "決済の記録から7/11だと分かります。"


def test_nested_headings_are_joined(store):
    """「かぐや」→「声 - 誰それ」のように見出しが続く場合は繋いで持つ。"""
    text = "かぐや\n声 - 夏吉ゆうこ\n月からやってきた謎の少女である。\n"
    r = ingest.ingest_text(store, text, kind="wiki_index", title="Wiki")
    ids = ingest.register_proposed(store, r["source_version_id"], require_entity=False)
    assert store.get_claim(ids[0])["text"] == "かぐや / 声 - 夏吉ゆうこ: 月からやってきた謎の少女である。"


def test_entity_matching_ignores_spaces_in_names(store):
    """出典によって「諌山真実」「諌山 真実」と表記が割れるので、空白は無視して拾う。"""
    store.ensure_entity("諌山真実", kind="character")
    text = "諌山 真実（いさやま まみ）\n「まみまみ」の名前でグルメインフルエンサーとして活動している。\n"
    r = ingest.ingest_text(store, text, kind="wiki_index", title="Wiki")
    ids = ingest.register_proposed(store, r["source_version_id"], require_entity=False)
    assert any("諌山真実" in store.get_claim(i)["entities"] for i in ids)


def test_shorter_name_inside_a_longer_one_is_not_tagged(store):
    """「まどか」は「魔法少女まどか☆マギカ」の一部でもある。作品名が出ただけの文に
    登場人物を紐づけない。"""
    store.ensure_entity("鹿目まどか", kind="character", aliases=["まどか"])
    store.ensure_entity("魔法少女まどか☆マギカ", kind="work")
    r = ingest.ingest_text(
        store,
        "魔法少女まどか☆マギカは2011年に放送された。\nまどかは中学2年生である。\n",
        kind="wiki_index", title="Wiki",
    )
    ids = ingest.register_proposed(store, r["source_version_id"], require_entity=False)
    by_text = {store.get_claim(i)["text"]: store.get_claim(i)["entities"] for i in ids}
    assert by_text["魔法少女まどか☆マギカは2011年に放送された。"] == ["魔法少女まどか☆マギカ"]
    assert by_text["まどかは中学2年生である。"] == ["鹿目まどか"]


def test_section_heading_names_carry_into_its_sentences(store):
    """人物名の見出しの下にある文は、その人物についての記述として扱う。

    日本語の地の文は主語を繰り返さないので、文だけを見ると
    「劇中で印象が二転三転していく」のような文が丸ごと落ちる。
    """
    store.ensure_entity("暁美ほむら", aliases=["ほむら"])
    text = (
        "暁美ほむら\n\n"
        "第1話で転校してきた魔法少女。\n\n"
        "劇中でその内面や過去などの秘密が明かされていく。\n\n"
        "巴マミ\n\n"
        "ベテランの魔法少女である。\n"
    )
    sid = store.add_source(url="https://example.com/chara", kind="wiki_index", title="キャラ一覧")
    v = store.add_version(sid, text=text, title="キャラ一覧")
    got = {c["text"]: c["entities"] for c in ingest.propose_claims(store, v.version_id)}
    assert got["劇中でその内面や過去などの秘密が明かされていく。"] == ["暁美ほむら"]
    # 別の人物の節にある文には、その人物は付かない
    assert got["ベテランの魔法少女である。"] == []


def test_reference_sources_keep_sentences_without_a_person(store):
    """用語や設定の説明は人物名を含まないことが多い。参照系の出典では落とさない。"""
    text = "おめかしの魔女\n\n巴マミが魔女化した存在。性質は「ご招待」。\n"
    wiki = store.add_source(url="https://ja.wikipedia.org/wiki/x", kind="wiki_index", title="一覧")
    wv = store.add_version(wiki, text=text, title="一覧")
    got = {c["text"] for c in ingest.propose_claims(store, wv.version_id)}
    assert "性質は「ご招待」。" in got

    # ニュース記事や感想は案内・余談が大半なので、登録済みの人物に触れる文だけを採る
    store.ensure_entity("暁美ほむら")
    news = store.add_source(url="https://news.example/x", kind="article", title="記事")
    nv = store.add_version(news, text=text, title="記事")
    assert [c["text"] for c in ingest.propose_claims(store, nv.version_id)] == []


def test_wiki_footnote_sections_are_not_claims(store):
    """出典・注釈の節に並ぶのは書誌情報であって、作品についての記述ではない。"""
    text = (
        "登場人物\n\nかぐやは月から来た少女である。\n\n"
        "出典\n\n↑『ニュータイプ』2026年3月号のインタビューより。\n\n"
        "参考文献\n\n『設定資料集』第2巻、112頁を参照のこと。\n"
    )
    sid = store.add_source(url="https://ja.wikipedia.org/wiki/y", kind="wiki_index", title="記事")
    v = store.add_version(sid, text=text, title="記事")
    got = {c["text"] for c in ingest.propose_claims(store, v.version_id)}
    assert "かぐやは月から来た少女である。" in got
    assert not [t for t in got if "ニュータイプ" in t or "設定資料集" in t]


def test_a_long_heading_is_not_mistaken_for_body(store):
    """長い見出しを本文とみなすと、その下の記述が1つ前の見出しに紐づいてしまう。"""
    html = (
        "<dl><dt>此岸の魔女</dt><dd>ゲーム版における暁美ほむらが魔女化した存在。</dd>"
        "<dt>おめかしの魔女 / キャンデロロ（Candeloro）</dt>"
        "<dd>巴マミが魔女化した存在。性質は「ご招待」。</dd></dl>"
    )
    from cqs import textutil
    sid = store.add_source(url="https://ja.wikipedia.org/wiki/z", kind="wiki_index", title="一覧")
    v = store.add_version(sid, text=textutil.html_to_text(html)[0], title="一覧")
    by_text = {c["text"]: c["section"] for c in ingest.propose_claims(store, v.version_id)}
    assert by_text["巴マミが魔女化した存在。"] == "おめかしの魔女 / キャンデロロ（Candeloro）"
    assert by_text["ゲーム版における暁美ほむらが魔女化した存在。"] == "此岸の魔女"


def test_extract_mode_overrides_the_kind_default(store):
    """監督や声優の記事は Wikipedia でも作品そのものの資料ではない。"""
    store.ensure_entity("かぐや")
    text = "映画『超かぐや姫！』ではかぐやを演じた。\n\n別の作品にも主演として出演している。\n"
    sid = store.add_source(url="https://ja.wikipedia.org/wiki/voice", kind="wiki_index", title="声優")
    v = store.add_version(sid, text=text, title="声優")
    assert len(ingest.propose_claims(store, v.version_id)) == 2

    store.set_source_extract(sid, "entity_only")
    got = [c["text"] for c in ingest.propose_claims(store, v.version_id)]
    assert got == ["映画『超かぐや姫！』ではかぐやを演じた。"]


def test_cli_register_leaves_the_decision_to_the_source_kind(store, monkeypatch, capsys):
    """`propose --register` が出典種別の既定を上書きしないこと。

    ここで常に「エンティティに触れる文だけ」を渡すと、Wikiや公式サイトから
    用語・設定の説明が丸ごと落ちる。
    """
    import argparse

    from cqs import cli

    store.ensure_entity("かぐや")
    sid = store.add_source(url="https://ja.wikipedia.org/wiki/x", kind="wiki_index", title="記事")
    v = store.add_version(sid, text="かぐやは月から来た。\n\n月には都があるとされる。\n", title="記事")
    monkeypatch.setattr(cli.config, "open_work", lambda slug: store)
    monkeypatch.setattr(store, "close", lambda: None)

    args = argparse.Namespace(work="w", json=False, source_version_id=v.version_id,
                              entity=None, limit=50, register=True,
                              allow_no_entity=False, require_entity=False)
    cli.cmd_propose(args)
    got = {c["text"] for c in store.search_claims(limit=50)}
    assert "月には都があるとされる。" in got


def test_inbox_lands_in_the_source_layer_only(store):
    """出先で書き留めたものは原文層まで。知識層へは通常どおり propose を通す。"""
    entries = [
        {"kind": "text", "title": "貼った資料", "body": "かぐやは月から来た少女である。"},
        {"kind": "note", "title": "気付き", "body": "ほむらの真名を確かめる。"},
        {"kind": "text", "title": "空", "body": "   "},
    ]
    rows = ingest.import_inbox(store, entries)
    assert [("error" in r) for r in rows] == [False, False, True]
    assert store.stats()["claims_active"] == 0        # 主張にはしない
    texts = {st["title"] for st in store.list_sources()}
    assert {"貼った資料", "気付き"} <= texts
    # 通常の道筋で主張にできる
    ids = ingest.register_proposed(store, rows[0]["source_version_id"], require_entity=False)
    assert ids


def test_inbox_accepts_what_the_page_hands_over(store, monkeypatch):
    """ページが書き出すJSONの形を、そのまま受け取れること。"""
    import json

    from cqs import cli

    handed = json.dumps([{"work": "w", "at": "2026-09-16T00:00:00.000Z",
                          "kind": "note", "title": "メモ", "body": "確かめること"}])
    monkeypatch.setattr(cli.config, "open_work", lambda slug: store)
    monkeypatch.setattr(store, "close", lambda: None)
    import argparse
    cli.cmd_inbox(argparse.Namespace(work="w", json=False, file=None, text=handed,
                                     robots=False, no_robots=False))
    assert any(s["title"] == "メモ" for s in store.list_sources())


def test_専用の取り込みが見ていない節にも自動抽出が届く(store):
    """作品ごとの取り込みは記事の一部しか見ていないことがある。

    登場人物欄だけを専用に取り込んだ版へ自動抽出を掛けると、あらすじの節が入り、
    専用の取り込みが既に主張にした一節からは二重に作られない。
    """
    store.ensure_entity("彩葉")
    text = "## 登場人物\n彩葉は17歳の高校生である。\n\n## あらすじ\n彩葉はかぐやと暮らし始める。\n"
    r = ingest.ingest_text(store, text, kind="wiki_index", title="作品記事")
    v = r["source_version_id"]
    # 専用の取り込みが作った主張。言い直しているので文字列では一致しない
    store.add_claim(text="登場人物: 彩葉は17歳である。", source_version_id=v,
                    entities=["彩葉"], locator="彩葉は17歳の高校生である。",
                    offset=text.index("彩葉は17歳の高校生である。"), note="専用の取り込み")

    ids = ingest.register_proposed(store, v, limit=100)
    got = [store.get_claim(i)["text"] for i in ids]
    assert got == ["あらすじ: 彩葉はかぐやと暮らし始める。"]
