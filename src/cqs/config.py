"""作品DBの置き場所と、作品の一覧・解決。

作品DBはソースコードと密結合させない。`data/<slug>.db` を差し替えれば別作品になる。
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from .store import WorkStore

DB_SUFFIX = ".db"


def data_dir() -> Path:
    """作品DBの置き場所。CQS_DATA_DIR > ./data > リポジトリ直下の data。"""
    env = os.environ.get("CQS_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    cwd_data = Path.cwd() / "data"
    if cwd_data.is_dir():
        return cwd_data.resolve()
    return (Path(__file__).resolve().parents[2] / "data").resolve()


def slugify(name: str) -> str:
    """作品名からファイル名に使えるスラッグを作る。日本語はそのまま残す。"""
    s = unicodedata.normalize("NFKC", name).strip()
    s = re.sub(r"[\s/\\:*?\"<>|]+", "-", s)
    s = re.sub(r"[.]+", "", s)
    s = s.strip("-")
    return s or "work"


def work_path(slug: str) -> Path:
    if slug.endswith(DB_SUFFIX):
        slug = slug[: -len(DB_SUFFIX)]
    return data_dir() / f"{slug}{DB_SUFFIX}"


def list_works() -> list[dict]:
    """data ディレクトリ内の作品DBを列挙する。"""
    d = data_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob(f"*{DB_SUFFIX}")):
        slug = p.stem
        entry = {"slug": slug, "path": str(p), "title": slug, "error": None}
        try:
            with WorkStore.open(p, create=False) as st:
                entry.update(st.stats())
                entry["slug"] = st.meta.get("slug") or slug
                entry["title"] = st.meta.get("title") or slug
                entry["file_slug"] = slug
        except Exception as e:  # 壊れたDBがあっても一覧自体は返す
            entry["error"] = str(e)
        entry.setdefault("file_slug", slug)
        out.append(entry)
    return out


def open_work(slug: str, *, create: bool = False) -> WorkStore:
    p = work_path(slug)
    if not p.exists() and not create:
        known = ", ".join(w["file_slug"] for w in list_works()) or "（なし）"
        raise FileNotFoundError(f"作品DBが見つかりません: {p}\n登録済み: {known}")
    return WorkStore.open(p, create=create)


def create_work(title: str, *, slug: str | None = None, note: str = "") -> WorkStore:
    slug = slug or slugify(title)
    p = work_path(slug)
    if p.exists():
        raise FileExistsError(f"すでに存在します: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    return WorkStore.create(p, slug=slug, title=title, note=note)
