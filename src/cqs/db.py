"""作品DB（1作品=1 SQLiteファイル）のスキーマと接続。

このモジュールはスキーマ定義だけを持ち、ドメイン操作は store.py に置く。
DBファイルはアプリのソースコードと密結合しない：ファイルを差し替えれば別作品になる。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

# trigram トークナイザを使う。日本語は空白で区切られないため、
# 既定の unicode61 では語をまたいだ検索がほぼ効かない。
# trigram は3文字以上の部分一致を索引で引ける（2文字以下は LIKE にフォールバックする）。
_FTS_TOKENIZE = "trigram"

SCHEMA = f"""
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 作品そのもののメタ情報（作品名・スラッグ・スキーマ版など）
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ===== 原文層 =============================================================
-- 出典（URL単位、または貼り付け資料単位）
CREATE TABLE IF NOT EXISTS sources (
    id         INTEGER PRIMARY KEY,
    url        TEXT UNIQUE,
    kind       TEXT NOT NULL,
    title      TEXT,
    note       TEXT,
    refetch    TEXT NOT NULL DEFAULT 'once',   -- periodic | once
    -- 同じ作品世界の中でも、どの作品についての記述かを分ける
    -- （TVシリーズ / 劇場版 / スピンオフゲーム など）。作品ごとに自由に決める。
    segment    TEXT,
    created_at TEXT NOT NULL
);

-- 出典の版。差し替えず追加のみ。
CREATE TABLE IF NOT EXISTS source_versions (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    version_no   INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL,
    http_status  INTEGER,
    content_hash TEXT NOT NULL,
    title        TEXT,
    text         TEXT NOT NULL,
    raw_gz       BLOB,          -- 生HTML（zlib圧縮）。消失対策。
    UNIQUE(source_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_sv_source ON source_versions(source_id);
CREATE INDEX IF NOT EXISTS idx_sv_hash   ON source_versions(source_id, content_hash);

CREATE VIRTUAL TABLE IF NOT EXISTS source_versions_fts USING fts5(
    text, title,
    content='source_versions', content_rowid='id',
    tokenize='{_FTS_TOKENIZE}'
);
CREATE TRIGGER IF NOT EXISTS sv_ai AFTER INSERT ON source_versions BEGIN
    INSERT INTO source_versions_fts(rowid, text, title) VALUES (new.id, new.text, new.title);
END;
CREATE TRIGGER IF NOT EXISTS sv_ad AFTER DELETE ON source_versions BEGIN
    INSERT INTO source_versions_fts(source_versions_fts, rowid, text, title)
        VALUES ('delete', old.id, old.text, old.title);
END;
CREATE TRIGGER IF NOT EXISTS sv_au AFTER UPDATE ON source_versions BEGIN
    INSERT INTO source_versions_fts(source_versions_fts, rowid, text, title)
        VALUES ('delete', old.id, old.text, old.title);
    INSERT INTO source_versions_fts(rowid, text, title) VALUES (new.id, new.text, new.title);
END;

-- ===== 知識層 =============================================================
CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT,           -- character | term | event | work | person ...
    note       TEXT,
    created_at TEXT NOT NULL
);

-- 表記ゆれ。検索時に正規名へ寄せる。
CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias     TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS claims (
    id                INTEGER PRIMARY KEY,
    text              TEXT NOT NULL,
    source_version_id INTEGER REFERENCES source_versions(id) ON DELETE SET NULL,
    verification      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',   -- active | superseded | retracted
    locator           TEXT,        -- 原文中の該当箇所（照合に使った引用）
    note              TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_sv     ON claims(source_version_id);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);

CREATE TABLE IF NOT EXISTS claim_entities (
    claim_id  INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role      TEXT NOT NULL DEFAULT 'subject',   -- subject | mentioned
    PRIMARY KEY (claim_id, entity_id, role)
);
CREATE INDEX IF NOT EXISTS idx_ce_entity ON claim_entities(entity_id);

-- 矛盾・上書き・補強は主張の属性ではなく主張どうしの関係として持つ。
CREATE TABLE IF NOT EXISTS claim_links (
    id            INTEGER PRIMARY KEY,
    from_claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    to_claim_id   INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    type          TEXT NOT NULL,   -- supersedes | contradicts | supports
    note          TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE(from_claim_id, to_claim_id, type)
);
CREATE INDEX IF NOT EXISTS idx_links_to ON claim_links(to_claim_id);

CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
    text,
    content='claims', content_rowid='id',
    tokenize='{_FTS_TOKENIZE}'
);
CREATE TRIGGER IF NOT EXISTS claims_ai AFTER INSERT ON claims BEGIN
    INSERT INTO claims_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS claims_ad AFTER DELETE ON claims BEGIN
    INSERT INTO claims_fts(claims_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS claims_au AFTER UPDATE ON claims BEGIN
    INSERT INTO claims_fts(claims_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO claims_fts(rowid, text) VALUES (new.id, new.text);
END;

-- ===== 候補（他AIの調査報告から取り出した未照合の主張） ====================
CREATE TABLE IF NOT EXISTS candidates (
    id                INTEGER PRIMARY KEY,
    report_version_id INTEGER REFERENCES source_versions(id) ON DELETE CASCADE,
    url               TEXT,
    claim_text        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',  -- pending | verified | rejected
    checked_at        TEXT,
    note              TEXT,
    promoted_claim_id INTEGER REFERENCES claims(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cand_status ON candidates(status);

-- ===== 作業ログ ============================================================
CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT
);
"""


# 後から足した列。既存のDBには ALTER TABLE で追加する。
_ADDED_COLUMNS = [
    ("sources", "segment", "TEXT"),
    # 原文中での位置。主張IDは登録順でしかないので、取り込み直すと並び順が崩れる
    ("claims", "offset", "INTEGER"),
]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """既存のDBに、後から足した列を追加する。"""
    applied = []
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column}")
    if applied:
        conn.commit()
    return applied


def connect(path: str | Path, *, create: bool = True) -> sqlite3.Connection:
    """作品DBに接続する。存在しなければ（create=True なら）スキーマを作る。"""
    path = Path(path)
    if not create and not path.exists():
        raise FileNotFoundError(f"作品DBが見つかりません: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if create:
        conn.executescript(SCHEMA)
        conn.commit()
    migrate(conn)
    return conn


def init_work(conn: sqlite3.Connection, *, slug: str, title: str, note: str = "") -> None:
    """作品メタ情報を書き込む（既存値は上書き）。"""
    rows = [
        ("schema_version", str(SCHEMA_VERSION)),
        ("slug", slug),
        ("title", title),
        ("note", note),
    ]
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        rows,
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
