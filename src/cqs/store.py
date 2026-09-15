"""作品DBに対するドメイン操作（原文層・知識層・候補）。

CLI / MCPサーバー / Web UI はすべてこの層を通す。
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import db, textutil
from .constants import (
    NON_CITABLE_KINDS,
    SOURCE_KINDS,
    VERIFICATION_HANDLING,
    default_refetch,
    default_verification,
    kind_label,
    kind_priority,
    verification_label,
)

# 回答時の並び順に使う確認状態のランク
_VERIF_RANK = {
    "official": 0,
    "article": 1,
    "secondhand": 2,
    "fan_interpretation": 3,
    "needs_recheck": 4,
    "unverified": 5,
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class StoreError(RuntimeError):
    pass


@dataclass
class VersionResult:
    version_id: int
    version_no: int
    changed: bool
    rechecked_claims: int = 0


class WorkStore:
    """1作品ぶんのDBファイルを扱う。"""

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self.conn = conn
        self.path = path

    # ---- 開閉 -------------------------------------------------------------
    @classmethod
    def open(cls, path: str | Path, *, create: bool = False) -> "WorkStore":
        p = Path(path)
        return cls(db.connect(p, create=create or not p.exists()), p)

    @classmethod
    def create(cls, path: str | Path, *, slug: str, title: str, note: str = "") -> "WorkStore":
        p = Path(path)
        conn = db.connect(p, create=True)
        db.init_work(conn, slug=slug, title=title, note=note)
        return cls(conn, p)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "WorkStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def meta(self) -> dict[str, str]:
        return db.get_meta(self.conn)

    def log(self, action: str, detail: Any = None) -> None:
        self.conn.execute(
            "INSERT INTO activity_log(at, action, detail) VALUES (?,?,?)",
            (now(), action, detail if isinstance(detail, str) or detail is None else json.dumps(detail, ensure_ascii=False)),
        )
        self.conn.commit()

    def stats(self) -> dict[str, Any]:
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        by_kind = {
            r["kind"]: r["n"]
            for r in self.conn.execute("SELECT kind, COUNT(*) n FROM sources GROUP BY kind")
        }
        by_verif = {
            r["verification"]: r["n"]
            for r in self.conn.execute(
                "SELECT verification, COUNT(*) n FROM claims WHERE status='active' GROUP BY verification"
            )
        }
        return {
            "title": self.meta.get("title", ""),
            "slug": self.meta.get("slug", ""),
            "sources": q("SELECT COUNT(*) FROM sources"),
            "versions": q("SELECT COUNT(*) FROM source_versions"),
            "claims_active": q("SELECT COUNT(*) FROM claims WHERE status='active'"),
            "claims_total": q("SELECT COUNT(*) FROM claims"),
            "entities": q("SELECT COUNT(*) FROM entities"),
            "candidates_pending": q("SELECT COUNT(*) FROM candidates WHERE status='pending'"),
            "sources_by_kind": by_kind,
            "claims_by_verification": by_verif,
        }

    # ---- 出典種別の自動判定ルール -----------------------------------------
    def kind_rules(self) -> list[list[str]]:
        """[URLに含まれる文字列, 出典種別] の並び。作品ごとにDBへ持つ。"""
        raw = self.meta.get("kind_rules")
        if not raw:
            return []
        try:
            return [list(x) for x in json.loads(raw)]
        except Exception:
            return []

    def set_kind_rule(self, pattern: str, kind: str) -> None:
        if kind not in SOURCE_KINDS:
            raise StoreError(f"未知の出典種別: {kind}")
        rules = [r for r in self.kind_rules() if r[0] != pattern]
        rules.append([pattern, kind])
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES ('kind_rules', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(rules, ensure_ascii=False),),
        )
        self.conn.commit()

    def guess_kind(self, url: str, *, default: str = "article") -> str:
        """URLから出典種別を推定する。作品固有のルール > 一般ルール > 既定。"""
        u = url.lower()
        for pattern, kind in self.kind_rules():
            if pattern.lower() in u:
                return kind
        for pattern, kind in (
            ("note.com", "fan_note"),
            ("wikipedia.org", "wiki_index"),
            ("wikiwiki.jp", "wiki_index"),
            ("seesaawiki.jp", "wiki_index"),
            ("fandom.com", "wiki_index"),
            # ホスト名では公式かどうか分からない。公式アカウントは作品ごとのルールで
            # アカウント名まで含めて指定する（例: "x.com/Cho_KaguyaHime" → official_sns）。
            ("x.com", "fan_note"),
            ("twitter.com", "fan_note"),
        ):
            if pattern in u:
                return kind
        return default

    # ---- 原文層 -----------------------------------------------------------
    def add_source(
        self,
        *,
        url: str | None,
        kind: str,
        title: str | None = None,
        note: str | None = None,
        refetch: str | None = None,
    ) -> int:
        if kind not in SOURCE_KINDS:
            raise StoreError(f"未知の出典種別: {kind}（有効: {', '.join(SOURCE_KINDS)}）")
        if url:
            row = self.conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()
            if row:
                return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO sources(url, kind, title, note, refetch, created_at) VALUES (?,?,?,?,?,?)",
            (url, kind, title, note, refetch or default_refetch(kind), now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def set_source_kind(self, source_id: int, kind: str) -> None:
        """出典の種別を変える。分類を誤って取り込んだときに使う。

        すでに登録済みの主張の確認状態は変えない（機械的に上書きすると人手の判断を潰すため）。
        必要なら set_verification で個別に直す。
        """
        if kind not in SOURCE_KINDS:
            raise StoreError(f"未知の出典種別: {kind}")
        if not self.get_source(source_id):
            raise StoreError(f"出典が見つかりません: {source_id}")
        self.conn.execute(
            "UPDATE sources SET kind = ?, refetch = ? WHERE id = ?",
            (kind, default_refetch(kind), source_id),
        )
        self.conn.commit()
        self.log("set_source_kind", {"source_id": source_id, "kind": kind})

    def get_source(self, source_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()

    def find_source_by_url(self, url: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sources WHERE url = ?", (url,)).fetchone()

    def list_sources(self, *, kind: str | None = None) -> list[dict]:
        sql = (
            "SELECT s.*, "
            " (SELECT COUNT(*) FROM source_versions v WHERE v.source_id = s.id) AS versions, "
            " (SELECT MAX(fetched_at) FROM source_versions v WHERE v.source_id = s.id) AS last_fetched "
            "FROM sources s"
        )
        args: list[Any] = []
        if kind:
            sql += " WHERE s.kind = ?"
            args.append(kind)
        sql += " ORDER BY s.id"
        return [self._source_row(r) for r in self.conn.execute(sql, args)]

    @staticmethod
    def _source_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["kind_label"] = kind_label(d["kind"])
        d["priority"] = kind_priority(d["kind"])
        return d

    def add_version(
        self,
        source_id: int,
        *,
        text: str,
        title: str | None = None,
        http_status: int | None = None,
        raw_html: str | None = None,
    ) -> VersionResult:
        """出典に新しい版を積む。本文が前版と同一ならスキップする。

        本文が変化した場合、その出典の過去版に紐づく有効な主張を「要再確認」に落とす。
        """
        text = textutil.clean_text(text)
        if not text:
            raise StoreError("本文が空です")
        h = textutil.content_hash(text)
        latest = self.latest_version(source_id)
        if latest and latest["content_hash"] == h:
            return VersionResult(int(latest["id"]), int(latest["version_no"]), changed=False)

        version_no = (int(latest["version_no"]) + 1) if latest else 1
        raw_gz = zlib.compress(raw_html.encode("utf-8")) if raw_html else None
        cur = self.conn.execute(
            "INSERT INTO source_versions(source_id, version_no, fetched_at, http_status, "
            "content_hash, title, text, raw_gz) VALUES (?,?,?,?,?,?,?,?)",
            (source_id, version_no, now(), http_status, h, title, text, raw_gz),
        )
        vid = int(cur.lastrowid)

        rechecked = 0
        if latest is not None:
            cur2 = self.conn.execute(
                "UPDATE claims SET verification='needs_recheck', updated_at=? "
                "WHERE status='active' AND verification <> 'needs_recheck' "
                "  AND source_version_id IN (SELECT id FROM source_versions WHERE source_id = ? AND id <> ?)",
                (now(), source_id, vid),
            )
            rechecked = cur2.rowcount or 0
        self.conn.commit()
        return VersionResult(vid, version_no, changed=True, rechecked_claims=rechecked)

    def latest_version(self, source_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM source_versions WHERE source_id = ? ORDER BY version_no DESC LIMIT 1",
            (source_id,),
        ).fetchone()

    def get_version(self, version_id: int) -> dict | None:
        r = self.conn.execute(
            "SELECT v.*, s.url, s.kind, s.title AS source_title, s.note AS source_note "
            "FROM source_versions v JOIN sources s ON s.id = v.source_id WHERE v.id = ?",
            (version_id,),
        ).fetchone()
        if not r:
            return None
        d = dict(r)
        d.pop("raw_gz", None)
        d["kind_label"] = kind_label(d["kind"])
        d["priority"] = kind_priority(d["kind"])
        return d

    def get_raw_html(self, version_id: int) -> str | None:
        r = self.conn.execute("SELECT raw_gz FROM source_versions WHERE id = ?", (version_id,)).fetchone()
        if not r or r["raw_gz"] is None:
            return None
        return zlib.decompress(r["raw_gz"]).decode("utf-8", "replace")

    def rederive_text(self, version_id: int) -> dict:
        """保存済みの生HTMLから本文を作り直す（再取得しない）。

        原文層が保存しているのは取得したHTMLそのもので、`text` はその描画結果にすぎない。
        表の扱いのように描画の仕方を直したときは、同じHTMLから作り直せば足りる。
        取得し直すわけではないので、取得日時と版番号は変えない。
        """
        from . import fetch

        raw = self.get_raw_html(version_id)
        if not raw:
            return {"version_id": version_id, "changed": False, "reason": "生データが保存されていません"}
        url = (self.get_version(version_id) or {}).get("url")
        text, title = fetch.render_snapshot(raw, url)
        if not text:
            return {"version_id": version_id, "changed": False, "reason": "本文を取り出せませんでした"}
        row = self.conn.execute(
            "SELECT text, title FROM source_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"出典版が見つかりません: {version_id}")
        if row["text"] == text:
            return {"version_id": version_id, "changed": False, "before": len(row["text"])}
        self.conn.execute(
            "UPDATE source_versions SET text = ?, content_hash = ?, title = COALESCE(?, title) WHERE id = ?",
            (text, textutil.content_hash(text), title, version_id),
        )
        self.conn.commit()
        self.log("rederive_text", {"version_id": version_id, "before": len(row["text"]), "after": len(text)})
        return {"version_id": version_id, "changed": True, "before": len(row["text"]), "after": len(text)}

    def source_excerpt(self, version_id: int, *, offset: int = 0, length: int = 2000) -> dict | None:
        """出典参照ツールの実体。版の本文を位置指定で切り出す。"""
        v = self.get_version(version_id)
        if not v:
            return None
        text: str = v.pop("text")
        offset = max(0, offset)
        v["total_length"] = len(text)
        v["offset"] = offset
        v["excerpt"] = text[offset : offset + length]
        v["has_more"] = offset + length < len(text)
        return v

    def search_sources(
        self, query: str, *, kind: str | None = None, limit: int = 10, latest_only: bool = True
    ) -> list[dict]:
        """原文層の全文検索。版ごとに1件、該当箇所の抜粋を返す。"""
        long_terms, short_terms = textutil.split_terms(query)
        match = textutil.match_expression_for(long_terms)
        args: list[Any] = []
        if match:
            sql = (
                "SELECT v.id, v.source_id, v.version_no, v.fetched_at, v.title, v.text, "
                "       s.url, s.kind, s.title AS source_title, bm25(source_versions_fts) AS score "
                "FROM source_versions_fts f "
                "JOIN source_versions v ON v.id = f.rowid "
                "JOIN sources s ON s.id = v.source_id "
                "WHERE source_versions_fts MATCH ?"
            )
            args.append(match)
        else:
            if not short_terms:
                return []
            sql = (
                "SELECT v.id, v.source_id, v.version_no, v.fetched_at, v.title, v.text, "
                "       s.url, s.kind, s.title AS source_title, 0 AS score "
                "FROM source_versions v JOIN sources s ON s.id = v.source_id "
                "WHERE 1=1"
            )
        # 索引で引けない短い語は LIKE で併せて絞る
        for t in short_terms:
            sql += " AND v.text LIKE ? ESCAPE '\\'"
            args.append(textutil.like_pattern(t))
        if kind:
            sql += " AND s.kind = ?"
            args.append(kind)
        if latest_only:
            sql += (
                " AND v.version_no = (SELECT MAX(version_no) FROM source_versions x "
                "WHERE x.source_id = v.source_id)"
            )
        sql += " ORDER BY score, v.id DESC LIMIT ?"
        args.append(limit)

        out = []
        for r in self.conn.execute(sql, args):
            d = dict(r)
            text = d.pop("text")
            d["excerpt"] = textutil.excerpt(text, query)
            d["source_version_id"] = d.pop("id")
            d["kind_label"] = kind_label(d["kind"])
            d["priority"] = kind_priority(d["kind"])
            d["citable"] = d["kind"] not in NON_CITABLE_KINDS
            out.append(d)
        return out

    # ---- エンティティ -----------------------------------------------------
    def resolve_entity(self, name: str) -> sqlite3.Row | None:
        name = name.strip()
        r = self.conn.execute("SELECT * FROM entities WHERE name = ?", (name,)).fetchone()
        if r:
            return r
        return self.conn.execute(
            "SELECT e.* FROM entities e JOIN entity_aliases a ON a.entity_id = e.id WHERE a.alias = ?",
            (name,),
        ).fetchone()

    def ensure_entity(
        self, name: str, *, kind: str | None = None, aliases: Sequence[str] = (), note: str | None = None
    ) -> int:
        name = name.strip()
        if not name:
            raise StoreError("エンティティ名が空です")
        r = self.resolve_entity(name)
        if r:
            eid = int(r["id"])
            if kind and not r["kind"]:
                self.conn.execute("UPDATE entities SET kind = ? WHERE id = ?", (kind, eid))
            if note and not r["note"]:
                self.conn.execute("UPDATE entities SET note = ? WHERE id = ?", (note, eid))
        else:
            cur = self.conn.execute(
                "INSERT INTO entities(name, kind, note, created_at) VALUES (?,?,?,?)",
                (name, kind, note, now()),
            )
            eid = int(cur.lastrowid)
        for a in aliases:
            a = a.strip()
            if not a or a == name:
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO entity_aliases(entity_id, alias) VALUES (?,?)", (eid, a)
            )
        self.conn.commit()
        return eid

    def list_entities(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT e.*, "
            " (SELECT COUNT(*) FROM claim_entities ce JOIN claims c ON c.id = ce.claim_id "
            "  WHERE ce.entity_id = e.id AND c.status='active') AS claims "
            "FROM entities e ORDER BY claims DESC, e.name"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["aliases"] = [
                a["alias"]
                for a in self.conn.execute(
                    "SELECT alias FROM entity_aliases WHERE entity_id = ?", (d["id"],)
                )
            ]
            out.append(d)
        return out

    def entity_coverage(self) -> list[dict]:
        """エンティティごとに、主張が何件・何種類の出典から来ているかを返す。

        どのキャラクターの情報が薄いかは、件数だけでなく
        「出典が公式サイト1つしかない」といった偏りに出る。
        """
        rows = self.conn.execute(
            "SELECT e.id, e.name, e.kind, "
            "       COUNT(DISTINCT c.id) AS claims, "
            "       COUNT(DISTINCT v.source_id) AS sources, "
            "       SUM(CASE WHEN c.verification='official' THEN 1 ELSE 0 END) AS official, "
            "       SUM(CASE WHEN c.verification='article' THEN 1 ELSE 0 END) AS article, "
            "       SUM(CASE WHEN c.verification='secondhand' THEN 1 ELSE 0 END) AS secondhand, "
            "       SUM(CASE WHEN c.verification='fan_interpretation' THEN 1 ELSE 0 END) AS fan "
            "FROM entities e "
            "LEFT JOIN claim_entities ce ON ce.entity_id = e.id "
            "LEFT JOIN claims c ON c.id = ce.claim_id AND c.status='active' "
            "LEFT JOIN source_versions v ON v.id = c.source_version_id "
            "GROUP BY e.id ORDER BY claims DESC, e.name"
        ).fetchall()
        return [dict(r) for r in rows]

    def related_entities(self, name: str, *, limit: int = 20) -> list[dict]:
        """同じ主張に一緒に現れるエンティティを数える（交流関係の下敷き）。"""
        e = self.resolve_entity(name)
        if not e:
            return []
        rows = self.conn.execute(
            "SELECT e2.id, e2.name, e2.kind, COUNT(*) AS shared "
            "FROM claim_entities a "
            "JOIN claim_entities b ON b.claim_id = a.claim_id AND b.entity_id <> a.entity_id "
            "JOIN claims c ON c.id = a.claim_id AND c.status='active' "
            "JOIN entities e2 ON e2.id = b.entity_id "
            "WHERE a.entity_id = ? GROUP BY e2.id ORDER BY shared DESC, e2.name LIMIT ?",
            (int(e["id"]), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- 知識層 -----------------------------------------------------------
    def add_claim(
        self,
        *,
        text: str,
        source_version_id: int | None,
        verification: str | None = None,
        entities: Sequence[str] = (),
        locator: str | None = None,
        note: str | None = None,
        supersedes: int | None = None,
    ) -> int:
        text = text.strip()
        if not text:
            raise StoreError("主張の本文が空です")

        kind = None
        if source_version_id is not None:
            v = self.get_version(source_version_id)
            if not v:
                raise StoreError(f"出典版が見つかりません: {source_version_id}")
            kind = v["kind"]
            if kind in NON_CITABLE_KINDS:
                raise StoreError(
                    f"出典種別 {kind_label(kind)} は知識層の根拠にできません。"
                    "候補（candidates）として扱い、実際のURLを取得・照合してから登録してください。"
                )
        if verification is None:
            verification = default_verification(kind) if kind else "unverified"
        if verification not in VERIFICATION_HANDLING:
            raise StoreError(f"未知の確認状態: {verification}")

        ts = now()
        cur = self.conn.execute(
            "INSERT INTO claims(text, source_version_id, verification, status, locator, note, "
            "created_at, updated_at) VALUES (?,?,?,'active',?,?,?,?)",
            (text, source_version_id, verification, locator, note, ts, ts),
        )
        cid = int(cur.lastrowid)
        for name in entities:
            eid = self.ensure_entity(name)
            self.conn.execute(
                "INSERT OR IGNORE INTO claim_entities(claim_id, entity_id, role) VALUES (?,?,'subject')",
                (cid, eid),
            )
        if supersedes:
            self._supersede(supersedes, cid)
        self.conn.commit()
        return cid

    def _supersede(self, old_id: int, new_id: int) -> None:
        old = self.conn.execute("SELECT id FROM claims WHERE id = ?", (old_id,)).fetchone()
        if not old:
            raise StoreError(f"上書き対象の主張が見つかりません: {old_id}")
        self.conn.execute(
            "UPDATE claims SET status='superseded', updated_at=? WHERE id = ?", (now(), old_id)
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO claim_links(from_claim_id, to_claim_id, type, created_at) "
            "VALUES (?,?,'supersedes',?)",
            (new_id, old_id, now()),
        )

    def link_claims(self, from_id: int, to_id: int, link_type: str, note: str | None = None) -> None:
        from .constants import LINK_TYPES

        if link_type not in LINK_TYPES:
            raise StoreError(f"未知の関係: {link_type}")
        self.conn.execute(
            "INSERT OR IGNORE INTO claim_links(from_claim_id, to_claim_id, type, note, created_at) "
            "VALUES (?,?,?,?,?)",
            (from_id, to_id, link_type, note, now()),
        )
        if link_type == "supersedes":
            self.conn.execute(
                "UPDATE claims SET status='superseded', updated_at=? WHERE id = ?", (now(), to_id)
            )
        self.conn.commit()

    def set_verification(self, claim_id: int, verification: str, note: str | None = None) -> None:
        if verification not in VERIFICATION_HANDLING:
            raise StoreError(f"未知の確認状態: {verification}")
        self.conn.execute(
            "UPDATE claims SET verification=?, updated_at=?, note=COALESCE(?, note) WHERE id=?",
            (verification, now(), note, claim_id),
        )
        self.conn.commit()

    def retract_claim(self, claim_id: int, note: str | None = None) -> None:
        self.conn.execute(
            "UPDATE claims SET status='retracted', updated_at=?, note=COALESCE(?, note) WHERE id=?",
            (now(), note, claim_id),
        )
        self.conn.commit()

    _CLAIM_SELECT = (
        "SELECT c.id, c.text, c.verification, c.status, c.locator, c.note, "
        "       c.created_at, c.updated_at, c.source_version_id, "
        "       v.version_no, v.fetched_at, s.url, s.kind, s.title AS source_title "
        "FROM claims c "
        "LEFT JOIN source_versions v ON v.id = c.source_version_id "
        "LEFT JOIN sources s ON s.id = v.source_id "
    )

    def _claim_row(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["kind_label"] = kind_label(d["kind"]) if d.get("kind") else "（出典なし）"
        d["priority"] = kind_priority(d["kind"]) if d.get("kind") else 0
        d["verification_label"] = verification_label(d["verification"])
        d["handling"] = VERIFICATION_HANDLING.get(d["verification"], "")
        d["entities"] = [
            e["name"]
            for e in self.conn.execute(
                "SELECT e.name FROM claim_entities ce JOIN entities e ON e.id = ce.entity_id "
                "WHERE ce.claim_id = ? ORDER BY e.name",
                (d["id"],),
            )
        ]
        d["contradicts"] = [
            int(x["other"])
            for x in self.conn.execute(
                "SELECT to_claim_id AS other FROM claim_links WHERE from_claim_id=? AND type='contradicts' "
                "UNION SELECT from_claim_id FROM claim_links WHERE to_claim_id=? AND type='contradicts'",
                (d["id"], d["id"]),
            )
        ]
        return d

    def get_claim(self, claim_id: int) -> dict | None:
        r = self.conn.execute(self._CLAIM_SELECT + " WHERE c.id = ?", (claim_id,)).fetchone()
        return self._claim_row(r) if r else None

    def search_claims(
        self,
        *,
        query: str | None = None,
        entity: str | None = None,
        kind: str | None = None,
        verification: str | None = None,
        status: str | None = "active",
        limit: int = 30,
    ) -> list[dict]:
        where: list[str] = []
        args: list[Any] = []
        sql = self._CLAIM_SELECT

        if query:
            long_terms, short_terms = textutil.split_terms(query)
            match = textutil.match_expression_for(long_terms)
            if match:
                sql = sql.replace(
                    "FROM claims c ", "FROM claims_fts f JOIN claims c ON c.id = f.rowid "
                )
                where.append("claims_fts MATCH ?")
                args.append(match)
            # 索引で引けない短い語（2文字の人名など）は LIKE で絞る
            for t in short_terms:
                where.append("c.text LIKE ? ESCAPE '\\'")
                args.append(textutil.like_pattern(t))
        if entity:
            e = self.resolve_entity(entity)
            if e is None:
                # 未登録のエンティティ名は本文一致に退避する
                where.append("c.text LIKE ? ESCAPE '\\'")
                args.append(f"%{entity}%")
            else:
                where.append(
                    "c.id IN (SELECT claim_id FROM claim_entities WHERE entity_id = ?)"
                )
                args.append(int(e["id"]))
        if kind:
            where.append("s.kind = ?")
            args.append(kind)
        if verification:
            where.append("c.verification = ?")
            args.append(verification)
        if status:
            where.append("c.status = ?")
            args.append(status)

        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " LIMIT ?"
        args.append(max(limit * 4, limit))

        rows = [self._claim_row(r) for r in self.conn.execute(sql, args)]
        rows.sort(key=lambda d: (_VERIF_RANK.get(d["verification"], 9), -d["priority"], d["id"]))
        return rows[:limit]

    def count_claims(self, query: str, *, status: str | None = "active") -> int:
        """語に一致する主張の件数。検索語の効き目（希少さ）を測るのに使う。"""
        long_terms, short_terms = textutil.split_terms(query)
        match = textutil.match_expression_for(long_terms)
        where: list[str] = []
        args: list[Any] = []
        if match:
            sql = "SELECT COUNT(*) FROM claims_fts f JOIN claims c ON c.id = f.rowid"
            where.append("claims_fts MATCH ?")
            args.append(match)
        else:
            sql = "SELECT COUNT(*) FROM claims c"
        for t in short_terms:
            where.append("c.text LIKE ? ESCAPE '\\'")
            args.append(textutil.like_pattern(t))
        if status:
            where.append("c.status = ?")
            args.append(status)
        if not where:
            return 0
        return int(self.conn.execute(f"{sql} WHERE {' AND '.join(where)}", args).fetchone()[0])

    # ---- 候補（他AIの調査報告） -------------------------------------------
    def add_candidates(self, report_version_id: int | None, items: Iterable[dict]) -> int:
        n = 0
        for it in items:
            text = (it.get("claim_text") or "").strip()
            if not text:
                continue
            self.conn.execute(
                "INSERT INTO candidates(report_version_id, url, claim_text, status, note, created_at) "
                "VALUES (?,?,?,'pending',?,?)",
                (report_version_id, it.get("url"), text, it.get("note"), now()),
            )
            n += 1
        self.conn.commit()
        return n

    def list_candidates(self, *, status: str | None = "pending", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM candidates"
        args: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY id LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]

    def resolve_candidate(
        self, candidate_id: int, *, status: str, note: str | None = None, promoted_claim_id: int | None = None
    ) -> None:
        if status not in ("pending", "verified", "rejected"):
            raise StoreError(f"未知の候補状態: {status}")
        self.conn.execute(
            "UPDATE candidates SET status=?, checked_at=?, note=COALESCE(?, note), "
            "promoted_claim_id=COALESCE(?, promoted_claim_id) WHERE id=?",
            (status, now(), note, promoted_claim_id, candidate_id),
        )
        self.conn.commit()
