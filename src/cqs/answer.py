"""回答層。質問→検索→出典種別に応じた扱いを指示した上での回答。

LLMを呼ぶかどうかは任意。APIキーが無い環境では、検索した材料と
そのまま貼れるプロンプトを返すところまでを担う（Claude Desktop / Claude Code へ渡す想定）。
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Sequence

from . import report, textutil
from .constants import VERIFICATION_HANDLING, verification_label
from .store import WorkStore

DEFAULT_MODEL = os.environ.get("CQS_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """あなたは特定の作品について、蓄積された出典付きの主張だけを根拠に答える。

守ること:
- 与えられた材料に無いことは答えない。推測で補わない。材料が足りなければ「この知識層には無い」と述べる。
- 各主張には確認状態が付いている。確認状態ごとの扱いを必ず守る:
{handling}
- 出典の優先順は 公式サイト・公式SNS ＞ インタビュー・紹介記事 ＞ 感想note。
  下位の出典が上位と食い違う場合は、両方を並べ、どちらがどの出典かを示す。
- 主張どうしが矛盾している場合は、片方を選ばず両方を提示する。
- 同じ出典から取り出した主張は、claim ID の小さい順が原文での並び順に対応する。時系列を組み立てるときの手がかりにしてよい。
- 一部の主張には「区分」が付いている。同じ作品世界でも、どの作品（TVシリーズ／劇場版／スピンオフなど）についての記述かを表す。
  区分の違う主張を、区分を伏せたまま同じ作品の事実として並べない。答える際はどの区分の記述かを示し、
  区分をまたぐ食い違いは矛盾ではなく「作品ごとの違い」として扱う。質問が区分を指定している場合は、その区分の主張を優先する。
