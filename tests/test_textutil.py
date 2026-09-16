from cqs import textutil


def test_html_to_text_drops_scripts_and_keeps_title():
    html = (
        "<html><head><title>作品トップ</title><style>b{}</style>"
        "<script>alert(1)</script></head><body><nav>メニュー</nav>"
        "<h1>登場人物</h1><p>かぐや<br>彩葉</p></body></html>"
    )
    text, title = textutil.html_to_text(html)
    assert title == "作品トップ"
    assert "alert" not in text and "メニュー" not in text
    assert "かぐや" in text and "彩葉" in text


def test_clean_text_collapses_blank_lines():
    assert textutil.clean_text("a\n\n\n\nb") == "a\n\nb"


def test_split_sentences():
    s = textutil.split_sentences("かぐやは姫である。彩葉は高校生だ！短い")
    assert s == ["かぐやは姫である。", "彩葉は高校生だ！"]


def test_fts_expression_quotes_terms():
    assert textutil.fts_match_expression("かぐや 月の都") == '"かぐや" AND "月の都"'
    assert textutil.fts_match_expression("「酒寄 彩葉」") == '"酒寄 彩葉"'


def test_short_terms_are_split_out_for_like():
    # trigram は3文字未満を索引化しない。2文字の人名は LIKE 側に回す
    long_terms, short_terms = textutil.split_terms("かぐや 彩葉")
    assert long_terms == ["かぐや"] and short_terms == ["彩葉"]
    assert textutil.fts_match_expression("彩葉") is None


def test_normalize_query_folds_fullwidth():
    assert textutil.normalize_query("ＡＢＣ１２３") == "ABC123"


def test_excerpt_centers_on_needle():
    text = "あ" * 100 + "かぐや" + "い" * 100
    assert "かぐや" in textutil.excerpt(text, "かぐや", width=30)


def test_split_sentences_does_not_break_inside_brackets():
    # 作品名「超かぐや姫！」の「！」で切ってはいけない
    got = textutil.split_sentences("アニメ「超かぐや姫！」が配信された。次の文はこれ。")
    assert got == ["アニメ「超かぐや姫！」が配信された。", "次の文はこれ。"]


def test_join_wrapped_lines_reconnects_comma_endings():
    joined = textutil.join_wrapped_lines("17歳の女子高生・彩葉は、\n多忙な日々を送っていた。\nかぐや")
    assert joined == "17歳の女子高生・彩葉は、多忙な日々を送っていた。\nかぐや"


def test_join_wrapped_lines_keeps_bullets_separate():
    joined = textutil.join_wrapped_lines("以上を踏まえると、\n・7/11〜9/22の出来事\n・9/12まで一緒にいた")
    assert joined.split("\n") == ["以上を踏まえると、", "・7/11〜9/22の出来事", "・9/12まで一緒にいた"]


def test_terminator_inside_a_sentence_does_not_split():
    got = textutil.split_sentences("プロゲーマーとFPS？でガチンコ対決した。次の文はこれ。")
    assert got == ["プロゲーマーとFPS？でガチンコ対決した。", "次の文はこれ。"]


def test_headings_are_marked_from_the_html():
    """見出しかどうかを行の長さから推し量ると、長い見出しを本文と取り違える。"""
    html = (
        "<dl><dt>此岸の魔女</dt><dd>ゲーム版における暁美ほむらが魔女化した存在。</dd>"
        "<dt><span class='anchor'></span>おめかしの魔女 / キャンデロロ（Candeloro）</dt>"
        "<dd>巴マミが魔女化した存在。性質は「ご招待」。</dd></dl>"
    )
    text, _title = textutil.html_to_text(html)
    assert "## 此岸の魔女" in text
    # 見出しの中に別のタグが入っていても、1行の見出しにまとまること
    assert "## おめかしの魔女 / キャンデロロ（Candeloro）" in text


def test_箇条書きの項目は見出しにならない():
    """短いリンク一覧が節見出しとして読まれると、下の本文が無関係な語に紐づく。"""
    html = "<ul><li>手順を説明します。</li></ul>"
    assert "・手順を説明します。" in textutil.html_to_text(html)[0]


def test_リンクだけの箇条書き項目は落とす():
    """「おすすめ記事」のような回遊用リンク一覧は本文ではない。"""
    html = (
        '<ul><li><a href="/a">トップページ</a></li>'
        '<li><a href="/b">リセマラ当たり</a></li></ul>'
        "<p>ここが本文である。</p>"
    )
    text, _ = textutil.html_to_text(html)
    assert "トップページ" not in text
    assert "リセマラ当たり" not in text
    assert "ここが本文である。" in text


def test_リンクの外に文字がある項目は残す():
    html = '<ul><li>開発 - <a href="/q">QualiArts</a></li></ul>'
    text, _ = textutil.html_to_text(html)
    assert "開発 - QualiArts" in text.replace("\n", " ").replace("・", "")
