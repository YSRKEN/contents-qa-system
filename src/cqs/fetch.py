"""URL取得。原文層に版を積むための最小限のクローラ。

robots.txt の確認は既定で行わない（CQS_RESPECT_ROBOTS=1 で有効にできる）。
一方、同一ホストへの連続アクセスに間隔を空けるのは常に行う。相手サイトへの
負荷を決めるのは robots.txt の有無ではなくアクセス頻度のほうなので、
そちらは切れないようにしてある。

X（旧Twitter）は通常の取得ではほぼ本文が得られないため、FxTwitter API を経由する。
"""

from __future__ import annotations

import gzip
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass

from . import textutil

DEFAULT_UA = os.environ.get(
    "CQS_USER_AGENT", "contents-qa-system/0.1 (personal research bot)"
)
DEFAULT_TIMEOUT = 30
DEFAULT_DELAY = 1.5  # 同一ホストへの最短間隔（秒）


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


# 既定では robots.txt を確認しない。確認させたい場合は CQS_RESPECT_ROBOTS=1。
DEFAULT_RESPECT_ROBOTS = _env_flag("CQS_RESPECT_ROBOTS", False)

# FxTwitter（X の本文を JSON で返す公開API）
FXTWITTER_API = os.environ.get("CQS_FXTWITTER_API", "https://api.fxtwitter.com")
_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
_X_STATUS = re.compile(r"^/(?P<user>[A-Za-z0-9_]{1,15})/status(?:es)?/(?P<id>\d+)")
_X_PROFILE = re.compile(r"^/(?P<user>[A-Za-z0-9_]{1,15})/?$")
_X_RESERVED = {"i", "home", "search", "explore", "notifications", "messages", "settings"}

_last_access: dict[str, float] = {}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    html: str
    text: str
    title: str | None
    content_type: str


def _decode(raw: bytes, content_type: str) -> str:
    charset = None
    m = re.search(r"charset=([\w\-]+)", content_type, re.I)
    if m:
        charset = m.group(1)
    if not charset:
        head = raw[:4096].decode("ascii", "ignore")
        m = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
        if m:
            charset = m.group(1)
    for enc in _codecs_for(charset):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 宣言があるなら、その系統で読めない字だけを落とす。ここで無関係な符号化に
    # 乗り換えると、EUC-JPの本文がcp932として「成功」して全文が化ける。
    fallback = next(iter(_codecs_for(charset)), None) if charset else None
    return raw.decode(fallback or "utf-8", "replace")


# 宣言された符号化の別名と、同じ系統で範囲の広いもの。日本語のページは
# 宣言がEUC-JPでも、機種依存文字（NEC・IBM拡張）が混ざって厳密なEUC-JPでは読めない。
_CODEC_FAMILY = {
    "euc-jp": ["eucjp_ms", "euc_jis_2004", "euc_jp"],
    "eucjp": ["eucjp_ms", "euc_jis_2004", "euc_jp"],
    "x-euc-jp": ["eucjp_ms", "euc_jis_2004", "euc_jp"],
    "shift-jis": ["cp932", "shift_jis_2004", "shift_jis"],
    "shift_jis": ["cp932", "shift_jis_2004", "shift_jis"],
    "sjis": ["cp932", "shift_jis_2004", "shift_jis"],
    "x-sjis": ["cp932", "shift_jis_2004", "shift_jis"],
    "ms_kanji": ["cp932", "shift_jis_2004", "shift_jis"],
    "iso-2022-jp": ["iso2022_jp_2004", "iso2022_jp_3", "iso2022_jp"],
}


def _codecs_for(charset: str | None) -> list[str]:
    """試す符号化の順。宣言があればその系統だけ、無ければ日本語圏の定番を順に。"""
    if not charset:
        return ["utf-8", "cp932", "eucjp_ms"]
    key = charset.strip().lower().replace("_", "-")
    if key in ("utf8", "utf-8"):
        return ["utf-8"]
    return _CODEC_FAMILY.get(key) or _CODEC_FAMILY.get(key.replace("-", "_")) or [charset]