- 根拠にした主張は、末尾で claim ID と出典URLを挙げる。
- 感想noteは「そう感じた人がいる」という事実の根拠にはなるが、作品内の事実の根拠にはしない。
"""


def _handling_block() -> str:
    return "\n".join(
        f"  - {verification_label(k)}（{k}）: {v}" for k, v in VERIFICATION_HANDLING.items()
    )


def mentioned_entities(store: WorkStore, question: str) -> list[str]:
    """質問文に含まれる登録済みエンティティ名（別名含む）を拾う。"""
    flat = textutil.flatten(question)
    hits: list[str] = []
    for e in store.list_entities():
        names = [e["name"], *e.get("aliases", [])]
        if any(n and (n in question or textutil.flatten(n) in flat) for n in names):
            hits.append(e["name"])
    return hits


_VERIF_RANK = {"official": 0, "article": 1, "secondhand": 2, "fan_interpretation": 3,
               "needs_recheck": 4, "unverified": 5}


# 漢字の連なりと、その直後の送り仮名1文字まで。カタカナ・英数は2文字以上。
_Q_TOKEN = re.compile(r"([一-龥々]+)([ぁ-ん]?)|([ァ-ヴー]{2,}|[A-Za-z0-9]{2,})")
# 語の一部ではなく助詞。「月に」「関係は」を検索語にしても本文には当たらない
_PARTICLES = frozenset("はがをにへもので")
# 質問の言い回しであって、作品について何も言っていない語。
# 形式名詞（「魔女化した際の」の「際」）を残すと、「回復する際」「登場の際」のように
# 話題の違う文が高い重みで大量に入ってくる。単独の漢字1文字で出てきたときだけ落とす
# （「劇中」「時間」のように長い語の一部なら、その語ごと検索語になる）。
_Q_STOP = frozenset({
    "何", "誰", "教", "教え", "詳", "詳し", "説明", "箇条", "箇条書",
    "書", "書い", "述べ", "答え", "一体", "具体", "以下", "上記",
    "際", "場合", "時", "点", "上", "中", "内", "他", "等", "方",
    "為", "事", "物", "者", "様", "頃", "前", "後", "間", "的", "性",
})


def question_terms(question: str) -> list[str]:
    """質問文から検索語を取り出す。

    質問文をそのまま全文検索に渡すと、文全体が1つのフレーズとして扱われ、
    ほぼ必ず0件になる。漢字・カタカナ・英数の連なりを語として拾う
    （助詞はひらがななので自然に落ちる）。

    漢字2文字以上だけを語とすると、日本語の内容語の多くが消える。
    「願い」「月」「帰った」「叶えた」はいずれも漢字1文字＋送り仮名で、
    「まどかはどんな願いを叶えた？」からは検索語が1つも取れなかった。
    漢字の連なりと、それに送り仮名1文字を足した形の両方を出し、
    どちらが本文の表記に合うかは重み付け（term_weight）に任せる。
    実在しない語は出現数0になり、useful の絞り込みで自然に落ちる。
    """
    out: list[str] = []

    def add(t: str) -> None:
        if t and t not in _Q_STOP and t not in out:
            out.append(t)

    for kanji, okuri, other in _Q_TOKEN.findall(textutil.normalize_query(question)):
        if not kanji:
            add(other)
            continue
        for run in kanji.split("何"):      # 「全何話」→「全」「話」
            add(run)
            # 送り仮名を足すのは漢字1文字の語だけ。2文字以上はそれで語として完結して
            # おり、後ろに続くのはたいてい助詞（「関係は」「時系列に」）になる。
            if len(run) == 1 and okuri and okuri not in _PARTICLES and run == kanji.split("何")[-1]:
                add(run + okuri)
    return out


def term_weight_for(df: int, total: int) -> float:
    """出現件数から重みを出す。多くの主張に当たるものほど軽い。"""
    if df <= 0 or total <= 1:
        return 0.0
    return round(8.0 * math.log(max(total / df, 1.0)) / math.log(total), 2)


def term_weight(store: WorkStore, term: str, total: int) -> float:
    """検索語の重み。多くの主張に当たる語ほど軽くする。

    「作品」のような語は数百件に当たるため、そのままでは点数を支配してしまい、
    「時系列」のような効く語がかき消される。
    """
    return term_weight_for(store.count_claims(term), total)


def _best_windows(
    positions: Sequence[int], hits: dict[float, float], width: int, slots: int
) -> list[tuple[int, int]]:
    """当たりが密な区間を、重ならないように上位から slots 個取る。

    出典を丸ごと1つの塊として点を付けると、Wikipediaのように何百件も主張のある出典は
    「当たりの総和は大きいが、当たりどうしが記事の端から端まで散っている」状態になり、
    正規化で割り引くと今度は該当節ごと沈む。実際に渡すのは連続した区間なので、
    その区間が持つ当たりの量で比べる。

    区間を1つに限らないのは、「魔法少女それぞれの魔女化」のように**答えが1か所に
    まとまっていない**質問があるため。1つの記事の中で、人物ごと・用語ごとに離れた
    場所に少しずつ書かれている。連続した1区間だけを切り出すと、最初の1人分しか渡せない。
    """
    scores = [hits.get(cid, 0.0) for cid in positions]
    n = len(scores)
    if n <= width:
        return [(0, n)] if n else []
    taken: list[tuple[int, int]] = []
    blocked: list[tuple[int, int]] = []
    for _ in range(max(slots, 1)):
        best, best_score = None, 0.0
        cur = sum(scores[:width])
        for i in range(0, n - width + 1):
            if i > 0:
                cur += scores[i + width - 1] - scores[i - 1]
            if any(i < b and a < i + width for a, b in blocked):
                continue
            if cur > best_score:
                best, best_score = i, cur
        if best is None or best_score <= 0:
            break
        taken.append((best, best + width))
        blocked.append((best, best + width))
    return sorted(taken)


def window_score(positions: Sequence[int], hits: dict[float, float], width: int, slots: int) -> float:
    scores = [hits.get(cid, 0.0) for cid in positions]
    total = 0.0
    for a, b in _best_windows(positions, hits, width, slots):
        total += sum(scores[a:b])
    return total


def _claims_in_order(store: WorkStore) -> tuple[dict[int, list[int]], dict[tuple[int, str], list[int]]]:
    """主張IDを原文での並び順で返す。出典ごとと、節ごとの2通り。

    主張IDは登録順でしかない。取り込み条件を変えて同じ出典を取り直すと、
    途中の文が後から末尾のIDで入るため、IDの順は原文の順と一致しなくなる。
    抽出時に原文中の位置（offset）を控えてあるので、そちらを優先して並べる。
    """
    order: dict[int, list[int]] = {}
    sections: dict[tuple[int, str], list[int]] = {}
    for r in store.conn.execute(
        "SELECT id, text, source_version_id AS sv FROM claims "
        "WHERE status='active' AND source_version_id IS NOT NULL "
        "ORDER BY sv, (offset IS NULL), offset, id"
    ):
        sv, cid = int(r["sv"]), int(r["id"])
        order.setdefault(sv, []).append(cid)
        sections.setdefault((sv, split_section(r["text"])[0]), []).append(cid)
    return order, sections


def _fetch_claims(store: WorkStore, ids: Sequence[int]) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [
        store._claim_row(r)
        for r in store.conn.execute(
            store._CLAIM_SELECT + f" WHERE c.id IN ({marks}) ORDER BY c.id", tuple(ids)
        )
    ]


def _shingles(text: str) -> set[str]:
    t = textutil.flatten(text)
    return {t[i:i + 3] for i in range(max(len(t) - 2, 1))}


def _near_duplicate(a: set[str], b: set[str], threshold: float = 0.65) -> bool:
    """作品紹介の定型文（「願いを叶えた代償として…」）は多くの出典に同じ形で載る。

    出典が増えるほど、同じ内容の主張が枠を食い潰す。文字3組の重なりで近さを測り、
    近すぎるものは1件だけ残す。出典が違っても、読み手に伝わる内容は同じなので。

    重なりは「短いほうのうち何割が相手に含まれるか」で測る。同じ定型文でも
    前後に足す言葉は出典ごとに違うため、両方の長さで割ると（Jaccard）
    見た目には同じ文でも0.4程度にしかならず、拾えない。
    """
    if not a or not b:
        return False
    # 短い文どうしは、違う内容でも形が同じになる（「巴マミが魔女化した存在。」と
    # 「佐倉杏子が魔女化した存在。」）。違うのは名前だけで、その名前こそが情報なので、
    # 重複として潰さない。定型文はもっと長い。
    if min(len(a), len(b)) < 30:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def split_section(text: str) -> tuple[str, str]:
    """主張は「節見出し: 本文」の形で入っている。見出しと本文に分ける。"""
    head, sep, body = text.partition(": ")
    if sep and len(head) <= 80:
        return head, body
    return "", text


def retrieve(
    store: WorkStore,
    question: str,
    *,
    max_claims: int = 120,
    max_sources: int = 5,
    extra_terms: Sequence[str] = (),
) -> dict[str, Any]:
    """質問に対する材料を集める。

    三段構え。
    1. エンティティ一致と語一致の合算で主張に点を付ける。
    2. 当たりが集まった出典については、その区間を原文の並び順ごと取り出す
       （時系列やあらすじのように、順序そのものが答えになる質問のため）。
    3. 矛盾関係にある主張は、片方が入ったらもう片方も必ず入れる。
    """
    ents = mentioned_entities(store, question)
    terms = list(dict.fromkeys([*(t.strip() for t in extra_terms if t.strip()), *question_terms(question)]))

    scored: dict[int, tuple[int, dict]] = {}
    # 見出しの一致を割り引く前の点。節を選ぶときはこちらを使う（下の bump を参照）
    topical: dict[int, float] = {}

    def bump(c: dict, points: float, full: float | None = None) -> None:
        prev = scored.get(c["id"])
        scored[c["id"]] = (prev[0] + points if prev else points, c)
        topical[c["id"]] = topical.get(c["id"], 0.0) + (points if full is None else full)

    def matched_weight(c: dict, needles: Sequence[str], points: float) -> float:
        """本文に当たったのか、節見出しにだけ当たったのかで重みを変える。

        名言集の主張は「By 鹿目まどか / ○○の名言: <セリフ>」の形で入る。セリフ本体は
        誰のことも名指ししていないのに、見出しのおかげでエンティティ一致として満点を取り、
        「関係は？」のような質問の材料を数十件単位で食い潰していた。
        見出しは「この主張が何の話題の下にあるか」であって「何を述べているか」ではないので、
        見出しだけの一致は本文の一致より軽くする。
        """
        _, body = split_section(c["text"])
        flat_body = textutil.flatten(body)
        if any(textutil.flatten(n) in flat_body for n in needles):
            return points
        return points * 0.25

    total = store.stats()["claims_active"]
    entity_rows = store.list_entities()
    alias_map = {e["name"]: [e["name"], *e.get("aliases", [])] for e in entity_rows}
    # 人物にも語と同じ重み付けをする。主人公は数百件の主張に出てくるので、
    # 名前が一致してもほとんど何も絞り込めない。一方その質問の鍵になる語
    # （「願」157件）のほうが絞り込める。固定点だと主人公の名前が鍵の語を上回り、
    # 答えそのものの主張が順位の外へ押し出されていた。
    entity_weight = {
        e["name"]: max(term_weight_for(int(e["claims"] or 0), total), 1.0) for e in entity_rows
    }
    for name in ents:
        names = alias_map.get(name, [name])
        # エンティティ一致は索引を引くだけで安いので、打ち切らずに全部見る。
        # ここを max_claims の数倍で打ち切ると、主人公のように何百件も付く人物では
        # 打ち切りの内側に入った出典だけが候補になり、出典の選び方が偶然で決まる。
        w = entity_weight.get(name, 4.0)
        for c in store.search_claims(entity=name, limit=100000):
            bump(c, matched_weight(c, names, w), full=w)
    weights = {t: term_weight(store, t, total) for t in terms}
    # ほぼ全件に当たる語は点数を支配するだけなので落とす。ただし全部落ちると
    # 何も返らなくなるので、その場合は重みの大きい順に2語だけ残す。
    useful = [t for t, w in weights.items() if w > 0.5]
    if not useful:
        useful = [t for t, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w > 0][:2]
    for t in useful:
        for c in store.search_claims(query=t, limit=max_claims * 3):
            w = max(weights[t], 0.5)
            bump(c, matched_weight(c, [t], w), full=w)

    ranked = sorted(
        scored.values(),
        key=lambda x: (-x[0], _VERIF_RANK.get(x[1]["verification"], 9), -x[1]["priority"], x[1]["id"]),
    )

    # --- 2. 当たりが集まった出典を、並び順ごと取り出す
    by_source: dict[int, list[tuple[int, dict]]] = {}
    for score, c in ranked:
        if c["source_version_id"]:
            by_source.setdefault(int(c["source_version_id"]), []).append((score, c))
    def title_bonus(items: list[tuple[float, dict]]) -> float:
        """出典のタイトル自体が検索語を含むなら、その出典が答えそのものである可能性が高い。

        ただし「関係」「作品」のようなありふれた語はどの記事の見出しにも出るので、
        語の重みをそのまま使い、加点の総量にも上限を置く。ここを大きくすると、
        中身がほとんど当たっていない出典が題名だけで節ごと選ばれてしまう。
        """
        title = (items[0][1].get("source_title") or "") + " " + (items[0][1].get("url") or "")
        return min(sum(weights.get(t, 0) * 2 for t in terms if t and t in title), 8.0)

    # 節が材料を食い尽くさないように、全体の6割までに抑える
    run_budget = max(8, int(max_claims * 0.6))
    in_order, in_section = _claims_in_order(store)

    # 取り出しの単位は「節」にする。参照記事は人物や用語ごとに節が立っており、
    # 質問が「それぞれの〜」と尋ねるとき、答えは節そのものだから。
    # 出典を単位にして幅の決まった窓を滑らせると、一行ずつ並んだ一覧表のような出典が
    # （どの行も当たるので密に見えて）必ず勝ち、説明の書かれた節が入らない。
    ALL_SECTIONS = "\x00"     # 出典まるごとを表す擬似的な節
    by_group: dict[tuple[int, str], list[tuple[float, dict]]] = {}
    for score, c in ranked:
        if c["source_version_id"]:
            sv = int(c["source_version_id"])
            by_group.setdefault((sv, split_section(c["text"])[0]), []).append((score, c))
            # 節だけを単位にすると、当たりの無い節に手が届かない。「時系列まとめ」の
            # ように出典そのものが答えである場合は、節をまたいで取り出す必要がある。
            by_group.setdefault((sv, ALL_SECTIONS), []).append((score, c))
    # 見出しの無い出典では、節なしの群と出典まるごとの群が同じものになる。
    # 両方を候補にすると同じ材料で2枠を使うので、出典まるごとのほうに寄せる。
    for sv, _sec in [k for k in by_group if k[1] == ""]:
        if (sv, ALL_SECTIONS) in by_group:
            by_group.pop((sv, ""), None)

    # 1つの節から渡す上限。節をいくつ拾うかと合わせて持ち分を決める
    per_group = max(4, run_budget // 7)

    entity_names = {textutil.flatten(n) for names in alias_map.values() for n in names}

    def heading_bonus(section: str) -> float:
        """節見出しがその作品の登録済みの対象（人物・用語）そのものなら押す。

        「人魚の魔女 / オクタヴィア（Oktavia）」のような節は、質問が尋ねている当の対象
        についての説明そのもの。一方「劇場版〈…〉あらすじ」のような節は、同じ語を含んでいても
        対象そのものではない。エンティティは人が選んで登録したものなので、
        「この作品が実際に持っている対象か」の目印になる。
        """
        if not section or section == ALL_SECTIONS:
            return 0.0
        head = re.sub(r"[（(\[【].*", "", section.split(" / ")[0])
        named = textutil.flatten(head) in entity_names
        return (6.0 if named else 0.0) + min(
            sum(weights.get(t, 0) * 2 for t in terms if t and t in section), 4.0)

    def group_value(key: tuple[int, str], items: list[tuple[float, dict]]) -> float:
        """その群から実際に渡す分（per_group 件）が、どれだけの内容を持つか。

        群の全体を合計すると、見出しが1つしかない長い記事（187件で1節）が
        件数だけで勝ってしまう。渡すのは per_group 件なので、その幅で比べる。

        1件あたりの重みには文の長さを掛ける。当たりの数だけで比べると、一行ずつ並んだ
        一覧表が必ず勝つ（どの行も語が当たるので密に見える）が、1件が伝える内容は
        「名前: 性質」程度しかない。説明の書かれた節は当たる行が少なくても1件が伝える量が多い。
        """
        ids_of = in_order.get(key[0], []) if key[1] == ALL_SECTIONS else in_section.get(key, [])
        # 節を選ぶときは、見出しだけの一致を割り引かない。
        # 「人魚の魔女 / オクタヴィア: <説明>」の説明文は魔女の名前を繰り返さないが、
        # その節は問われている当の対象についての記述そのものだから。
        # （割り引きは、名言集のように見出しが単なる帰属ラベルである場合のために、
        #   主張を1件ずつ並べる先頭の枠のほうで効かせる）
        weighted = {
            c["id"]: topical.get(c["id"], sc) * min(len(split_section(c["text"])[1]), 120) / 60
            for sc, c in items
        }
        if not ids_of:
            return sum(weighted.values())
        return window_score(ids_of, weighted, per_group, 1)

    def group_score(key: tuple[int, str], items: list[tuple[float, dict]]) -> float:
        base = group_value(key, items) + title_bonus(items) + heading_bonus(key[1])
        # 出典の優先順を効かせる。感想noteの節が公式・記事の節を押しのけないように
        return base * (0.5 + items[0][1].get("priority", 0) / 200)

    runs: list[dict] = []
    used_sources: list[int] = []
    taken = 0
    for key, items in sorted(by_group.items(), key=lambda kv: -group_score(kv[0], kv[1])):
        if taken >= run_budget:
            break
        if len(items) < 2 and title_bonus(items) <= 0:
            continue
        ids = in_order.get(key[0], []) if key[1] == ALL_SECTIONS else in_section.get(key, [])
        if not ids:
            continue
        width = min(per_group, run_budget - taken)
        hits = {c["id"]: score for score, c in items}
        picked: list[int] = []
        for start, end in _best_windows(ids, hits, width, 1):
            picked.extend(ids[start:end])
        if not picked:
            continue
        runs.extend(_fetch_claims(store, picked))
        taken += len(picked)
        if key[0] not in used_sources:
            used_sources.append(key[0])

    # 並び: まず当たりの強い主張（点で答える質問のため）、次に節を原文の並び順で
    # （順序が答えになる質問のため）、最後に残り。
    claims: list[dict] = []
    seen: set[int] = set()
    head_budget = max(4, int(max_claims * 0.4))
    # 先頭は「広さ」を担う。同じ節から何件も取ると、「魔法少女それぞれの魔女化」のような
    # 数え上げの質問で、最初の1人分だけで枠を使い切る。深さは後段の節ごと切り出しが担う。
    per_section = 3
    used_sections: dict[tuple[int, str], int] = {}
    head_shingles: list[set[str]] = []
    for _, c in ranked:
        if len(claims) >= head_budget:
            break
        key = (int(c["source_version_id"] or 0), split_section(c["text"])[0])
        if key[1]:
            if used_sections.get(key, 0) >= per_section:
                continue
        sh = _shingles(split_section(c["text"])[1])
        if any(_near_duplicate(sh, other) for other in head_shingles):
            seen.add(c["id"])     # 後段でも拾い直さない
            continue
        if key[1]:
            used_sections[key] = used_sections.get(key, 0) + 1
        head_shingles.append(sh)
        seen.add(c["id"])
        claims.append(c)
    for c in runs:
        if c["id"] not in seen:
            seen.add(c["id"])
            claims.append(c)
    for _, c in ranked:
        if len(claims) >= max_claims:
            break
        if c["id"] in seen:
            continue
        sh = _shingles(split_section(c["text"])[1])
        if any(_near_duplicate(sh, other) for other in head_shingles):
            seen.add(c["id"])
            continue
        head_shingles.append(sh)
        seen.add(c["id"])
        claims.append(c)

    # 当たりが少ない質問では、材料が数件で終わってしまう。人が資料を読むときと同じで、
    # 当たった文の前後には答えの残りが書かれていることが多いので、空いている分だけ足す。
    if len(claims) < max_claims // 2:
        index = {sv: {cid: i for i, cid in enumerate(ids)} for sv, ids in in_order.items()}
        extra: list[int] = []
        for _, c in ranked:
            sv = c["source_version_id"]
            if not sv:
                continue
            ids = in_order.get(int(sv), [])
            i = index.get(int(sv), {}).get(c["id"])
            if i is None:
                continue
            for j in range(max(0, i - 3), min(len(ids), i + 4)):
                if ids[j] not in seen:
                    seen.add(ids[j])
                    extra.append(ids[j])
            if len(claims) + len(extra) >= max_claims:
                break
        claims.extend(_fetch_claims(store, extra[: max_claims - len(claims)]))

    # --- 3. 矛盾する相手は必ず一緒に入れる（片方だけでは「両方を並べる」が守れない）
    for c in list(claims):
        for other in c.get("contradicts") or []:
            if other in seen:
                continue
            o = store.get_claim(int(other))
            if o:
                seen.add(int(other))
                claims.append(o)

    related = {n: store.related_entities(n) for n in ents}
    sources: list[dict] = []
    seen_src: set[int] = set()
    for t in terms:
        for s in store.search_sources(t, limit=max_sources):
            if s["source_version_id"] not in seen_src:
                seen_src.add(s["source_version_id"])
                sources.append(s)
    return {
        "question": question,
        "entities": ents,
        "terms": terms,
        "term_weights": weights,
        "related_entities": related,
        "claims": claims,
        "runs_from_sources": used_sources,
        "sources": sources[:max_sources],
    }


def format_context(ctx: dict[str, Any]) -> str:
    """LLMに渡す材料を、確認状態と出典が見える形で整形する。"""
    lines: list[str] = []
    if ctx.get("terms"):
        lines.append(f"# 使った検索語: {', '.join(ctx['terms'])}")
    if ctx["entities"]:
        lines.append(f"# 質問に現れた登録済みエンティティ: {', '.join(ctx['entities'])}")
        for name, rel in ctx["related_entities"].items():
            if rel:
                pairs = ", ".join(f"{r['name']}({r['shared']}件)" for r in rel[:10])
                lines.append(f"  - {name} と同じ主張に現れる: {pairs}")
        lines.append("")

    if ctx.get("targets"):
        got = ctx.get("claims_per_target") or {}
        lines.append(
            "# 対象ごとに分けて材料を集めた: "
            + "、".join(f"{t}（{got.get(t, 0)}件）" for t in ctx["targets"])
        )
        lines.append("  対象ごとに項を立てて答えること。材料が無い対象は、無いと述べる。")
        lines.append("")

    lines.append("# 知識層の主張")
    if not ctx["claims"]:
        lines.append("（該当なし）")
    last_target = None
    for c in ctx["claims"]:
        if c.get("target") and c["target"] != last_target:
            last_target = c["target"]
            lines.append("")
            lines.append(f"## {c['target']} について集めた材料")
        src = c.get("url") or c.get("source_title") or "（出典なし）"
        extra = f" / 矛盾: claim {', '.join(str(x) for x in c['contradicts'])}" if c["contradicts"] else ""
        seg = f" / 区分: {c['segment']}" if c.get("segment") else ""
        lines.append(
            f"- [claim {c['id']}] {c['text']}\n"
            f"    確認状態: {c['verification_label']}（{c['verification']}） / "
            f"出典種別: {c['kind_label']}{seg} / 出典: {src}{extra}"
        )

    lines.append("")
    lines.append("# 原文層の該当箇所（知識層に未登録の記述を拾うため）")
    if not ctx["sources"]:
        lines.append("（該当なし）")
    for s in ctx["sources"]:
        note = "" if s["citable"] else "  ※この出典種別は根拠にできない（候補どまり）"
        ssg = f" / 区分: {s['segment']}" if s.get("segment") else ""
        lines.append(
            f"- [source_version {s['source_version_id']}] {s.get('source_title') or s.get('title') or ''}"
            f" / {s['kind_label']}{ssg} / {s.get('url') or ''}{note}\n    …{s['excerpt']}…"
        )
    return "\n".join(lines)


def build_prompt(ctx: dict[str, Any]) -> dict[str, str]:
    system = SYSTEM_PROMPT.format(handling=_handling_block())
    user = f"{format_context(ctx)}\n\n# 質問\n{ctx['question']}"
    return {"system": system, "user": user}


def retrieve_for_targets(
    store: WorkStore,
    question: str,
    targets: Sequence[str],
    *,
    max_claims: int = 120,
    max_sources: int = 5,
    extra_terms: Sequence[str] = (),
) -> dict[str, Any]:
    """対象ごとに材料を集めてから1つにまとめる。

    「それぞれの魔女化は？」のような質問は、1回の検索では答えられない。
    どの一人ひとりを指すのかが質問文に書かれていないので、語の重みでは
    対象を区別できず、120件の枠が一覧表や概要文に薄く広がって終わる。

    枠が足りないのではなく、枠の配り方の問題である。対象ごとに枠を分け、
    対象の名前を足して引き直すと、その対象の節が選ばれるようになる。
    """
    targets = [t.strip() for t in targets if t and t.strip()]
    if not targets:
        return retrieve(store, question, max_claims=max_claims,
                        max_sources=max_sources, extra_terms=extra_terms)

    share = max(12, max_claims // len(targets))
    claims: list[dict] = []
    seen: set[int] = set()
    entities: list[str] = []
    terms: list[str] = []
    sources: list[dict] = []
    seen_src: set[int] = set()
    related: dict[str, Any] = {}
    per_target: dict[str, int] = {}

    for target in targets:
        sub = retrieve(
            store, f"{target}について。{question}",
            max_claims=share, max_sources=2,
            extra_terms=[target, *extra_terms],
        )
        got = 0
        for c in sub["claims"]:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            c = dict(c, target=target)
            claims.append(c)
            got += 1
        per_target[target] = got
        for name in sub["entities"]:
            if name not in entities:
                entities.append(name)
        for t in sub["terms"]:
            if t not in terms:
                terms.append(t)
        related.update(sub["related_entities"])
        for src in sub["sources"]:
            if src["source_version_id"] not in seen_src:
                seen_src.add(src["source_version_id"])
                sources.append(src)

    return {
        "question": question,
        "targets": targets,
        "claims_per_target": per_target,
        "entities": entities,
        "terms": terms,
        "term_weights": {},
        "related_entities": related,
        "claims": claims[:max_claims],
        "runs_from_sources": [],
        "sources": sources[:max_sources],
    }


PLAN_PROMPT = """作品『{title}』についての質問を、材料を集めやすい形に分ける。

