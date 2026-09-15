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
    """人物名を含まない記述（日付など）も拾えること。"""
    sid = store.add_source(url="https://blog.example/t", kind="fan_chronicle", title="時系列")
    v = store.add_version(sid, text="2030年の9/12はリアルでも満月です。")
    store.ensure_entity("かぐや")
    store.add_claim(text="2030年の9/12はリアルでも満月です。", source_version_id=v.version_id)
    store.add_claim(text="かぐや: 月からやってきた少女。", source_version_id=v.version_id, entities=["かぐや"])

    got = [c["text"] for c in answer.retrieve(store, "かぐやが月に帰った日付は？")["claims"]]
    assert "2030年の9/12はリアルでも満月です。" in got

    with_terms = answer.retrieve(store, "かぐやが月に帰った日付は？", extra_terms=["満月"])
    assert "2030年の9/12はリアルでも満月です。" in [c["text"] for c in with_terms["claims"]]


def test_question_terms_splits_a_sentence(store):
    """質問文をまるごと全文検索に渡すと必ず0件になる。語に割ること。"""
    assert set(answer.question_terms("作品内の時系列を書いて")) == {"時系列", "作品内"}


def test_question_terms_keeps_single_kanji_stems(store):
    """漢字1文字＋送り仮名の語を落とすと、内容語が1つも残らない質問がある。"""
    assert set(answer.question_terms("まどかはどんな願いを叶えた？")) == {"願", "願い", "叶", "叶え"}
    # 助詞は送り仮名ではないので足さない（「月に」では本文に当たらない）
    assert "月に" not in answer.question_terms("かぐやが月に帰った日付は？")
    assert "月" in answer.question_terms("かぐやが月に帰った日付は？")
    # 質問の言い回しは検索語にしない
    assert "教え" not in answer.question_terms("この作品について詳しく教えて")
    # 「全何話」は語ではないので、疑問詞で切る
    assert set(answer.question_terms("この作品は全何話？")) == {"作品", "全", "話"}


def test_thin_results_are_padded_with_surrounding_claims(store):
    """当たりが数件しかない質問では、その前後を足して文脈にする。"""
    sid = store.add_source(url="https://example.com/a", kind="official_site", title="紹介")
    v = store.add_version(sid, text="紹介", title="紹介")
    ids = [store.add_claim(text=f"背景の説明{i}。", source_version_id=v.version_id) for i in range(6)]
    hit = store.add_claim(text="主題歌はある楽曲である。", source_version_id=v.version_id)
    ids += [store.add_claim(text=f"続きの説明{i}。", source_version_id=v.version_id) for i in range(6)]

    got = {c["id"] for c in answer.retrieve(store, "主題歌は？", max_claims=20)["claims"]}
    assert hit in got
    assert len(got) > 1
    assert got & set(ids)


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


def test_section_only_match_ranks_below_a_real_mention(store):
    """名言集のように、見出しだけが人物名で本文がセリフの出典に材料を食われない。"""
    store.ensure_entity("彩葉")
    quotes = store.add_source(url="https://example.com/meigen", kind="fan_chronicle", title="名言集")
    qv = store.add_version(quotes, text="名言", title="名言集")
    for i in range(30):
        store.add_claim(text=f"By 彩葉 / 名言まとめ: どうしてこうなったの{i}。",
                        source_version_id=qv.version_id, entities=["彩葉"])
    prose = store.add_source(url="https://example.com/wiki", kind="wiki_index", title="解説")
    pv = store.add_version(prose, text="解説", title="解説")
    said = store.add_claim(text="人物: 彩葉はかぐやの保護者として振る舞う。",
                           source_version_id=pv.version_id, entities=["彩葉"])

    ctx = answer.retrieve(store, "彩葉はどんな人？", max_claims=10)
    ids = [c["id"] for c in ctx["claims"]]
    assert said in ids
    assert ids.index(said) < 5


