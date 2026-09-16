"""資料に書いてある基本的な事実が、回答の材料に入るか調べる。

    python examples/eval_basic_facts.py                    # 全作品
    python examples/eval_basic_facts.py gakuen-idolmaster  # 作品を指定

取り出しの規則を変えたら、全作品で掛けて壊していないか確かめる。
「×」は知識層に無いのか、あるのに届いていないのかで意味が違うので、
知識層全体での件数も併記する。
"""
import sys
sys.path.insert(0, "src")
from cqs import answer, config

CASES = {
    "madoka-magica": [
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
    ],
    "cho-kaguyahime": [
        ("監督は誰？", ["山下清悟"]),
        ("配信はいつから？", ["2026年1月22日"]),
        ("かぐやはどんな人物？", ["かぐや"]),
        ("酒寄彩葉とかぐやの関係は？", ["彩葉", "かぐや"]),
        ("月見ヤチヨとは？", ["ヤチヨ"]),
        ("制作会社はどこ？", ["ツインエンジン"]),
        ("かぐやの声優は？", ["夏吉ゆうこ"]),
        ("ツクヨミとは何？", ["ツクヨミ"]),
        ("どの作品が原典になっている？", ["竹取物語"]),
        ("KASSENとは？", ["KASSEN"]),
    ],
    "gakuen-idolmaster": [
        # 公式サイトに書いてあること
        ("篠澤広の誕生日は？", ["12月21日"]),
        ("花海咲季の声優は？", ["長月"]),
        ("初星学園の学園長は誰？", ["十王邦夫"]),
        ("根緒亜紗里はどういう立場？", ["担任", "先生"]),
        ("配信開始はいつ？", ["2024年5月16日"]),
        # 登場人物（節見出しにしか名前が無い人）
        ("白草四音はどんな人物？", ["極月学園", "月花の妹", "Aランク"]),
        ("賀陽燐羽と月村手毬の関係は？", ["チームメイト", "SyngUp"]),
        ("極月学園とは？", ["961プロダクション", "アイドル養成学校"]),
        ("篠澤広の交友関係は？", ["倉本千奈", "花海佑芽", "初めてできた友達"]),
        # ゲームシステム
        ("好調とは何？", ["好調"]),
        ("トゥルーエンドの到達条件は？", ["アチーブメント", "親愛度"]),
        ("レッスン以外に何ができる？", ["授業", "おでかけ", "相談", "活動支給"]),
        # シナリオ
        ("月村手毬のSTEP2ではどんな出来事があった？", ["燐羽", "N.I.A", "炎上"]),
        ("十王星南はどんな経緯でアイドルになった？", ["星南"]),
        # 制作・技術
        ("コミュの制作にはどんなツールを使っている？", ["Uguiss"]),
        ("カードのバランス調整にAIをどう使った？", ["強化学習", "デッキ探索"]),
        ("ライブ制作にはどれくらい時間がかかる？", ["半年"]),
        ("シナリオは誰が書いている？", ["伏見つかさ", "志瑞祐", "雨宮和希"]),
    ],
}


def run(slug: str) -> tuple[int, int]:
    cases = CASES.get(slug)
    if not cases:
        print(f"{slug}: 評価ケースが無いので飛ばします")
        return (0, 0)
    st = config.open_work(slug)
    ok = 0
    print(f"### {slug}")
    for q, needles in cases:
        ctx = answer.retrieve(st, q)
        body = "\n".join(c["text"] for c in ctx["claims"])
        hit = [n for n in needles if n in body]
        anywhere = []
        for n in needles:
            r = st.conn.execute(
                "SELECT COUNT(*) n FROM claims WHERE status='active' AND text LIKE ?",
                (f"%{n}%",)).fetchone()["n"]
            anywhere.append(f"{n}:{r}")
        ok += bool(hit)
        print(f"{'○' if hit else '×'} {q}\n    材料{len(ctx['claims'])}件 / 当たり={hit} / 知識層全体={anywhere}")
    print(f"{slug}: {ok}/{len(cases)}\n")
    st.close()
    return ok, len(cases)


slugs = sys.argv[1:] or [w["file_slug"] for w in config.list_works() if not w.get("error")]
total_ok = total_n = 0
for slug in slugs:
    a, b = run(slug)
    total_ok += a
    total_n += b
if len(slugs) > 1:
    print(f"合計 {total_ok}/{total_n}")
