import pytest

from cqs.store import StoreError


def _src(store, kind="official_site", url="https://example.com/a"):
    sid = store.add_source(url=url, kind=kind, title="出典")
    v = store.add_version(sid, text="かぐやは月から来た。彩葉は17歳の高校生である。", title="出典")
    return sid, v


def test_same_content_does_not_create_new_version(store):
    sid, v1 = _src(store)
    v2 = store.add_version(sid, text="かぐやは月から来た。彩葉は17歳の高校生である。")
    assert v2.changed is False
    assert v2.version_id == v1.version_id


def test_changed_content_flags_dependent_claims(store):
    sid, v = _src(store)
    cid = store.add_claim(text="彩葉は17歳である。", source_version_id=v.version_id, entities=["彩葉"])
    assert store.get_claim(cid)["verification"] == "official"

    v2 = store.add_version(sid, text="かぐやは月から来た。彩葉は18歳の高校生である。")
    assert v2.changed is True and v2.rechecked_claims == 1
    assert store.get_claim(cid)["verification"] == "needs_recheck"


def test_verification_defaults_follow_source_kind(store):
    _, official = _src(store)
    _, note = _src(store, kind="fan_note", url="https://note.com/x")
    a = store.add_claim(text="公式の記述", source_version_id=official.version_id)
    b = store.add_claim(text="感想の記述", source_version_id=note.version_id)
    assert store.get_claim(a)["verification"] == "official"
    assert store.get_claim(b)["verification"] == "fan_interpretation"


def test_ai_report_cannot_back_a_claim(store):
    _, rep = _src(store, kind="ai_report", url="https://example.com/report")
    with pytest.raises(StoreError, match="根拠にできません"):
        store.add_claim(text="AIが言っていた", source_version_id=rep.version_id)


def test_search_orders_official_before_fan(store):
    _, official = _src(store)
    _, note = _src(store, kind="fan_note", url="https://note.com/x")
    store.add_claim(text="彩葉についての感想", source_version_id=note.version_id, entities=["彩葉"])
    store.add_claim(text="彩葉についての公式記述", source_version_id=official.version_id, entities=["彩葉"])
    rows = store.search_claims(entity="彩葉")
    assert [r["verification"] for r in rows] == ["official", "fan_interpretation"]


def test_alias_resolves_to_entity(store):
    _, v = _src(store)
    store.ensure_entity("酒寄彩葉", aliases=["彩葉"])
    store.add_claim(text="酒寄彩葉は高校生", source_version_id=v.version_id, entities=["酒寄彩葉"])
    assert len(store.search_claims(entity="彩葉")) == 1


def test_supersede_keeps_old_claim(store):
    _, v = _src(store)
    old = store.add_claim(text="彩葉は16歳", source_version_id=v.version_id, entities=["彩葉"])
    new = store.add_claim(text="彩葉は17歳", source_version_id=v.version_id, entities=["彩葉"], supersedes=old)
    assert store.get_claim(old)["status"] == "superseded"
    assert [r["id"] for r in store.search_claims(entity="彩葉")] == [new]
    assert old in [r["id"] for r in store.search_claims(entity="彩葉", status=None)]


def test_contradiction_is_a_link_not_a_state(store):
    _, v = _src(store)
    a = store.add_claim(text="彩葉は17歳", source_version_id=v.version_id)
    b = store.add_claim(text="彩葉は18歳", source_version_id=v.version_id)
    store.link_claims(a, b, "contradicts")
    assert store.get_claim(a)["contradicts"] == [b]
    assert store.get_claim(b)["contradicts"] == [a]
    # 矛盾しても両方が有効なまま残る
    assert store.get_claim(a)["status"] == "active"
    assert store.get_claim(b)["status"] == "active"


def test_related_entities_counts_co_occurrence(store):
    _, v = _src(store)
    store.add_claim(text="かぐやと彩葉は同居している", source_version_id=v.version_id, entities=["かぐや", "彩葉"])
    rel = store.related_entities("彩葉")
    assert rel and rel[0]["name"] == "かぐや" and rel[0]["shared"] == 1


def test_source_search_and_excerpt(store):
    _, v = _src(store)
    rows = store.search_sources("高校生")
    assert rows and rows[0]["source_version_id"] == v.version_id
    assert "高校生" in rows[0]["excerpt"]
    part = store.source_excerpt(v.version_id, offset=0, length=5)
    assert len(part["excerpt"]) == 5 and part["has_more"] is True


def test_guess_kind_uses_work_specific_rule_first(store):
    assert store.guess_kind("https://note.com/x/n/1") == "fan_note"
    store.set_kind_rule("cho-kaguyahime.com", "official_site")
    assert store.guess_kind("https://www.cho-kaguyahime.com/news/") == "official_site"
    assert store.guess_kind("https://unknown.example/a") == "article"


def test_two_character_name_is_searchable(store):
    """2文字の人名は trigram 索引に載らないため、LIKE 併用で引けることを確かめる。"""
    _, v = _src(store)
    store.add_claim(text="彩葉は17歳の高校生である。", source_version_id=v.version_id)
    store.add_claim(text="かぐやは月から来た。", source_version_id=v.version_id)
    assert [r["text"] for r in store.search_claims(query="彩葉")] == ["彩葉は17歳の高校生である。"]
    assert store.search_sources("彩葉")
    # 3文字以上の語と混ぜても両方が効く
    assert store.search_claims(query="彩葉 高校生")
    assert store.search_claims(query="彩葉 月から") == []
