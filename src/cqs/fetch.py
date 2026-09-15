"""URL取得。原文層に版を積むための最小限のクローラ。

個人利用でも相手サイトに負荷をかけないよう、既定で robots.txt を見て
同一ホストへの連続アクセスに間隔を空ける。
"""

from __future__ import annotations

import gzip
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
    for enc in [charset, "utf-8", "cp932", "euc-jp"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


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
    respect_robots: bool = True,
) -> FetchResult:
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
            raw = resp.read()
            status = resp.status
            final_url = resp.geturl()
            content_type = resp.headers.get("Content-Type", "")
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}: {url}") from e
    except Exception as e:
        raise FetchError(f"取得に失敗しました ({type(e).__name__}): {url}: {e}") from e

    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)

    body = _decode(raw, content_type)
    if "html" in content_type.lower() or body.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        text, title = textutil.html_to_text(body)
    else:
        text, title = textutil.clean_text(body), None
    if not text:
        raise FetchError(f"本文を取り出せませんでした: {url}")
    return FetchResult(url, final_url, status, body, text, title, content_type)
