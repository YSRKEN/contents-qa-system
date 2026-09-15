import pytest

from cqs import fetch


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://x.com/Cho_KaguyaHime", ("profile", "Cho_KaguyaHime", "")),
        ("https://twitter.com/Cho_KaguyaHime/", ("profile", "Cho_KaguyaHime", "")),
        ("https://x.com/Cho_KaguyaHime/status/2046968215088369935",
         ("status", "Cho_KaguyaHime", "2046968215088369935")),
        ("https://mobile.twitter.com/foo/statuses/12345", ("status", "foo", "12345")),
        ("https://x.com/home", None),          # 予約パスはプロフィールにしない
        ("https://example.com/a", None),
    ],
)
def test_x_target(url, expected):
    assert fetch.x_target(url) == expected


def test_format_tweet_includes_author_body_and_quote():
    tweet = {
        "text": "復活上映が決定しました。",
        "created_at": "Wed Jul 15 12:01:00 +0000 2026",
        "author": {"name": "公式", "screen_name": "Cho_KaguyaHime"},
        "likes": 10,
        "quote": {"text": "元の投稿", "author": {"name": "誰か", "screen_name": "someone"}},
    }
    out = fetch._format_tweet(tweet)
    assert "@Cho_KaguyaHime" in out
    assert "復活上映が決定しました。" in out
    assert "［引用元］" in out and "元の投稿" in out
    assert "いいね 10" in out


def test_fetch_x_rejects_non_x_url():
    with pytest.raises(fetch.FetchError, match="X のURL"):
        fetch.fetch_x("https://example.com/a")


def test_env_flag_reads_falsey_values(monkeypatch):
    monkeypatch.setenv("CQS_TEST_FLAG", "0")
    assert fetch._env_flag("CQS_TEST_FLAG", True) is False
    monkeypatch.setenv("CQS_TEST_FLAG", "1")
    assert fetch._env_flag("CQS_TEST_FLAG", False) is True
    monkeypatch.delenv("CQS_TEST_FLAG")
    assert fetch._env_flag("CQS_TEST_FLAG", True) is True


def test_render_snapshot_handles_fxtwitter_json():
    """Xの版はJSONを保存しているので、HTMLとして解釈してはいけない。"""
    raw = (
        '{"code":200,"status":{"id":"1","text":"復活上映が決定しました。",'
        '"created_at":"Wed Jul 15 12:01:00 +0000 2026",'
        '"author":{"name":"公式","screen_name":"Cho_KaguyaHime"}}}'
    )
    text, title = fetch.render_snapshot(raw)
    assert "復活上映が決定しました。" in text
    assert "{" not in text
    assert title == "@Cho_KaguyaHime の投稿 1"


def test_render_snapshot_handles_html():
    text, title = fetch.render_snapshot("<html><head><title>作品</title></head><body><p>本文。</p></body></html>")
    assert text == "本文。" and title == "作品"
