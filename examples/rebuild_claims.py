"""原文を作り直したうえで、自動抽出の主張を作り直す。

抽出の規則（見出しの取り方、どの文を採るか）を変えたときに使う。
手で足した主張（note の付いたもの）は残す。

note の付いた主張が残る版にも自動抽出を掛ける。二重にならないのは、
`register_proposed` が**原文での位置の重なり**を見て、既に主張が付いている
一節からは作らないため。専用の取り込みは記事の一部（登場人物欄など）しか
見ていないことがあり、掛けないと、あらすじや設定の節がまるごと落ちる。
"""
import sys
sys.path.insert(0, "src")
from cqs import config, ingest

for slug in sys.argv[1:] or [w["file_slug"] for w in config.list_works() if not w.get("error")]:
    st = config.open_work(slug)
    before = st.stats()["claims_active"]
    vids = [int(r["id"]) for r in st.conn.execute(
        "SELECT v.id FROM source_versions v JOIN sources s ON s.id = v.source_id "
        "WHERE s.kind NOT IN ('ai_report') ORDER BY v.id")]
    redone = kept = 0
    for v in vids:
        if st.get_raw_html(v):
            r = st.rederive_text(v)
            if r.get("changed"):
                redone += 1
        st.conn.execute(
            "DELETE FROM claims WHERE source_version_id = ? AND note IS NULL AND status = 'active'",
            (v,))
        st.conn.commit()
        if st.conn.execute(
            "SELECT 1 FROM claims WHERE source_version_id = ? AND note IS NOT NULL "
            "AND status = 'active' LIMIT 1", (v,)).fetchone():
            kept += 1
        try:
            ingest.register_proposed(st, v, limit=100000)
        except Exception as ex:                       # 取り込めない版は飛ばす
            print(f"  skip v{v}: {ex}")
    print(f"{slug}: 本文を作り直した版 {redone} / 専用の取り込みに足した版 {kept} / "
          f"主張 {before} → {st.stats()['claims_active']}")
    st.close()
