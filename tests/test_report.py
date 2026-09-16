from cqs import report


def test_parse_inline_markdown_link():
    items = report.parse_report("- かぐやは月から来た。([公式](https://example.com/a))")
    assert items[0].claim_text == "かぐやは月から来た。"
    assert items[0].url == "https://example.com/a"


def test_parse_footnote_reference():
    items = report.parse_report("彩葉は17歳の高校生である。[1]\n\n[1]: https://example.com/b")
    assert items[0].url == "https://example.com/b"


def test_claims_without_url_are_kept_as_candidates():
    items = report.parse_report("出典の無い主張がここにある。")
    assert items[0].url is None
    assert "URLに結び付かなかった" in items[0].note


def test_match_score_detects_present_and_missing_tokens():
    s = report.match_score("彩葉は17歳の高校生である。", "酒寄彩葉は17歳の高校生。")
    assert s["score"] == 1.0
    s2 = report.match_score("彩葉は宇宙飛行士である。", "酒寄彩葉は高校生。")
    assert s2["score"] < 1.0 and "宇宙飛行士" in s2["missing"]


def test_web_query_survives_raw_utf8_in_the_request_line():
    """リクエスト行は latin-1 として読まれる。日本語の作品名が文字化けしないこと。"""
    from cqs.web.app import Handler

    assert Handler._repair("work=%E8%A9%A6%E4%BD%9C%E5%93%81") == "work=%E8%A9%A6%E4%BD%9C%E5%93%81"
    mojibake = "work=試作品".encode("utf-8").decode("latin-1")
    assert Handler._repair(mojibake) == "work=試作品"