質問: {question}

この作品に登録されている対象: {entities}

次のJSONだけを返す。
{{"targets": [...], "depth": "point" | "detail" | "survey"}}

targets: 質問が**複数の対象をまとめて**尋ねているなら（「それぞれ」「全員」「一覧」
「主要な〜たち」など）、その一人ひとり・一つひとつの名前。上の一覧にある名前をそのまま使う。
1つの対象だけを尋ねているなら空の配列。
depth: どれくらいの深さの答えが要るか。
point=1つの事実を答えれば済む（「監督は誰？」「全何話？」）。
detail=1つの対象について詳しく述べる（「○○について詳しく」「なぜ○○したの？」）。
survey=複数の対象を見渡す（「それぞれの〜」「一覧」「時系列」）。"""

FOLLOW_UP_PROMPT = """作品『{title}』の知識ベースで、次のやり取りがあった。

質問: {question}

回答:
{answer}

この作品に登録されている対象: {entities}

この人が次に知りたくなりそうなことを5つ、質問文の形で挙げる。次を満たすこと。
・回答が「この知識層には無い」で終わった点や、触れただけで掘り下げていない点を優先する
・回答の言い換えではなく、一段深いところか、隣にある別の対象を訊く
・上の「登録されている対象」に出てくる固有名詞を使う
・1つ40字以内。互いに重ならないようにする

