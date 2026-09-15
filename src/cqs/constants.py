"""出典種別・確認状態など、層をまたいで参照される語彙の定義。"""

from __future__ import annotations

# --- 出典種別 ---------------------------------------------------------------
# priority: 回答時の優先順。大きいほど優先する。
# refetch:  既定の再取得方針。'periodic' は版を積む対象、'once' は1回取得で確定。
# verification: その出典から抽出した主張に機械的に与える確認状態。
SOURCE_KINDS: dict[str, dict] = {
    "official_site": {
        "label": "公式サイト",
        "priority": 100,
        "refetch": "periodic",
        "verification": "official",
    },
    "official_sns": {
        "label": "公式SNS",
        "priority": 95,
        "refetch": "periodic",
        "verification": "official",
    },
    "interview": {
        "label": "インタビュー",
        "priority": 70,
        "refetch": "once",
        "verification": "article",
    },
    "article": {
        "label": "紹介記事・報道",
        "priority": 60,
        "refetch": "once",
        "verification": "article",
    },
    "transcript": {
        # 本編のセリフの書き起こし・字幕・名言集。原典が手に入らない作品では
        # これが一番原典に近い。ただし第三者の書き起こしなので伝聞として扱う。
        "label": "本編の書き起こし・セリフ引用",
        "priority": 75,
        "refetch": "once",
        "verification": "secondhand",
    },
    "image_transcript": {
        # 記事に貼られた表・カレンダー・図の画像を読み取った結果。
        # 元の出典が何であれ、読み取りを挟む以上は伝聞として扱う。
        "label": "画像の読み取り",
        "priority": 40,
        "refetch": "once",
        "verification": "secondhand",
    },
    "fan_chronicle": {
        # 感想ではなく「本編に何が描かれていたか」の記述。作中の日付や配置の読み取り、
        # 時系列の整理など、原理的には本編を見れば検証できる内容を置く。
        "label": "ファンによる本編の記述・時系列整理",
        "priority": 40,
        "refetch": "once",
        "verification": "secondhand",
    },
    "fan_note": {
        "label": "感想note・ファン考察",
        "priority": 30,
        "refetch": "once",
        "verification": "fan_interpretation",
    },
    "wiki_index": {
        # 第一の役割は「出典欄から原資料を辿るための索引」。ただし出典が書籍しかない記述は
        # 辿る先がネット上に無いので、Wikiを根拠から完全に外すとその情報が落ちる。
        # そのため伝聞として扱い、どの脚注に基づくかを主張のメモに残す。
        "label": "Wiki（百科事典・探索用インデックス）",
        "priority": 45,
        "refetch": "periodic",
        "verification": "secondhand",
    },
    "ai_report": {
        "label": "他AIの調査報告",
        "priority": 0,
        "refetch": "once",
        "verification": "unverified",
    },
    "manual": {
        "label": "手動入力・手持ち資料",
        "priority": 50,
        "refetch": "once",
        "verification": "unverified",
    },
}

# --- 確認状態 ---------------------------------------------------------------
VERIFICATIONS: dict[str, str] = {
    "official": "公式確認",
    "article": "記事のみ",
    "secondhand": "伝聞",
    "fan_interpretation": "ファン解釈",
    "needs_recheck": "要再確認",
    "unverified": "未検証",
}

# 回答時の扱い。ツール返却値に同梱し、LLM側のプロンプトでこの文字列を根拠にさせる。
VERIFICATION_HANDLING: dict[str, str] = {
    "official": "作品内の事実として断定してよい。",
    "article": "出典を添えて提示する。断定はしない。",
    "secondhand": (
        "作中の出来事として述べてよい。ただし公式が述べたことではなく、"
        "本編を見た第三者の読み取りなので、出典を示し、他の出典と一致するかどうかを断る。"
    ),
    "fan_interpretation": "『そう解釈する感想がある』という形でのみ述べる。作品内の事実の根拠にはしない。",
    "needs_recheck": "出典の本文が変化している。再確認するまで根拠に使わない。",
    "unverified": "未照合。根拠に使わない。",
}

# 知識層の主張が取り得る状態
CLAIM_STATUSES = ("active", "superseded", "retracted")

# 主張どうしの関係
LINK_TYPES = ("supersedes", "contradicts", "supports")

# 知識層に入れてよい出典種別（ai_report は候補どまり）
NON_CITABLE_KINDS = frozenset({"ai_report"})


def kind_label(kind: str) -> str:
    return SOURCE_KINDS.get(kind, {}).get("label", kind)


def kind_priority(kind: str) -> int:
    return SOURCE_KINDS.get(kind, {}).get("priority", 0)


def default_verification(kind: str) -> str:
    return SOURCE_KINDS.get(kind, {}).get("verification", "unverified")


def default_refetch(kind: str) -> str:
    return SOURCE_KINDS.get(kind, {}).get("refetch", "once")


def verification_label(verification: str) -> str:
    return VERIFICATIONS.get(verification, verification)
