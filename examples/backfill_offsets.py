"""既存の主張に、原文中の位置を埋める。

主張IDは登録順でしかないので、取り込み条件を変えて同じ出典を取り直すと、
途中の文が末尾のIDで入り、IDの順が原文の順と一致しなくなる。
locator（抽出元の文）を原文から探して位置を復元する。
"""
import sys
sys.path.insert(0, "src")
from cqs import config, textutil

for slug in sys.argv[1:] or [w["file_slug"] for w in config.list_works() if not w.get("error")]:
    st = config.open_work(slug)
    texts = {int(r["id"]): r["text"] for r in st.conn.execute("SELECT id, text FROM source_versions")}
    filled = missed = 0
    for sv, text in texts.items():
        rows = st.conn.execute(
            "SELECT id, locator, text FROM claims "
            "WHERE source_version_id = ? AND offset IS NULL ORDER BY id", (sv,)).fetchall()
        cursor = 0
        for r in rows:
            needle = r["locator"] or r["text"]
            at = textutil.find_flat(text, needle, cursor)
            if at is None:
                missed += 1
                continue
            cursor = at + len(needle)
            st.conn.execute("UPDATE claims SET offset = ? WHERE id = ?", (at, r["id"]))
            filled += 1
    st.conn.commit()
    print(f"{slug}: 位置を埋めた {filled} 件 / 見つからず {missed} 件")
    st.close()
