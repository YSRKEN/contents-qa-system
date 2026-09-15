from cqs import answer


def test_prompt_marks_fan_interpretation(store):
    sid = store.add_source(url="https://note.com/x", kind="fan_note", title="感想")
    v = store.add_version(sid, text="彩葉はかぐやを娘のように見ていた。")
    store.ensure_entity("彩葉")
    store.add_claim(text="彩葉はかぐやを娘のように見ている。", source_version_id=v.version_id, entities=["彩葉"])

    ctx = answer.retrieve(store, "彩葉はかぐやをどう見ている？")
    body = answer.format_context(ctx)
    assert "ファン解釈" in body
    p = answer.build_prompt(ctx)
    assert "作品内の事実の根拠にはしない" in p["system"]


def test_answer_without_api_key_returns_prompt_only(store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    r = answer.answer(store, "何か質問")
    assert r["answer"] is None and "ANTHROPIC_API_KEY" in r["reason"]
    assert r["prompt"]["user"].endswith("何か質問")


def test_retrieve_finds_claims_without_entities_via_terms(store):
    """人物名を含まない記述（日付など）も、検索語を足せば拾えること。"""
    sid = store.add_source(url="https://blog.example/t", kind="fan_chronicle", title="時系列")
    v = store.add_version(sid, text="2030年の9/12はリアルでも満月です。")
    store.ensure_entity("かぐや")
    store.add_claim(text="2030年の9/12はリアルでも満月です。", source_version_id=v.version_id)
    store.add_claim(text="かぐや: 月からやってきた少女。", source_version_id=v.version_id, entities=["かぐや"])

    plain = answer.retrieve(store, "かぐやが月に帰った日付は？")
    assert "2030年の9/12はリアルでも満月です。" not in [c["text"] for c in plain["claims"]]

    with_terms = answer.retrieve(store, "かぐやが月に帰った日付は？", extra_terms=["満月"])
    assert "2030年の9/12はリアルでも満月です。" in [c["text"] for c in with_terms["claims"]]


def test_question_terms_splits_a_sentence(store):
    """質問文をまるごと全文検索に渡すと必ず0件になる。語に割ること。"""
    assert set(answer.question_terms("作品内の時系列を箇条書きで書いて")) == {"箇条書", "時系列", "作品内"}


def test_retrieve_returns_a_section_in_document_order(store):
    """順序が答えになる質問では、当たった出典を並び順ごと返すこと。"""
    from cqs import ingest

    text = (
        "<かぐやを拾った日>\n7/11に拾ったと分かります。\n"
        "<ミニライブの日>\n7/18に開演しました。\n"
        "<卒業ライブ>\n9/12は満月です。\n"
        "まとめ\n・時系列としては7/11から9/12までの出来事です。\n"
    )
    r = ingest.ingest_text(store, text, kind="fan_chronicle", title="作中の時系列まとめ")
    ingest.register_proposed(store, r["source_version_id"], require_entity=False)
    # 別の出典を厚めに入れて、件数の多さだけで勝たないことも見る
    other = ingest.ingest_text(
        store, "\n".join(f"時系列とは関係のない話題その{i}です。" for i in range(30)),
        kind="article", title="関係のない記事")
    ingest.register_proposed(store, other["source_version_id"], require_entity=False)

    ctx = answer.retrieve(store, "作中の時系列を教えて", max_claims=20)
    texts = [c["text"] for c in ctx["claims"]]
    assert "かぐやを拾った日: 7/11に拾ったと分かります。" in texts
    assert "卒業ライブ: 9/12は満月です。" in texts
    # 原文の並び順が保たれている
    assert texts.index("かぐやを拾った日: 7/11に拾ったと分かります。") < texts.index("卒業ライブ: 9/12は満月です。")


def test_retrieve_pulls_in_contradicting_claims(store):
    sid = store.add_source(url="https://a.example", kind="official_sns", title="公式")
    v = store.add_version(sid, text="誕生日は7月12日です。")
    a = store.add_claim(text="かぐやの誕生日は7月12日である。", source_version_id=v.version_id, entities=["かぐや"])
    sid2 = store.add_source(url="https://b.example", kind="fan_chronicle", title="ファン整理")
    v2 = store.add_version(sid2, text="誕生日は7月5日とされる。")
    b = store.add_claim(text="かぐやの誕生日は7月5日とされる。", source_version_id=v2.version_id)
    store.link_claims(a, b, "contradicts")
    ctx = answer.retrieve(store, "かぐやの誕生日は？", max_claims=5)
    ids = [c["id"] for c in ctx["claims"]]
    assert a in ids and b in ids


def test_prompt_shows_which_work_a_claim_describes(store):
    """関連作品をまとめて1つのDBに入れた場合、区分を伏せたまま並べさせない。"""
    store.set_segment_rule("example.com/movie", "劇場版")
    sid = store.add_source(url="https://example.com/movie/", kind="official_site", title="劇場版公式")
    v = store.add_version(sid, text="ほむらは最後に街を去る。")
    store.ensure_entity("ほむら")
    store.add_claim(text="ほむらは最後に街を去る。", source_version_id=v.version_id, entities=["ほむら"])

    ctx = answer.retrieve(store, "ほむらの結末は？")
    body = answer.format_context(ctx)
    assert "区分: 劇場版" in body
    assert "区分" in answer.build_prompt(ctx)["system"]
