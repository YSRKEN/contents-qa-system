"""公開ページ用に、全作品の知識層を1つのJSへ書き出す。"""
import json, pathlib, sys, datetime
sys.path.insert(0, "src")
from cqs import config
from cqs.constants import VERIFICATION_HANDLING, SOURCE_KINDS

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data.js")
works = []
for w in config.list_works():
    if w.get("error"):
        continue
    with config.open_work(w["file_slug"]) as st:
        claims = [{"id":c["id"],"t":c["text"],"v":c["verification"],"k":c["kind"] or "","u":c["url"] or "",
                   "st":c["status"],"sv":c["source_version_id"],"e":c["entities"],"x":c["contradicts"]}
                  for c in st.search_claims(status=None, limit=100000)]
        sources = []
        for s in st.list_sources():
            v = st.latest_version(s["id"])
            if not v: continue
            sources.append({"sv":int(v["id"]),"id":s["id"],"k":s["kind"],"u":s["url"] or "",
                            "ti":s["title"] or "","at":v["fetched_at"],"n":v["version_no"],
                            "tx":st.source_excerpt(int(v["id"]), length=10**7)["excerpt"]})
        works.append({
            "work": {"title": st.meta.get("title"), "slug": st.meta.get("slug"),
                     "note": st.meta.get("note", "")},
            "entities": [{"n":e["name"],"k":e["kind"] or "","c":e["claims"],"a":e["aliases"]}
                         for e in st.list_entities()],
            "claims": claims, "sources": sources, "stats": st.stats(),
        })
        print(f"  {st.meta.get('title')}: 出典{len(sources)} / 主張{len(claims)}")

data = {"kinds": {k: v["label"] for k, v in SOURCE_KINDS.items()},
        "priority": {k: v["priority"] for k, v in SOURCE_KINDS.items()},
        "handling": VERIFICATION_HANDLING,
        "works": works,
        "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
js = "window.CQS_DATA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"
OUT.write_text(js, encoding="utf-8")
print(f"{OUT}: {round(len(js.encode())/1024)} KB / {len(works)}作品")
