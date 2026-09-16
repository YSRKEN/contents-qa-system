"""原文から主張を言い直すところの検査。

本物のAPIは叩けないので、OpenAI互換の応答をまねる小さなサーバを立てて確かめる。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cqs import restate


SCENE = (
    "## 第17話：センパイが選んだアイドル\n"
    "そこへ冷ややかな声が響きます。\n"
    "「まさか、まだ諦めず、惨めにしがみついているとは……」\n"
    "「……ただ諦めが悪いだけ。\n"
    "アイドルとしての才能は欠片もない。\n"
    "あなたはなぜこれを選んだのですか？」\n"
    "極月学園の1年生。白草四音。\n"
)


class _Fake(BaseHTTPRequestHandler):
    reply = "[]"
    prompts: list[str] = []

    def log_message(self, *a):
        pass

    def do_POST(self):                # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Fake.prompts.append(json.dumps(body, ensure_ascii=False))
        out = {"choices": [{"message": {"role": "assistant", "content": _Fake.reply}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def fake_llm(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CQS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("CQS_LLM_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setenv("CQS_LLM_API_KEY", "test")
    monkeypatch.setenv("CQS_MODEL", "fake-1")
    _Fake.prompts.clear()
    yield _Fake
    srv.shutdown()


def _version(store):
    store.ensure_entity("白草四音", kind="character")
    store.ensure_entity("葛城リーリヤ", kind="character")
    sid = store.add_source(url="https://example.com/step2", kind="fan_chronicle", title="振り返り")
    return store.add_version(sid, text=SCENE, title="振り返り").version_id


def test_主語の無いセリフが独立して読める主張になる(store, fake_llm):
    fake_llm.reply = json.dumps([{
        "text": "白草四音は葛城リーリヤに「アイドルとしての才能は欠片もない」と侮辱した。",
        "locator": "アイドルとしての才能は欠片もない。",
        "entities": ["白草四音", "葛城リーリヤ"],
    }], ensure_ascii=False)
    v = _version(store)
    got = restate.restate_version(store, v)
    assert len(got) == 1
    assert got[0]["entities"] == ["白草四音", "葛城リーリヤ"]
    # 原文中の位置が付き、並び順に使える
    assert SCENE[got[0]["offset"]:].startswith("アイドルとしての才能は欠片もない。")


def test_原文に無い根拠を返してきたら捨てる(store, fake_llm):
    """言い直しはLLMに任せるが、作り話は入れない。"""
    fake_llm.reply = json.dumps([
        {"text": "白草四音は葛城リーリヤを殴った。", "locator": "四音はリーリヤを殴った。",
         "entities": ["白草四音"]},
        {"text": "白草四音は極月学園の1年生である。", "locator": "極月学園の1年生。",
         "entities": ["白草四音"]},
    ], ensure_ascii=False)
    got = restate.restate_version(store, _version(store))
    assert [c["text"] for c in got] == ["白草四音は極月学園の1年生である。"]


def test_根拠は改行をまたいでいても原文から探せる(store, fake_llm):
    fake_llm.reply = json.dumps([{
        "text": "白草四音は葛城リーリヤの選択そのものを否定した。",
        "locator": "アイドルとしての才能は欠片もない。 あなたはなぜこれを選んだのですか？",
        "entities": ["白草四音"],
    }], ensure_ascii=False)
    got = restate.restate_version(store, _version(store))
    assert len(got) == 1 and got[0]["offset"] > 0


def test_登録すると確認状態は出典種別から決まる(store, fake_llm):
    """言い直しはLLMに任せても、確からしさの判断は機械的規則のまま。"""
    fake_llm.reply = json.dumps([{
        "text": "白草四音は極月学園の1年生である。", "locator": "極月学園の1年生。",
        "entities": ["白草四音"]}], ensure_ascii=False)
    v = _version(store)
    ids = restate.register_restated(store, v, restate.restate_version(store, v))
    c = store.get_claim(ids[0])
    assert c["verification"] == "secondhand"      # fan_chronicle → 伝聞
    assert c["note"] == restate.NOTE


def test_登録済みのエンティティ名だけを対象にする(store, fake_llm):
    fake_llm.reply = json.dumps([{
        "text": "白草四音は極月学園の1年生である。", "locator": "極月学園の1年生。",
        "entities": ["白草四音", "存在しない人"]}], ensure_ascii=False)
    got = restate.restate_version(store, _version(store))
    assert got[0]["entities"] == ["白草四音"]
