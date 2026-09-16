"""公開ページ用に、知識層をJSへ書き出す。

    python examples/export_for_web.py out/data.js

作品ごとにファイルを分ける。1作品が数万件になると1ファイルが配布の上限
（16MB）を超えるため。ページは索引だけ先に読み、作品を選んだ時点で
その作品のファイルを読む。

主張の出典種別・URL・区分は出典側に同じものがあるので書き出さない。
学園アイドルマスターでは、この3つで16.7MBのうち4.9MBを占めていた。
ページ側は読み込み時に出典から補う。
"""
import datetime
import json
import pathlib
import sys

sys.path.insert(0, "src")
from cqs import config
from cqs.constants import SOURCE_KINDS, VERIFICATION_HANDLING

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data.js")
OUT.parent.mkdir(parents=True, exist_ok=True)


def dump(path: pathlib.Path, js: str) -> int:
    path.write_text(js, encoding="utf-8")
    n = len(js.encode())
    flag = "  ← 上限16MB超過" if n > 16 * 1024 * 1024 else ""
    print(f"  {path.name}: {round(n / 1024):>7} KB{flag}")
    return n


index = []
for w in config.list_works():
    if w.get("error"):
        continue
    with config.open_work(w["file_slug"]) as st:
        slug = st.meta.get("slug")
        claims = [{"id": c["id"], "t": c["text"], "v": c["verification"], "st": c["status"],
                   "sv": c["source_version_id"], "e": c["entities"], "x": c["contradicts"],
                   "o": c["offset"]}
                  for c in st.search_claims(status=None, limit=1000000)]
        sources, texts = [], {}
        for s in st.list_sources():
            v = st.latest_version(s["id"])
            if not v:
                continue
            sv = int(v["id"])
            sources.append({"sv": sv, "id": s["id"], "k": s["kind"], "u": s["url"] or "",
                            "ti": s["title"] or "", "at": v["fetched_at"], "n": v["version_no"],
                            "g": s["segment"] or ""})
            texts[str(sv)] = st.source_excerpt(sv, length=10 ** 7)["excerpt"]
        work = {"work": {"title": st.meta.get("title"), "slug": slug,
                         "note": st.meta.get("note", "")},
                "entities": [{"n": e["name"], "k": e["kind"] or "", "c": e["claims"], "a": e["aliases"]}
                             for e in st.list_entities()],
                "claims": claims, "sources": sources, "stats": st.stats()}
        print(f"{st.meta.get('title')}: 出典{len(sources)} / 主張{len(claims)}")
        j = json.dumps(work, ensure_ascii=False, separators=(",", ":"))
        dump(OUT.with_name(f"work-{slug}.js"), f'window.CQS_WORK["{slug}"]={j};'
             f"window.dispatchEvent(new CustomEvent('cqs-work',{{detail:'{slug}'}}));")
        t = json.dumps(texts, ensure_ascii=False, separators=(",", ":"))
        dump(OUT.with_name(f"text-{slug}.js"), f'window.CQS_TEXT["{slug}"]={t};'
             f"window.dispatchEvent(new CustomEvent('cqs-text',{{detail:'{slug}'}}));")
        index.append({"work": work["work"], "stats": work["stats"]})

data = {"kinds": {k: v["label"] for k, v in SOURCE_KINDS.items()},
        "priority": {k: v["priority"] for k, v in SOURCE_KINDS.items()},
        "handling": VERIFICATION_HANDLING,
        "works": index,
        "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
print("索引")
dump(OUT, "window.CQS_WORK={};window.CQS_TEXT={};window.CQS_DATA="
     + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";")
