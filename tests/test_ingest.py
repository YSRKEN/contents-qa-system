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
