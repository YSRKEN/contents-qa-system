"""取り込みの上位操作（取得→原文層→候補→知識層）。

CLI・MCP・Web UI から共通で呼ぶ。個々の層の操作は store.py に委ねる。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from . import extract, fetch, report, textutil
from .constants import is_reference_kind, kind_label
from .store import StoreError, WorkStore


def ingest_url(
    store: WorkStore,
    url: str,
    *,
    kind: str | None = None,
    title: str | None = None,
    note: str | None = None,
    respect_robots: bool | None = None,
) -> dict:
    """URLを取得して原文層に版を積む。既存URLなら版を追加する。"""
    existing = store.find_source_by_url(url)
    kind = kind or (existing["kind"] if existing else store.guess_kind(url))
    res = fetch.fetch(url, respect_robots=respect_robots)
    source_id = store.add_source(url=url, kind=kind, title=title or res.title, note=note)
    v = store.add_version(
        source_id, text=res.text, title=res.title, http_status=res.status, raw_html=res.html
    )
    store.log(
        "ingest_url",
        {"url": url, "kind": kind, "version_id": v.version_id, "changed": v.changed},
    )
    return {
        "source_id": source_id,
        "source_version_id": v.version_id,
        "version_no": v.version_no,
        "changed": v.changed,
        "rechecked_claims": v.rechecked_claims,
        "kind": kind,
        "kind_label": kind_label(kind),
        "title": res.title,
        "url": url,
        "final_url": res.final_url,
        "text_length": len(res.text),
    }


def ingest_text(
    store: WorkStore,
    text: str,
    *,
    kind: str,
    title: str,
    url: str | None = None,
    note: str | None = None,
) -> dict:
    """貼り付けた資料をそのまま原文層に入れる（検索を伴わない直接入力）。"""
    source_id = store.add_source(url=url, kind=kind, title=title, note=note)
    v = store.add_version(source_id, text=text, title=title)
    store.log("ingest_text", {"title": title, "kind": kind, "version_id": v.version_id})
    return {
        "source_id": source_id,
        "source_version_id": v.version_id,
        "version_no": v.version_no,
        "changed": v.changed,
        "rechecked_claims": v.rechecked_claims,
        "kind": kind,
        "kind_label": kind_label(kind),
        "title": title,
        "text_length": len(text),
    }


def ingest_ai_report(store: WorkStore, text: str, *, title: str, note: str | None = None) -> dict:
    """他AIの調査報告を取り込む。原文層に置き、(URL,主張) の対を候補に積む。

    報告そのものは根拠にしない（出典種別 ai_report は知識層に入れられない）。
    """
    r = ingest_text(store, text, kind="ai_report", title=title, note=note)
    items = report.parse_report(text)
    n = store.add_candidates(
        r["source_version_id"],
        [{"url": it.url, "claim_text": it.claim_text, "note": it.note} for it in items],
    )
    r["candidates"] = n
    r["with_url"] = sum(1 for it in items if it.url)
    store.log("ingest_ai_report", {"title": title, "candidates": n})
    return r


def refetch_sources(
    store: WorkStore, *, source_ids: Sequence[int] | None = None, only_periodic: bool = True
) -> list[dict]:
    """再取得対象の出典を取り直す。本文が変われば版を積み、依存する主張を要再確認に落とす。"""
    if source_ids:
        rows = [store.get_source(i) for i in source_ids]
        rows = [r for r in rows if r]
    else:
        rows = [
            r
            for r in store.conn.execute("SELECT * FROM sources WHERE url IS NOT NULL ORDER BY id")
            if not only_periodic or r["refetch"] == "periodic"
        ]
    out = []
    for r in rows:
        try:
            res = ingest_url(store, r["url"], kind=r["kind"])
            out.append(res)
        except Exception as e:
            out.append({"url": r["url"], "error": str(e)})
    return out


def verify_candidates(
    store: WorkStore,
    *,
    candidate_ids: Iterable[int] | None = None,
    limit: int = 20,
    promote_threshold: float | None = None,
    respect_robots: bool | None = None,
) -> list[dict]:
    """候補のURLを実際に取得し、主張がそのページに書かれているか照合する。

    promote_threshold を与えた場合のみ、しきい値以上の候補を知識層へ昇格する。
    既定では照合結果を記録するだけで、採否は人（またはLLM）が決める。
    """
    if candidate_ids is not None:
        ids = [int(i) for i in candidate_ids]
        placeholders = ",".join("?" * len(ids))
        cands = (
            [
                dict(r)
                for r in store.conn.execute(
                    f"SELECT * FROM candidates WHERE id IN ({placeholders})", ids
                )
            ]
            if ids
            else []
        )
    else:
        cands = store.list_candidates(status="pending", limit=limit)

    results: list[dict] = []
    for c in cands:
        if not c.get("url"):
            results.append({"candidate_id": c["id"], "error": "URLが紐づいていません"})
            continue
        try:
            ing = ingest_url(store, c["url"], respect_robots=respect_robots)
        except Exception as e:
            store.resolve_candidate(c["id"], status="pending", note=f"取得失敗: {e}")
            results.append({"candidate_id": c["id"], "url": c["url"], "error": str(e)})
            continue

        v = store.source_excerpt(ing["source_version_id"], offset=0, length=10**7)
        score = report.match_score(c["claim_text"], v["excerpt"] if v else "")
        rec = {
            "candidate_id": c["id"],
            "url": c["url"],
            "claim_text": c["claim_text"],
            "source_version_id": ing["source_version_id"],
            "kind": ing["kind"],
            "kind_label": ing["kind_label"],
            **score,
        }
        note = f"照合スコア {score['score']} (一致 {len(score['matched'])}/{score['tokens']})"
        if promote_threshold is not None and score["score"] >= promote_threshold:
            try:
                cid = store.add_claim(
                    text=c["claim_text"],
                    source_version_id=ing["source_version_id"],
                    locator=None,
                    note=f"他AI報告の候補から照合して登録。{note}",
                )
                store.resolve_candidate(c["id"], status="verified", note=note, promoted_claim_id=cid)
                rec["claim_id"] = cid
                rec["promoted"] = True
            except StoreError as e:
                store.resolve_candidate(c["id"], status="pending", note=f"{note} / 昇格不可: {e}")
                rec["error"] = str(e)
        else:
            store.resolve_candidate(c["id"], status="pending", note=note)
            rec["promoted"] = False
        results.append(rec)
    return results


def _longest_matches(hits: Sequence[str]) -> list[str]:
    """短いほうが長いほうに含まれている一致を落とす。

    「まどか」は「魔法少女まどか☆マギカ」の一部でもある。作品名が出ただけの文に
    登場人物が紐づくと、そのキャラクターの主張が本文と関係なく膨らむ。
    長い一致が取れているなら、その中に収まる短い一致は数えない。
    """
    out = []
    for h in hits:
        if any(h != other and h in other for other in hits):
            continue
        out.append(h)
    return out


def _surface_map(store: WorkStore, entities: Sequence[str]) -> dict[str, str]:
    """本文に現れ得る表記 → 正規名 の対応表。別名も検索対象に含める。"""
    out: dict[str, str] = {}
    for e in store.list_entities():
        if entities and e["name"] not in entities:
            continue
        out[e["name"]] = e["name"]
        for a in e.get("aliases", ()):
            out.setdefault(a, e["name"])
    for name in entities:
        out.setdefault(name, name)
    return out


def _default_require_entity(store: WorkStore, source_version_id: int) -> bool:
    """人物名を含まない文まで採るかどうか。出典ごとの指定があればそれに従う。"""
    v = store.get_version(source_version_id)
    if not v:
        return True
    mode = v["extract"] if "extract" in v.keys() else None
    if mode == "full":
        return False
    if mode == "entity_only":
        return True
    return not is_reference_kind(v["kind"])


def propose_claims(
    store: WorkStore,
    source_version_id: int,
    *,
    entities: Sequence[str] = (),
    limit: int = 50,
    require_entity: bool | None = None,
) -> list[dict]:
    """ある版の本文から主張候補を切り出す（登録はしない）。

    エンティティは別名（表記ゆれ）でも拾い、候補には正規名を返す。
    """
    v = store.source_excerpt(source_version_id, offset=0, length=10**7)
    if not v:
        raise StoreError(f"出典版が見つかりません: {source_version_id}")
    if require_entity is None:
        require_entity = _default_require_entity(store, source_version_id)
    surfaces = _surface_map(store, list(entities))
    # require_entity=False のときはエンティティで絞らず、文として成立する行をすべて出す。
    # 公式SNSの告知のように、人物名が出ないが作品の事実を含む出典で使う。
    cands = extract.dedupe(
        extract.candidate_sentences(v["excerpt"], entities=list(surfaces) if require_entity else ())
    )
    for c in cands:
        # entities で絞らなかった場合、文中の表記を改めて拾って正規名に直す
        flat_text = textutil.flatten(c["text"])
        hits = c["entities"] or [
            k for k in surfaces if k in c["text"] or textutil.flatten(k) in flat_text
        ]
        c["entities"] = sorted({surfaces[k] for k in _longest_matches(hits)})
        # 節見出しがあれば、それも対象エンティティの手がかりにする
        if c.get("section"):
            flat_section = textutil.flatten(c["section"])
            section_hits = [
                k for k in surfaces
                if k in c["section"] or textutil.flatten(k) in flat_section
            ]
            c["entities"] = sorted(
                set(c["entities"]) | {surfaces[k] for k in _longest_matches(section_hits)}
            )
    return cands[:limit]


def register_proposed(
    store: WorkStore,
    source_version_id: int,
    *,
    entities: Sequence[str] = (),
    limit: int = 50,
    require_entity: bool | None = None,
) -> list[int]:
    """propose_claims の候補をまとめて知識層に登録する。

    確認状態は出典種別から機械的に決まる（公式サイト→公式確認、感想note→ファン解釈）。
    locator には抽出元の文をそのまま入れ、後から原文と突き合わせられるようにする。
    """
    cands = propose_claims(
        store, source_version_id, entities=entities, limit=limit, require_entity=require_entity
    )
    # 同じ版から二度登録しても重複しないようにする（条件を変えて取り直す運用があるため）
    existing = {
        r["text"]
        for r in store.conn.execute(
            "SELECT text FROM claims WHERE source_version_id = ?", (source_version_id,)
        )
    }
    ids: list[int] = []
    for c in cands:
        if require_entity and not c["entities"]:
            continue
        # 見出しを本文に冠して、その文が何についての記述かを残す
        text = f"{c['section']}: {c['text']}" if c.get("section") else c["text"]
        if text in existing:
            continue
        existing.add(text)
        ids.append(
            store.add_claim(
                text=text,
                source_version_id=source_version_id,
                entities=c["entities"],
                locator=c["text"],
                offset=c.get("offset"),
            )
        )
    store.log("register_proposed", {"source_version_id": source_version_id, "claims": len(ids)})
    return ids
