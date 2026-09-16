"""原文層から主張候補を切り出すところの回帰テスト。"""

from cqs import extract


def _texts(cands):
    return [c["text"] for c in cands]


def test_節見出しの人物名が下の文に引き継がれる():
    text = "## 白草 四音（しらくさ しおん）\n\n極月学園1年生。全てにおいて一級品の能力を持つ。\n"
    got = extract.candidate_sentences(text, entities=["白草四音"])
    assert _texts(got) == ["極月学園1年生。", "全てにおいて一級品の能力を持つ。"]
    assert all("白草四音" in c["entities"] for c in got)


def test_見出し直下の編集リンク行が節の連鎖を切らない():
    """Wikipediaは見出しの下に「[編集]」を単独行で挟む。

    これを本文として扱うと after_heading が落ち、次に来る短い行
    （「声 - 子安武人」）が新しい見出しとして節を上書きしてしまい、
    人物名が節から失われる。
    """
    text = (
        "## 黒井 崇男（くろい たかお）\n"
        "[編集]\n\n"
        "声 - 子安武人\n\n"
        "961プロダクション社長にして極月学園理事長。\n"
    )
    got = extract.candidate_sentences(text, entities=["黒井崇男"])
    assert _texts(got) == ["961プロダクション社長にして極月学園理事長。"]
    assert "黒井崇男" in got[0]["entities"]
    assert "黒井 崇男（くろい たかお）" in got[0]["section"]


def test_編集リンク行そのものは主張にならない():
    text = "## 概要\n[編集]\n\nこれは本文である。\n"
    assert _texts(extract.candidate_sentences(text)) == ["これは本文である。"]


def test_見出し末尾の編集リンクは落とす():
    assert extract.heading_of("## 概要[編集]") == "概要"
    assert extract.heading_of("[編集]") is None


def test_セリフの行は節見出しにならない():
    """物語の資料は短いセリフが並ぶ。見出しと読むとそこで場面が切れ、
    前半に出ていた人物名が後半の文に引き継がれなくなる。"""
    text = (
        "## 第17話：センパイが選んだアイドル\n"
        "現れたのは白草四音でした。\n"
        "「へえ？まさかとは思いますが……\n"
        "諦めなければ、夢は叶うとでも？」\n"
        "アイドルとしての才能は欠片もない。\n"
    )
    got = extract.candidate_sentences(text)
    assert {c["section"] for c in got} == {"第17話：センパイが選んだアイドル"}
    assert "アイドルとしての才能は欠片もない。" in _texts(got)


def test_助詞や言いさしで終わる行は節見出しにならない():
    assert extract.heading_of("Ｐは") is None
    assert extract.heading_of("と言葉を重ねますが、彼女は") is None
    assert extract.heading_of("そんな四音の言葉に麻央は…") is None
    # 名前の末尾に来るひらがな（「かぐや」「はるか」）は助詞扱いしない
    assert extract.heading_of("かぐや") == "かぐや"
    assert extract.heading_of("はるか") == "はるか"
    assert extract.heading_of("交友関係") == "交友関係"
