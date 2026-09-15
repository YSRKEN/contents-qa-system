"""記事に貼られた画像の中身を取り込む。

表・カレンダー・図が画像で置かれていると、HTMLからは代替テキストしか取れない。
実際、作中の時系列をまとめた記事では、カレンダーと詳細な時系列表がどちらも画像だった。

ここでは3段階を分けている。
1. 保存済みの生HTMLから、本文に属する画像のURLを拾う（`list_images`）
2. 画像を取得して保存する（`download`）
3. 画像を読み取って文字にし、原文層へ「画像の読み取り」として入れる（`transcribe`）

3は機械が画像を読む工程なので、確認状態は常に「伝聞」になる。
"""

from __future__ import annotations

import base64
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import fetch
from .store import StoreError, WorkStore

# 本文の内容ではない画像（アイコン・ボタン・関連記事のサムネイルなど）
_CHROME = re.compile(
    r"(profile-image|/image/square/|entry-button|favicon|sprite|spacer|blank|"
    r"avatar|logo|badge|banner|/ads?/|doubleclick|analytics|1x1|pixel)",
    re.I,
)
_CONTENT_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_BYTES = 20 * 1024 * 1024

DEFAULT_PROMPT = """この画像は、ある作品についての記事に貼られたものです。
画像に書かれている内容を、後から検索できる形の日本語テキストに書き起こしてください。

- 表やカレンダーなら、行ごとに「項目: 値 / 項目: 値」の形で全行を書き出す。
- 図や地図なら、読み取れる文字と、要素どうしの関係を文で書く。
- 画像に書かれていないことは補わない。読めない箇所は「（判読不能）」と書く。
- 説明や前置きは書かず、書き起こしだけを返す。"""


@dataclass
class ImageRef:
    url: str
    alt: str
    width: str | None = None
    height: str | None = None


def list_images(store: WorkStore, source_version_id: int, *, include_chrome: bool = False) -> list[ImageRef]:
    """保存済みの生HTMLから、本文に属する画像を拾う。"""
    raw = store.get_raw_html(source_version_id)
    if not raw:
        return []
    version = store.get_version(source_version_id) or {}
    base = version.get("url") or ""
    out: list[ImageRef] = []
    seen: set[str] = set()
    for tag in re.findall(r"<img[^>]+>", raw, re.I):
        src = re.search(r'\bsrc="([^"]+)"', tag) or re.search(r"\bsrc='([^']+)'", tag)
        if not src:
            continue
        url = urllib.parse.urljoin(base, src.group(1).strip().replace("&amp;", "&"))
        if url in seen or url.startswith("data:"):
            continue
        if not include_chrome and _CHROME.search(url):
            continue
        attr = lambda name: (re.search(rf'\b{name}="([^"]*)"', tag) or [None, None])[1]  # noqa: E731
        w, h = attr("width"), attr("height")
        if not include_chrome and w and h and w.isdigit() and h.isdigit() and int(w) < 200 and int(h) < 200:
            continue
        seen.add(url)
        out.append(ImageRef(url=url, alt=(attr("alt") or "").strip(), width=w, height=h))
    return out


def download(url: str, *, timeout: int = 30) -> tuple[bytes, str]:
    """画像を取得して (バイト列, MIMEタイプ) を返す。"""
    req = urllib.request.Request(url, headers={"User-Agent": fetch.DEFAULT_UA, "Accept": "image/*,*/*"})
    fetch._throttle(url, fetch.DEFAULT_DELAY)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        media = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise StoreError(f"画像が大きすぎます（{MAX_BYTES // 1024 // 1024}MB超）: {url}")
    if media not in _CONTENT_TYPES:
        raise StoreError(f"画像として扱えない形式です（{media or '不明'}）: {url}")
    return data, media


def save_all(store: WorkStore, source_version_id: int, out_dir: str | Path) -> list[dict]:
    """本文の画像をまとめて保存する（自分の目で読む、別の道具に渡す、のいずれにも使える）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for i, ref in enumerate(list_images(store, source_version_id), 1):
        try:
            data, media = download(ref.url)
        except Exception as e:
            results.append({"url": ref.url, "error": str(e)})
            continue
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}[media]
        path = out / f"v{source_version_id}-{i:02d}{ext}"
        path.write_bytes(data)
        results.append({"url": ref.url, "alt": ref.alt, "path": str(path), "bytes": len(data)})
    return results


def store_transcript(
    store: WorkStore,
    source_version_id: int,
    *,
    image_url: str,
    text: str,
    note: str | None = None,
) -> dict:
    """読み取った文字を原文層に「画像の読み取り」として入れる。

    元の記事とは別の出典として持ち、どの版のどの画像から来たかを note に残す。
    """
    from . import ingest

    parent = store.get_version(source_version_id) or {}
    title = f"{(parent.get('source_title') or '（無題）')[:48]} の画像"
    detail = f"source_version {source_version_id} に貼られた画像の読み取り。元記事: {parent.get('url') or '-'}"
    return ingest.ingest_text(
        store, text, kind="image_transcript", title=title, url=image_url,
        note=f"{detail}{(' / ' + note) if note else ''}",
    )


def transcribe(
    store: WorkStore,
    source_version_id: int,
    *,
    model: str | None = None,
    prompt: str = DEFAULT_PROMPT,
    limit: int = 20,
) -> list[dict]:
    """画像を取得し、Claudeに読み取らせて原文層に入れる。

    画像を読む工程なので APIキー（ANTHROPIC_API_KEY）が要る。
    キーが無い環境では `save_all` で画像を書き出し、読み取り結果を
    `store_transcript` や `cqs add-text --kind image_transcript` で入れればよい。
    """
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise StoreError(
            "anthropic パッケージが必要です（pip install 'contents-qa-system[llm]'）"
        ) from e
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise StoreError("ANTHROPIC_API_KEY が未設定です")

    client = anthropic.Anthropic()
    model = model or os.environ.get("CQS_MODEL", "claude-opus-5")
    results: list[dict] = []
    for ref in list_images(store, source_version_id)[:limit]:
        try:
            data, media = download(ref.url)
        except Exception as e:
            results.append({"url": ref.url, "error": str(e)})
            continue
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": media,
                            "data": base64.standard_b64encode(data).decode("ascii")}},
                        {"type": "text", "text": prompt + (f"\n\n（代替テキスト: {ref.alt}）" if ref.alt else "")},
                    ],
                }],
            )
        except anthropic.APIStatusError as e:
            results.append({"url": ref.url, "error": f"APIエラー ({e.status_code}): {e.message}"})
            continue
        if resp.stop_reason == "refusal":
            results.append({"url": ref.url, "error": "モデルが読み取りを拒否しました"})
            continue
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        if not text:
            results.append({"url": ref.url, "error": "読み取り結果が空でした"})
            continue
        rec = store_transcript(store, source_version_id, image_url=ref.url, text=text,
                              note=f"読み取りモデル: {resp.model}")
        results.append({"url": ref.url, "alt": ref.alt, "chars": len(text), **rec})
    return results
