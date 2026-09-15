"""Wikipediaに書いてある基本的な事実が、回答の材料に入るか調べる。"""
import sys
sys.path.insert(0, "src")
from cqs import config, answer

CASES = [
    ("暁美ほむらの声優は？", ["斎藤千和"]),
    ("まどかはどんな願いを叶えた？", ["すべての魔女を生まれる前に消し去りたい", "魔女を生み出すルール"]),
    ("ワルプルギスの夜とは？", ["ワルプルギスの夜"]),
    ("ソウルジェムとは何？", ["ソウルジェム"]),
    ("キュゥべえの正体は？", ["インキュベーター"]),
    ("この作品は全何話？", ["全12話", "12話"]),
    ("放送はいつから？", ["2011年1月", "1月7日"]),
    ("舞台となる街の名前は？", ["見滝原"]),
    ("巴マミはどうなった？", ["お菓子の魔女", "最期"]),
    ("美樹さやかは何を願った？", ["恭介", "指"]),
    ("佐倉杏子はどんな魔法少女？", ["杏子"]),
    ("監督は誰？", ["新房昭之"]),
    ("脚本は誰が書いた？", ["虚淵玄"]),
    ("キャラクター原案は？", ["蒼樹うめ"]),
    ("魔女とは何？", ["結界", "グリーフシード"]),
]
st = config.open_work(sys.argv[1] if len(sys.argv) > 1 else "madoka-magica")
ok = 0
for q, needles in CASES:
    ctx = answer.retrieve(st, q)
    body = "\n".join(c["text"] for c in ctx["claims"])
    hit = [n for n in needles if n in body]
    # 知識層のどこかにはあるか
    anywhere = []
    for n in needles:
        r = st.conn.execute("SELECT COUNT(*) n FROM claims WHERE status='active' AND text LIKE ?", (f"%{n}%",)).fetchone()["n"]
        anywhere.append(f"{n}:{r}")
    mark = "○" if hit else "×"
    ok += bool(hit)
    print(f"{mark} {q}\n    材料{len(ctx['claims'])}件 / 当たり={hit} / 知識層全体={anywhere}")
print(f"\n{ok}/{len(CASES)}")
st.close()
