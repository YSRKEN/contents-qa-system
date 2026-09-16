"""LLM呼び出しの検査。

本物のAPIは叩けないので、OpenAI互換とAnthropicの応答をまねる小さなサーバを立てて確かめる。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cqs import llm


class _Fake(BaseHTTPRequestHandler):
    received: list[dict] = []
    reply_text = "こたえ"

    def log_message(self, *a):        # テスト中の標準エラー出力を抑える
        pass

    def do_POST(self):                # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Fake.received.append({"path": self.path, "body": body,
                               "auth": self.headers.get("Authorization"),
                               "key": self.headers.get("x-api-key")})
        if self.path.endswith("/chat/completions"):
            out = {"model": "fake-1", "choices": [
                {"message": {"role": "assistant", "content": _Fake.reply_text}}]}
        else:
            out = {"model": "fake-claude", "content": [
                {"type": "text", "text": _Fake.reply_text}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def server():
    _Fake.received = []
    _Fake.reply_text = "こたえ"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def _clear(monkeypatch):
    for k in ("CQS_LLM_PROVIDER", "CQS_LLM_BASE_URL", "CQS_LLM_API_KEY", "CQS_MODEL",
              "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_MODEL",
              "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def test_no_setting_means_no_llm(monkeypatch):
    """設定が無ければLLMは使わない。システム自体はそれで動く。"""
    _clear(monkeypatch)
    assert llm.provider() is None
    assert llm.available() is False


def test_openai_compatible_server(server, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CQS_LLM_BASE_URL", server)
    monkeypatch.setenv("CQS_LLM_API_KEY", "k-1")
    monkeypatch.setenv("CQS_MODEL", "my-model")

    text, used = llm.chat("しつもん", system="きまり", max_tokens=100)
    assert text == "こたえ"
    assert used == "fake-1"
    sent = _Fake.received[-1]
    assert sent["path"].endswith("/chat/completions")
    assert sent["auth"] == "Bearer k-1"
    assert sent["body"]["model"] == "my-model"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "きまり"}
    assert sent["body"]["messages"][1]["content"] == "しつもん"


def test_local_server_without_a_key(server, monkeypatch):
    """LM Studio や Ollama はキーを要らない。基点だけで動くこと。"""
    _clear(monkeypatch)
    monkeypatch.setenv("CQS_LLM_BASE_URL", server)
    p = llm.provider()
    assert p is not None and p.name == "openai"
    assert llm.chat("しつもん")[0] == "こたえ"
    assert _Fake.received[-1]["auth"] is None


def test_anthropic_messages_api(server, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CQS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("CQS_LLM_BASE_URL", server)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-2")

    text, used = llm.chat("しつもん", system="きまり")
    assert (text, used) == ("こたえ", "fake-claude")
    sent = _Fake.received[-1]
    assert sent["path"].endswith("/messages")
    assert sent["key"] == "k-2"
    assert sent["body"]["system"] == "きまり"


def test_images_are_sent_in_each_provider_shape(server, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CQS_LLM_BASE_URL", server)
    llm.chat("よんで", images=[("image/png", b"\x89PNG")])
    part = _Fake.received[-1]["body"]["messages"][0]["content"][0]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")

    monkeypatch.setenv("CQS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    llm.chat("よんで", images=[("image/png", b"\x89PNG")])
    part = _Fake.received[-1]["body"]["messages"][0]["content"][0]
    assert part["type"] == "image" and part["source"]["media_type"] == "image/png"


def test_json_is_taken_out_of_a_wordy_reply(server, monkeypatch):
    """前置きを付けて返すモデルがあるので、括弧の対で切り出す。"""
    _clear(monkeypatch)
    monkeypatch.setenv("CQS_LLM_BASE_URL", server)
    _Fake.reply_text = 'はい、こちらです:\n["巴マミ","美樹さやか"]\n以上です。'
    assert llm.chat_json("なにか") == ["巴マミ", "美樹さやか"]
    _Fake.reply_text = "JSONではない返事"
    assert llm.chat_json("なにか") is None


def test_answer_falls_back_to_a_pasteable_prompt(store, monkeypatch):
    """設定が無ければ、回答の代わりに貼れるプロンプトを出す。"""
    from cqs import answer

    _clear(monkeypatch)
    sid = store.add_source(url="https://example.com/a", kind="official_site", title="紹介")
    v = store.add_version(sid, text="かぐやは月から来た。", title="紹介")
    store.ensure_entity("かぐや")
    store.add_claim(text="かぐやは月から来た。", source_version_id=v.version_id, entities=["かぐや"])

    r = answer.answer(store, "かぐやはどこから来た？")
    assert r["answer"] is None
    assert "貼れば" in r["reason"]
    assert "かぐやは月から来た。" in r["prompt"]["user"]


def test_answer_uses_an_openai_compatible_server(server, store, monkeypatch):
    from cqs import answer

    _clear(monkeypatch)
    monkeypatch.setenv("CQS_LLM_BASE_URL", server)
    monkeypatch.setenv("CQS_MODEL", "my-model")
    sid = store.add_source(url="https://example.com/a", kind="official_site", title="紹介")
    v = store.add_version(sid, text="かぐやは月から来た。", title="紹介")
    store.ensure_entity("かぐや")
    store.add_claim(text="かぐやは月から来た。", source_version_id=v.version_id, entities=["かぐや"])

    r = answer.answer(store, "かぐやはどこから来た？", plan=False)
    assert r["answer"] == "こたえ"
    assert r["model"] == "fake-1"


def test_the_question_is_expanded_before_searching(server, store, monkeypatch):
    """質問の語と本文の語は噛み合わない。本文に出てきそうな語を足してから引くこと。"""
    from cqs import answer

    _clear(monkeypatch)
    monkeypatch.setenv("CQS_LLM_BASE_URL", server)
    sid = store.add_source(url="https://example.com/a", kind="fan_chronicle", title="時系列")
    v = store.add_version(sid, text="本文", title="時系列")
    store.ensure_entity("かぐや")
    want = store.add_claim(text="卒業ライブの夜は満月だった。", source_version_id=v.version_id)
    # 質問の語（月・日付）だけでは埋もれるよう、無関係な記述を厚めに入れる
    for i in range(60):
        store.add_claim(text=f"その月の日付に関する別の話題その{i}が記されている。",
                        source_version_id=v.version_id)

    q = "かぐやが月に帰った日は？"
    assert want not in [c["id"] for c in answer.retrieve(store, q, max_claims=20)["claims"]]

    _Fake.reply_text = '["卒業ライブ","満月"]'
    got = answer.search_terms(store, q)
    assert got == ["卒業ライブ", "満月"]
    assert want in [c["id"] for c in answer.retrieve(store, q, max_claims=20,
                                                     extra_terms=got)["claims"]]
