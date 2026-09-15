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


def test_secondhand_sits_between_article_and_fan_interpretation(store):
    """本編の読み取り（伝聞）は、感想より上・記事より下に並ぶこと。"""
    _, official = _src(store)
    _, art = _src(store, kind="article", url="https://news.example/1")
    _, chron = _src(store, kind="fan_chronicle", url="https://blog.example/timeline")
    _, note = _src(store, kind="fan_note", url="https://note.com/x")
    for v in (note, chron, art, official):
        store.add_claim(text=f"彩葉の話 {v.version_id}", source_version_id=v.version_id, entities=["彩葉"])
    assert [r["verification"] for r in store.search_claims(entity="彩葉")] == [
        "official", "article", "secondhand", "fan_interpretation",
    ]


def test_set_source_kind_reclassifies_without_touching_claims(store):
    sid, v = _src(store, kind="fan_note", url="https://blog.example/t")
    cid = store.add_claim(text="作中の日付の読み取り", source_version_id=v.version_id)
    assert store.get_claim(cid)["verification"] == "fan_interpretation"
    store.set_source_kind(sid, "fan_chronicle")
    assert store.get_source(sid)["kind"] == "fan_chronicle"
    # 既存の主張の確認状態は自動では変えない
    assert store.get_claim(cid)["verification"] == "fan_interpretation"


def test_x_account_is_not_official_by_host_alone(store):
    """ホスト名だけでは公式か判別できないので、既定ではファン扱いにする。"""
    assert store.guess_kind("https://x.com/some_fan") == "fan_note"
    store.set_kind_rule("x.com/Cho_KaguyaHime", "official_sns")
    assert store.guess_kind("https://x.com/Cho_KaguyaHime/status/1") == "official_sns"
    assert store.guess_kind("https://x.com/some_fan/status/1") == "fan_note"


def test_wiki_grounds_claims_as_secondhand(store):
    """Wikiは索引であると同時に、出典が書籍しかない記述の受け皿にもなる（伝聞扱い）。"""
    _, v = _src(store, kind="wiki_index", url="https://ja.wikipedia.org/wiki/x")
    cid = store.add_claim(text="小説版の著者は誰それである。", source_version_id=v.version_id)
    assert store.get_claim(cid)["verification"] == "secondhand"


def test_segment_rules_tag_sources_by_which_work_they_describe(store):
    """同じ作品世界でも、どの作品の記述かは確認状態とは別の軸で持つ。"""
    store.set_segment_rule("example.com/tv", "TVシリーズ")
    store.set_segment_rule("example.com/movie", "劇場版")
    sid_tv, v_tv = _src(store, url="https://example.com/tv/story")
    sid_mv, v_mv = _src(store, url="https://example.com/movie/story")
    sid_x, _ = _src(store, url="https://example.com/other")
    assert store.get_source(sid_tv)["segment"] == "TVシリーズ"
    assert store.get_source(sid_mv)["segment"] == "劇場版"
    assert store.get_source(sid_x)["segment"] is None

    cid = store.add_claim(text="結末はこうなる。", source_version_id=v_mv.version_id)
    assert store.get_claim(cid)["segment"] == "劇場版"


def test_segment_can_be_set_by_hand_and_rules_applied_later(store):
    sid, _ = _src(store, url="https://example.com/spinoff/1")
    store.set_source_segment(sid, "スピンオフ")
    assert store.get_source(sid)["segment"] == "スピンオフ"
    # 後からルールを足した場合は、当て直しで既存の出典にも反映する
    store.set_segment_rule("example.com/spinoff", "外伝ゲーム")
    assert store.apply_segment_rules() == 1
    assert store.get_source(sid)["segment"] == "外伝ゲーム"
    store.set_source_segment(sid, None)
    assert store.get_source(sid)["segment"] is None
