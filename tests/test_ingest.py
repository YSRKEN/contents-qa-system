from cqs import ingest


def test_ingest_text_and_propose(store):
    store.ensure_entity("彩葉")
    r = ingest.ingest_text(
        store, "彩葉は17歳の高校生である。かぐやは月から来た。Copyright 2026", kind="official_site", title="紹介"
    )
    cands = ingest.propose_claims(store, r["source_version_id"], entities=["彩葉"])
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
