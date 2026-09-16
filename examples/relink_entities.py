"""既存の主張に、いま登録されているエンティティを結び付け直す。

    python examples/relink_entities.py [作品スラッグ...]

主張とエンティティの結び付きは登録時に決まるので、あとから別名を足しても
既存の主張には付かない。取り込みをやり直さずに結び付けだけ直したいときに使う。

足すだけで、既にある結び付きは消さない。節見出しや題名から帰属させたもの
（本文に名前が出ない主張）を潰さないため。
"""
import sys

sys.path.insert(0, "src")
from cqs import config, textutil


def surfaces(store) -> list[tuple[str, int, str]]:
    """(照合する表記, エンティティID, 空白を除いた表記) の一覧。"""
    out = []
    for e in store.list_entities():
        eid = int(e["id"])
        for name in [e["name"], *(e.get("aliases") or ())]:
            if name:
                out.append((name, eid, textutil.flatten(name)))
    return out


for slug in sys.argv[1:] or [w["file_slug"] for w in config.list_works() if not w.get("error")]:
    st = config.open_work(slug)
    with st:
        table = surfaces(st)
        before = st.conn.execute("SELECT COUNT(*) FROM claim_entities").fetchone()[0]
        added = 0
        rows = st.conn.execute("SELECT id, text FROM claims WHERE status = 'active'").fetchall()
        for r in rows:
            text = r["text"]
            flat = textutil.flatten(text)
            for name, eid, fname in table:
                if name in text or fname in flat:
                    cur = st.conn.execute(
                        "INSERT OR IGNORE INTO claim_entities(claim_id, entity_id, role) "
                        "VALUES (?,?,'subject')", (int(r["id"]), eid))
                    added += cur.rowcount
        st.conn.commit()
        print(f"{slug}: 主張 {len(rows)} 件を見て、結び付きを {added} 件足した "
              f"（{before} → {before + added}）")
