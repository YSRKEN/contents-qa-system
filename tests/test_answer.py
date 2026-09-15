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