def _decompress_partial(raw: bytes, encoding: str) -> bytes:
    """打ち切られた gzip / deflate を、展開できるところまで展開する。"""
    wbits = 16 + zlib.MAX_WBITS if encoding == "gzip" else -zlib.MAX_WBITS
    d = zlib.decompressobj(wbits)
    try:
        return d.decompress(raw)
    except zlib.error:
        return b""


def _robots_allows(url: str, ua: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            rp.read()
        except Exception:
            _robots_cache[origin] = None  # 取得できない場合は判断材料なしとして通す
        else:
            _robots_cache[origin] = rp
    rp = _robots_cache[origin]
    if rp is None:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:
        return True


def _throttle(url: str, delay: float) -> None:
    host = urllib.parse.urlsplit(url).netloc
    last = _last_access.get(host)
    if last is not None:
        wait = delay - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_access[host] = time.monotonic()


def fetch(
    url: str,
    *,
    user_agent: str = DEFAULT_UA,
    timeout: int = DEFAULT_TIMEOUT,
    delay: float = DEFAULT_DELAY,
    respect_robots: bool | None = None,
) -> FetchResult:
    """URLを取得する。X（旧Twitter）のURLは FxTwitter API に振り替える。"""
    if respect_robots is None:
        respect_robots = DEFAULT_RESPECT_ROBOTS
    if x_target(url):
        return fetch_x(url, user_agent=user_agent, timeout=timeout, delay=delay)
    if respect_robots and not _robots_allows(url, user_agent):
        raise FetchError(f"robots.txt により取得が許可されていません: {url}")
    _throttle(url, delay)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            "Accept-Language": "ja,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            final_url = resp.geturl()
            content_type = resp.headers.get("Content-Type", "")
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            try:
                raw = resp.read()
            except http.client.IncompleteRead as e:
                # 途中で切られても、読めたぶんは原文層に入れる価値がある
                raw = e.partial
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}: {url}") from e
    except Exception as e:
        raise FetchError(f"取得に失敗しました ({type(e).__name__}): {url}: {e}") from e

    try:
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error):
        # 途中で切れた圧縮データは、読めるところまで展開する
        raw = _decompress_partial(raw, encoding)

    body = _decode(raw, content_type)
    if "html" in content_type.lower() or body.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        text, title = textutil.html_to_text(body)
    else:
        text, title = textutil.clean_text(body), None
    if not text:
        raise FetchError(f"本文を取り出せませんでした: {url}")
    return FetchResult(url, final_url, status, body, text, title, content_type)


# --- X（旧Twitter） ---------------------------------------------------------


def x_target(url: str) -> tuple[str, str, str] | None:
    """X のURLなら ('status', user, id) か ('profile', user, '') を返す。"""
    parts = urllib.parse.urlsplit(url)
    if parts.netloc.lower() not in _X_HOSTS:
        return None
    m = _X_STATUS.match(parts.path)
    if m:
        return ("status", m.group("user"), m.group("id"))
    m = _X_PROFILE.match(parts.path)
    if m and m.group("user").lower() not in _X_RESERVED:
        return ("profile", m.group("user"), "")
    return None


def _get_json(url: str, *, user_agent: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}: {url}") from e
    except Exception as e:
        raise FetchError(f"取得に失敗しました ({type(e).__name__}): {url}: {e}") from e


def _format_tweet(t: dict, *, prefix: str = "") -> str:
    author = t.get("author") or {}
    head = f"{prefix}{author.get('name', '')}（@{author.get('screen_name', '')}） {t.get('created_at', '')}"
    lines = [head.strip(), (t.get("text") or "").strip()]
    quote = t.get("quote")
    if quote:
        lines += ["", _format_tweet(quote, prefix="［引用元］")]
    media = ((t.get("media") or {}).get("all") or [])
    if media:
        lines += ["", "添付: " + ", ".join(m.get("type", "media") for m in media)]
    stats = [
        f"{label} {t[key]}"
        for label, key in (("リポスト", "reposts"), ("いいね", "likes"), ("表示", "views"))
        if t.get(key) is not None
    ]
    if stats:
        lines += ["", " / ".join(stats)]
    return "\n".join(x for x in lines if x is not None)