def test_run_comes_from_where_the_hits_cluster(store):
    """大きい出典でも、当たりが密集した区間が選ばれる（端から端まで平均されない）。"""
    sid = store.add_source(url="https://example.com/long", kind="wiki_index", title="長い記事")
    v = store.add_version(sid, text="長い記事", title="長い記事")
    store.ensure_entity("かぐや")
    # 同じ文の繰り返しだと重複除去に当たるので、1件ずつ違う内容にする
    ids = []
    words = ["月", "竹", "帝", "翁", "媼", "衣", "都", "山", "海", "星", "夜", "春",
             "夏", "秋", "冬", "雨", "雪", "風", "花", "鳥"]
    for i in range(60):
        w = words[i % len(words)]
        if 40 <= i < 52:
            ids.append(store.add_claim(
                text=f"節: かぐやは{w}にまつわる出来事に{i}度関わったとされる。",
                source_version_id=v.version_id, entities=["かぐや"]))
        else:
            ids.append(store.add_claim(
                text=f"節: {w}についての別の話題が{i}件記されている。",
                source_version_id=v.version_id))
    ctx = answer.retrieve(store, "かぐやについて教えて", max_claims=30)
    got = {c["id"] for c in ctx["claims"]}
    # 当たりの集まっているあたりが中心に来て、遠く離れた場所は入らない
    assert len(got & set(ids[40:52])) >= 8
    assert not (set(ids[:20]) & got)


def test_boilerplate_is_not_repeated_in_the_material(store):
    """あらすじの定型文は多くの出典に載る。出典が増えるほど枠を食い潰す。"""
    store.ensure_entity("かぐや")
    boiler = "願いを叶えた代償として魔法少女となり、人知れず人類の敵と戦うことになる少女たちの物語である。"
    for i in range(6):
        sid = store.add_source(url=f"https://example.com/{i}", kind="article", title=f"紹介{i}")
        v = store.add_version(sid, text=boiler, title=f"紹介{i}")
        store.add_claim(text=boiler.replace("物語である", f"物語{i}である"),
                        source_version_id=v.version_id, entities=["かぐや"])
    got = [c["text"] for c in answer.retrieve(store, "かぐやの願いは？", max_claims=20)["claims"]]
    assert sum(1 for t in got if "代償として魔法少女" in t) == 1


def test_a_section_about_the_asked_thing_is_returned_in_depth(store):
    """節の題が問われている当の対象なら、その節から厚く取ること。

    節の中の説明文は対象の名前を繰り返さない（「上半身は鎧をまとった騎士で……」）。
    見出しだけの一致を割り引いたまま節を選ぶと、この節が丸ごと沈む。
    """
    store.ensure_entity("人魚の魔女", kind="term", aliases=["オクタヴィア"])
    sid = store.add_source(url="https://ja.wikipedia.org/wiki/x", kind="wiki_index", title="キャラクター一覧")
    v = store.add_version(sid, text="一覧", title="キャラクター一覧")
    detail = [
        "美樹さやかが魔女化した存在である。",
        "コンサートホールのような結界に住んでいる。",
        "上半身は三つの目を持つ鎧兜をまとった巨体の騎士である。",
        "下半身は魚の姿をしている。",
        "多数の車輪を放つ攻撃を行う。",
    ]
    want = [store.add_claim(text=f"人魚の魔女 / オクタヴィア: {t}", source_version_id=v.version_id,
                            offset=i, entities=["人魚の魔女"]) for i, t in enumerate(detail)]
    # 一行ずつ並んだ一覧表。どの行も語が当たるので、件数だけで比べると必ず勝つ
    table = store.add_source(url="https://example.com/list", kind="fan_chronicle", title="魔女一覧")
    tv = store.add_version(table, text="一覧", title="魔女一覧")
    for i in range(40):
        store.add_claim(text=f"魔女一覧: 名前 第{i}の魔女 / 性質 不明 / 元の姿 不明",
                        source_version_id=tv.version_id, offset=i)

    got = {c["id"] for c in answer.retrieve(store, "人魚の魔女について教えて", max_claims=30)["claims"]}
    assert len(got & set(want)) >= 4
