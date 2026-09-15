"""ローカル用のブラウザUI。標準ライブラリだけで動かす。

既定で 127.0.0.1 にのみ待ち受ける。自分専用の前提なので認証は持たない。
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import answer as answer_mod
from .. import config, ingest
from ..constants import SOURCE_KINDS, VERIFICATIONS

STATIC = Path(__file__).parent / "static"


def _work(params: dict[str, list[str]]) -> Any:
    slug = (params.get("work") or [""])[0]
    if not slug:
        raise ValueError("work が指定されていません")
    return config.open_work(slug)


def _one(params: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    v = params.get(key)
    return v[0] if v and v[0] != "" else default


def _int(params: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(_one(params, key) or default)
    except ValueError:
        return default


class Handler(BaseHTTPRequestHandler):
    server_version = "cqs"

    def log_message(self, fmt: str, *args: Any) -> None:  # 既定のアクセスログを抑える
        pass

    # --- 応答ヘルパ ---
    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # --- ルーティング ---
    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
            if u.path == "/app.js":
                return self._send_file(STATIC / "app.js", "text/javascript; charset=utf-8")
            if u.path == "/style.css":
                return self._send_file(STATIC / "style.css", "text/css; charset=utf-8")
            if u.path.startswith("/api/"):
                return self._send(self._api_get(u.path[5:], q))
            self.send_error(404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, 400)

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        try:
            if u.path.startswith("/api/"):
                return self._send(self._api_post(u.path[5:], self._body()))
            self.send_error(404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, 400)

    # --- API ---
    def _api_get(self, name: str, q: dict[str, list[str]]) -> Any:
        if name == "meta":
            return {
                "kinds": {k: v["label"] for k, v in SOURCE_KINDS.items()},
                "verifications": VERIFICATIONS,
                "data_dir": str(config.data_dir()),
            }
        if name == "works":
            return {"works": config.list_works()}
        with _work(q) as st:
            if name == "stats":
                return st.stats()
            if name == "claims":
                return {
                    "claims": st.search_claims(
                        query=_one(q, "query"),
                        entity=_one(q, "entity"),
                        kind=_one(q, "kind"),
                        verification=_one(q, "verification"),
                        status=None if _one(q, "all") else "active",
                        limit=_int(q, "limit", 50),
                    )
                }
            if name == "sources":
                if _one(q, "query"):
                    return {"results": st.search_sources(_one(q, "query"), kind=_one(q, "kind"), limit=_int(q, "limit", 15))}
                return {"sources": st.list_sources(kind=_one(q, "kind"))}
            if name == "source":
                return st.source_excerpt(_int(q, "id", 0), offset=_int(q, "offset", 0), length=_int(q, "length", 3000)) or {
                    "error": "見つかりません"
                }
            if name == "entities":
                return {"entities": st.list_entities()}
            if name == "related":
                return {"related": st.related_entities(_one(q, "name") or "")}
            if name == "candidates":
                s = _one(q, "status", "pending")
                return {"candidates": st.list_candidates(status=None if s == "all" else s, limit=_int(q, "limit", 200))}
        raise ValueError(f"未知のAPI: {name}")

    def _api_post(self, name: str, body: dict) -> Any:
        if name == "work":
            st = config.create_work(body["title"], slug=body.get("slug") or None, note=body.get("note", ""))
            meta = {"path": str(st.path), **st.meta}
            st.close()
            return meta
        work = body.get("work")
        if not work:
            raise ValueError("work が指定されていません")
        with config.open_work(work) as st:
            if name == "fetch":
                return ingest.ingest_url(st, body["url"], kind=body.get("kind") or None)
            if name == "document":
                return ingest.ingest_text(
                    st, body["text"], kind=body.get("kind", "manual"), title=body["title"],
                    url=body.get("url") or None, note=body.get("note") or None,
                )
            if name == "report":
                return ingest.ingest_ai_report(st, body["text"], title=body["title"])
            if name == "verify":
                return {
                    "results": ingest.verify_candidates(
                        st,
                        candidate_ids=body.get("ids") or None,
                        limit=int(body.get("limit", 10)),
                        promote_threshold=body.get("promote"),
                    )
                }
            if name == "claim":
                cid = st.add_claim(
                    text=body["text"],
                    source_version_id=body.get("source_version_id") or None,
                    verification=body.get("verification") or None,
                    entities=body.get("entities") or (),
                    locator=body.get("locator") or None,
                    note=body.get("note") or None,
                    supersedes=body.get("supersedes") or None,
                )
                return {"claim_id": cid, "claim": st.get_claim(cid)}
            if name == "claim_verification":
                st.set_verification(int(body["claim_id"]), body["verification"], note=body.get("note"))
                return {"ok": True}
            if name == "claim_link":
                st.link_claims(int(body["from"]), int(body["to"]), body["type"], note=body.get("note"))
                return {"ok": True}
            if name == "claim_retract":
                st.retract_claim(int(body["claim_id"]), note=body.get("note"))
                return {"ok": True}
            if name == "entity":
                eid = st.ensure_entity(
                    body["name"], kind=body.get("kind") or None,
                    aliases=body.get("aliases") or (), note=body.get("note") or None,
                )
                return {"entity_id": eid}
            if name == "propose":
                return {
                    "candidates": ingest.propose_claims(
                        st, int(body["source_version_id"]), entities=body.get("entities") or (),
                        limit=int(body.get("limit", 80)),
                    )
                }
            if name == "refetch":
                return {"results": ingest.refetch_sources(st, only_periodic=not body.get("all"))}
            if name == "rule":
                st.set_kind_rule(body["pattern"], body["kind"])
                return {"rules": st.kind_rules()}
            if name == "ask":
                return answer_mod.answer(
                    st, body["question"], model=body.get("model") or None, use_llm=bool(body.get("use_llm", True))
                )
        raise ValueError(f"未知のAPI: {name}")


def serve(*, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"作品QAシステム: {url}")
    print(f"作品DB: {config.data_dir()}")
    print("終了は Ctrl-C")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します")
    finally:
        httpd.server_close()