def fetch_x(
    url: str, *, user_agent: str = DEFAULT_UA, timeout: int = DEFAULT_TIMEOUT, delay: float = DEFAULT_DELAY
) -> FetchResult:
    """X の投稿・プロフィールを FxTwitter API 経由で取得する。

    X 本体は通常の取得だと本文を返さないため、本文・投稿者・投稿日時・引用元を
    JSON で返す FxTwitter を使う。取得できるのは公開投稿のみ。
    """
    target = x_target(url)
    if not target:
        raise FetchError(f"X のURLとして解釈できません: {url}")
    kind, user, tweet_id = target
    api = f"{FXTWITTER_API}/2/status/{tweet_id}" if kind == "status" else f"{FXTWITTER_API}/{user}"
    _throttle(api, delay)
    data = _get_json(api, user_agent=user_agent, timeout=timeout)
    if data.get("code") != 200:
        raise FetchError(f"FxTwitter が投稿を返しませんでした（code={data.get('code')}）: {url}")

    if kind == "status":
        # FxTwitter v2 は投稿本体を "status"（旧APIは "tweet"）に入れる
        tweet = data.get("status") or data.get("tweet") or {}
        if not tweet.get("text"):
            raise FetchError(f"投稿の本文が空でした: {url}")
        text = _format_tweet(tweet)
        author = (tweet.get("author") or data.get("author") or {}).get("screen_name", user)
        title = f"@{author} の投稿 {tweet_id}"
    else:
        u = data.get("user") or {}
        text = textutil.clean_text(
            f"{u.get('name', '')}（@{u.get('screen_name', user)}）\n\n"
            f"{u.get('description', '')}\n\n"
            f"所在地: {u.get('location') or '-'} / サイト: {(u.get('website') or {}).get('url', '-')}\n"
            f"フォロワー {u.get('followers', '-')} / 投稿数 {u.get('tweets', '-')} / 開設 {u.get('joined', '-')}"
        )
        title = f"@{u.get('screen_name', user)} プロフィール"

    text = textutil.clean_text(text)
    if not text:
        raise FetchError(f"本文を取り出せませんでした: {url}")
    return FetchResult(
        url=url, final_url=url, status=200, html=json.dumps(data, ensure_ascii=False, indent=2),
        text=text, title=title, content_type="application/json (fxtwitter)",
    )


def render_snapshot(raw: str, url: str | None = None) -> tuple[str, str | None]:
    """保存してある生データから本文を作り直す。

    原文層に入っている「生データ」はHTMLとは限らない。X はFxTwitterのJSONを保存しているので、
    HTMLとして解釈すると本文がJSONそのものになってしまう。中身を見て描画を選ぶ。
    """
    head = raw.lstrip()[:1]
    if head in ("{", "["):
        try:
            data = json.loads(raw)
        except ValueError:
            return textutil.clean_text(raw), None
        tweet = data.get("status") or data.get("tweet")
        if tweet:
            author = (tweet.get("author") or data.get("author") or {}).get("screen_name", "")
            return textutil.clean_text(_format_tweet(tweet)), f"@{author} の投稿 {tweet.get('id', '')}".strip()
        u = data.get("user")
        if u:
            text = textutil.clean_text(
                f"{u.get('name', '')}（@{u.get('screen_name', '')}）\n\n"
                f"{u.get('description', '')}\n\n"
                f"所在地: {u.get('location') or '-'} / サイト: {(u.get('website') or {}).get('url', '-')}\n"
                f"フォロワー {u.get('followers', '-')} / 投稿数 {u.get('tweets', '-')} / 開設 {u.get('joined', '-')}"
            )
            return text, f"@{u.get('screen_name', '')} プロフィール"
        return textutil.clean_text(raw), None
    return textutil.html_to_text(raw)
