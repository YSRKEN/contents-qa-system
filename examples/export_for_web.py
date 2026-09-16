"""公開ページ用に、知識層をJSへ書き出す。

    python examples/export_for_web.py out/data.js

本体はJSONをgzipで固め、base64にして .txt で出す。ページ側で
DecompressionStream を通して展開する。同じ語の繰り返しが多いので、
gzipで生のJSONの2割前後まで落ちる。base64で3分の4に戻るが、それでも
生の3割弱で済む。バイナリのまま置けないのは、配布できるのが
テキスト・画像・音声など決まった種類だけのため。

作品ごとにファイルを分けるのは、選んでいない作品まで読み込んで展開する
必要がないため。索引だけ先に読み、作品を選んだ時点でその作品のぶんを読む。

主張の出典種別・URL・区分は出典側に同じものがあるので書き出さない。
学園アイドルマスターでは、この3つで16.7MBのうち4.9MBを占めていた。
ページ側は読み込み時に出典から補う。
"""
import base64
import datetime
import gzip
import json
import pathlib
import sys

sys.path.insert(0, "src")
from cqs import config
from cqs.constants import SOURCE_KINDS, VERIFICATION_HANDLING

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data.js")
OUT.parent.mkdir(parents=True, exist_ok=True)


def dump_text(path: pathlib.Path, js: str) -> int:
    path.write_text(js, encoding="utf-8")
    n = len(js.encode())
    print(f"  {path.name}: {round(n / 1024):>7} KB")
    return n


def dump_gz(path: pathlib.Path, obj) -> int:
    """JSONをgzipで固め、base64にして書く。配布の上限は1件16MB。"""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    # mtime=0 で、内容が同じなら毎回同じバイト列になる（差し替えの有無が分かる）
    packed = base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0))
    path.write_bytes(packed)
    flag = "  ← 上限16MB超過" if len(packed) > 16 * 1024 * 1024 else ""
    print(f"  {path.name}: {round(len(packed) / 1024):>7} KB"
          f"（生 {round(len(raw) / 1024):>7} KB / {round(100 * len(packed) / len(raw))}%）{flag}")
    return len(packed)


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
        dump_gz(OUT.with_name(f"work-{slug}.txt"), work)
        dump_gz(OUT.with_name(f"text-{slug}.txt"), texts)
        index.append({"work": work["work"], "stats": work["stats"]})

data = {"kinds": {k: v["label"] for k, v in SOURCE_KINDS.items()},
        "priority": {k: v["priority"] for k, v in SOURCE_KINDS.items()},
        "handling": VERIFICATION_HANDLING,
        "works": index,
        "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
print("索引")
dump_text(OUT, "window.CQS_WORK={};window.CQS_TEXT={};window.CQS_DATA="
          + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";")