JSON配列だけを返す。"""


def _has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _ask_json(prompt: str, *, model: str | None, max_tokens: int = 600) -> Any:
    """短い問い合わせを1回だけ投げて、JSONを取り出す。失敗したら None。"""
    if not _has_key():
        return None
    try:
        import anthropic  # type: ignore

        resp = anthropic.Anthropic().messages.create(
            model=model or DEFAULT_MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        for opener, closer in (("[", "]"), ("{", "}")):
            start, end = text.find(opener), text.rfind(closer)
            if 0 <= start < end:
                return json.loads(text[start:end + 1])
    except Exception:
        return None
    return None


def _entity_names(store: WorkStore, limit: int = 200) -> str:
    return "、".join(e["name"] for e in store.list_entities() if (e["claims"] or 0) > 0)[:4000]


def plan_question(store: WorkStore, question: str, *, model: str | None = None,
                  limit: int = 8) -> dict[str, Any]:
    """質問の対象と、要る答えの深さを決める。

    語の一致だけでは「それぞれ」が誰を指すか決められないので、ここだけLLMに委ねる。
    APIキーが無ければ既定値を返し、従来どおりの一発検索になる。
    """
    got = _ask_json(
        PLAN_PROMPT.format(title=store.meta.get("title") or "", question=question,
                           entities=_entity_names(store)),
        model=model)
    if not isinstance(got, dict):
        return {"targets": [], "depth": "detail"}
    targets = got.get("targets")
    targets = [str(t).strip() for t in targets if str(t).strip()] if isinstance(targets, list) else []
    depth = got.get("depth") if got.get("depth") in ("point", "detail", "survey") else "detail"
    return {"targets": targets[:limit], "depth": depth}


def follow_ups(store: WorkStore, question: str, answer_text: str, *,
               model: str | None = None) -> list[str]:
    """答えたあとに、次に訊きそうなことを出す。

    1回の質問で終わらせず、深いところへ降りていく道を見せるため。
    """
    if not answer_text:
        return []
    got = _ask_json(
        FOLLOW_UP_PROMPT.format(title=store.meta.get("title") or "", question=question,
                                answer=answer_text[:4000], entities=_entity_names(store, 60)),
        model=model)
    if not isinstance(got, list):
        return []
    return [str(x).strip() for x in got if str(x).strip()][:5]


def answer(
    store: WorkStore,
    question: str,
    *,
    model: str | None = None,
    max_claims: int = 120,
    use_llm: bool = True,
    extra_terms: Sequence[str] = (),
    plan: bool = True,
) -> dict[str, Any]:
    """質問に答える。APIキーが無い/use_llm=False の場合は材料とプロンプトだけを返す。"""
    plan_result = plan_question(store, question, model=model) if (plan and use_llm) else {
        "targets": [], "depth": "detail"}
    targets = plan_result["targets"]
    if len(targets) >= 2:
        ctx = retrieve_for_targets(store, question, targets,
                                   max_claims=max_claims, extra_terms=extra_terms)
    else:
        # 1つの事実を訊かれているのに120件を渡すと、周辺の話に埋もれて答えがぼやける
        limit = max_claims // 2 if plan_result["depth"] == "point" else max_claims
        ctx = retrieve(store, question, max_claims=limit, extra_terms=extra_terms)
    ctx["depth"] = plan_result["depth"]
    prompt = build_prompt(ctx)
    result: dict[str, Any] = {"context": ctx, "prompt": prompt, "answer": None, "model": None}

    if not use_llm:
        result["reason"] = "LLM呼び出しを行わない指定です。"
        return result
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        result["reason"] = (
            "ANTHROPIC_API_KEY が未設定のため、回答生成は行いませんでした。"
            "上のプロンプトを Claude Desktop / Claude Code に貼るか、MCPサーバー経由で使ってください。"
        )
        return result
    try:
        import anthropic  # type: ignore
    except ImportError:
        result["reason"] = (
            "anthropic パッケージが未インストールです（pip install 'contents-qa-system[llm]'）。"
        )
        return result

    client = anthropic.Anthropic()
    model = model or DEFAULT_MODEL
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=prompt["system"],
            messages=[{"role": "user", "content": prompt["user"]}],
        )
    except anthropic.APIStatusError as e:
        result["reason"] = f"APIエラー ({e.status_code}): {e.message}"
        return result
    except anthropic.APIConnectionError as e:
        result["reason"] = f"接続エラー: {e}"
        return result

    if resp.stop_reason == "refusal":
        result["reason"] = "モデルが応答を拒否しました。"
        return result
    result["answer"] = "\n".join(b.text for b in resp.content if b.type == "text")
    result["model"] = resp.model
    result["follow_ups"] = follow_ups(store, question, result["answer"], model=model)
    return result
