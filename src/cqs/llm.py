"""LLMの呼び出し。特定の提供元に縛らない。

回答生成・質問の計画・画像の読み取りでLLMを使う。呼び出しをここ1か所にまとめ、
**OpenAI互換のAPI**（OpenAI本体のほか、LM Studio・Ollama・vLLM・各種ホスティング）と
**AnthropicのMessages API**のどちらでも動くようにしている。

どちらも標準ライブラリのHTTPで叩くので、追加の依存は要らない。
検索・取り込み側はLLMを使わないので、ここが未設定でもシステムは動く
（回答生成の代わりに、貼れるプロンプトを出す）。

設定は環境変数で行う。

    CQS_LLM_PROVIDER   openai | anthropic | none（省略時は下の鍵から自動で決める）
    CQS_LLM_BASE_URL   APIの基点。OpenAI互換のサーバを指す場合はここを変える
                       （例: http://localhost:1234/v1、http://localhost:11434/v1）
    CQS_LLM_API_KEY    APIキー。OPENAI_API_KEY / ANTHROPIC_API_KEY でもよい
    CQS_MODEL          モデル名

ローカルのサーバはキーを要らないことが多いので、CQS_LLM_BASE_URL だけでも動く。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Sequence

OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-opus-5"}
TIMEOUT = float(os.environ.get("CQS_LLM_TIMEOUT", "180"))


class LLMError(RuntimeError):
    """LLMを呼べなかった／呼んだが失敗した。"""


@dataclass(frozen=True)
class Provider:
    name: str          # "openai" | "anthropic"
    base_url: str
    api_key: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.base_url}, {self.model})"


def _env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    return ""


def provider() -> Provider | None:
    """環境変数から、どこへ何で問い合わせるかを決める。設定が無ければ None。"""
    choice = _env("CQS_LLM_PROVIDER").lower()
    if choice == "none":
        return None

    base = _env("CQS_LLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE")
    openai_key = _env("CQS_LLM_API_KEY", "OPENAI_API_KEY")
    anthropic_key = _env("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    if not choice:
        # 明示が無ければ、鍵か基点があるほうを使う。
        # ローカルのサーバは鍵を要らないことが多いので、基点だけでもOpenAI互換とみなす。
        if base or openai_key:
            choice = "openai"
        elif anthropic_key:
            choice = "anthropic"
        else:
            return None

    if choice == "anthropic":
        key = _env("CQS_LLM_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
        if not key:
            return None
        return Provider("anthropic", (base or ANTHROPIC_DEFAULT_BASE).rstrip("/"), key,
                        _env("CQS_MODEL") or DEFAULT_MODELS["anthropic"])
    if choice == "openai":
        return Provider("openai", (base or OPENAI_DEFAULT_BASE).rstrip("/"),
                        openai_key or _env("ANTHROPIC_API_KEY"),
                        _env("CQS_MODEL", "OPENAI_MODEL") or DEFAULT_MODELS["openai"])
    raise LLMError(f"未知の CQS_LLM_PROVIDER: {choice}")


def available() -> bool:
    try:
        return provider() is not None
    except LLMError:
        return False


def describe() -> str:
    p = provider()
    return p.label if p else "未設定"


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"APIエラー ({e.code}): {body}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"接続エラー: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise LLMError(f"応答をJSONとして読めませんでした: {e}") from e


# --- 本文の生成 --------------------------------------------------------------

def _openai_content(text: str, images: Sequence[tuple[str, bytes]]) -> Any:
    if not images:
        return text
    parts: list[dict] = []
    for media, data in images:
        b64 = base64.standard_b64encode(data).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{b64}"}})
    parts.append({"type": "text", "text": text})
    return parts


def _anthropic_content(text: str, images: Sequence[tuple[str, bytes]]) -> Any:
    if not images:
        return text
    parts: list[dict] = []
    for media, data in images:
        parts.append({"type": "image", "source": {
            "type": "base64", "media_type": media,
            "data": base64.standard_b64encode(data).decode("ascii")}})
    parts.append({"type": "text", "text": text})
    return parts


def chat(
    user: str,
    *,
    system: str | None = None,
    images: Sequence[tuple[str, bytes]] = (),
    max_tokens: int = 4000,
    model: str | None = None,
    prov: Provider | None = None,
) -> tuple[str, str]:
    """1往復だけ問い合わせ、(本文, 実際に使ったモデル名) を返す。

    images は (media_type, バイト列) の並び。画像を渡せるかは提供元とモデル次第。
    """
    p = prov or provider()
    if p is None:
        raise LLMError("LLMの設定がありません（CQS_LLM_API_KEY / OPENAI_API_KEY / "
                       "ANTHROPIC_API_KEY / CQS_LLM_BASE_URL のいずれか）")
    model = model or p.model

    if p.name == "anthropic":
        payload: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": _anthropic_content(user, images)}],
        }
        if system:
            payload["system"] = system
        headers = {"x-api-key": p.api_key, "anthropic-version": ANTHROPIC_VERSION}
        got = _post(f"{p.base_url}/messages", payload, headers)
        if got.get("stop_reason") == "refusal":
            raise LLMError("モデルが応答を拒否しました。")
        text = "".join(b.get("text", "") for b in got.get("content", [])
                       if b.get("type") == "text")
        return text.strip(), got.get("model", model)

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": _openai_content(user, images)})
    payload = {"model": model, "max_completion_tokens": max_tokens, "messages": messages}
    headers = {"Authorization": f"Bearer {p.api_key}"} if p.api_key else {}
    try:
        got = _post(f"{p.base_url}/chat/completions", payload, headers)
    except LLMError as e:
        # max_completion_tokens を知らない古い実装・互換サーバ向けの取り直し
        if "max_completion_tokens" not in str(e):
            raise
        payload.pop("max_completion_tokens")
        payload["max_tokens"] = max_tokens
        got = _post(f"{p.base_url}/chat/completions", payload, headers)
    choices = got.get("choices") or []
    if not choices:
        raise LLMError(f"応答に choices がありません: {str(got)[:300]}")
    text = (choices[0].get("message") or {}).get("content") or ""
    if isinstance(text, list):     # 一部の互換サーバは配列で返す
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    return text.strip(), got.get("model", model)


def chat_json(user: str, *, max_tokens: int = 800, model: str | None = None) -> Any:
    """JSONだけを返させ、取り出す。取り出せなければ None。

    モデルが前置きを付けることがあるので、最初の [ か { から末尾の対応する括弧までを拾う。
    """
    try:
        text, _ = chat(user, max_tokens=max_tokens, model=model)
    except LLMError:
        return None
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None
