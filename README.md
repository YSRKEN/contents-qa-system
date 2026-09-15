# contents-qa-system（作品QAシステム）

原典（シナリオ・脚本など）が手に入らない作品について、公式サイト・記事・SNS・感想noteといった
**二次情報を出典付きで蓄積し、検索・質問応答する**ための個人用システムです。

要点は「LLM＋検索」そのものではなく、**二次情報の食い違いと更新をどう管理するか**にあります。
そのため、知識を「出典付きの主張」単位で持ち、上書き履歴を残し、出典種別ごとに回答での扱いを変えます。

```
公式サイト・Wiki ─┐
記事・note・SNS ─┼→ 原文層（不変・全文スナップショット）→ 抽出・照合 → 知識層（主張・出典・確認状態）
他AIの調査報告  ─┘         ↑ 再取得で版を積む                           ↓
                                                            検索ツール（CLI / MCP / Web UI）→ LLM
```

作品ごとに **1つのSQLiteファイル**（`data/<slug>.db`）を持ちます。ソースコードは作品に依存しません。
別の作品を扱いたければDBファイルを足すだけです。

## 3つの層

| 層 | 実体 | 性質 |
|---|---|---|
| 原文層 | `sources` / `source_versions` | 不変。取得した本文をURL・取得日時・版番号つきで積む。差し替えず追加のみ |
| 知識層 | `claims` / `entities` / `claim_links` | 編集可。「主張」1件ずつに出典IDと確認状態が付く。古い主張は消さず上書き扱いにする |
| 回答層 | CLI `ask` / MCPツール / Web UI | 確認状態と出典種別に応じて、断定するか・出典を添えるか・解釈として述べるかを変える |

### 出典種別と確認状態

出典種別から確認状態が機械的に決まります（人が上書きすることもできます）。

| 出典種別 | 既定の確認状態 | 回答での扱い | 再取得 |
|---|---|---|---|
| 公式サイト / 公式SNS | 公式確認 | 作品内の事実として断定してよい | 版を積む |
| インタビュー / 紹介記事・報道 | 記事のみ | 出典を添えて提示する。断定しない | 1回取得で確定 |
| ファンによる本編の記述・時系列整理 | 伝聞 | 作中の出来事として述べてよいが、公式の言明ではないので出典を示し、他の出典と一致するかを断る | 1回取得で確定 |
| 画像の読み取り | 伝聞 | 表・カレンダー・図の画像を読み取った結果。機械が読む工程が挟まるので伝聞として扱う | 1回取得で確定 |
| 本編の書き起こし・セリフ引用 | 伝聞 | 原典が無い作品では最も原典に近い。第三者の書き起こしなので伝聞として扱う | 1回取得で確定 |
| 感想note・ファン考察 | ファン解釈 | 「そう解釈する感想がある」形でのみ述べる。作品内の事実の根拠にしない | 1回取得で確定 |
| Wiki | 伝聞 | 索引として出典欄から原資料を辿る。書籍のみが出典の記述は辿る先が無いので、脚注を記録したうえで伝聞として使う | 版を積む |
| 他AIの調査報告 | 未検証 | **知識層の根拠にできない**（DB側で拒否する）。候補どまり | 1回取得で確定 |

再取得で本文が変化すると、その出典に依存する主張は自動で **要再確認** に落ちます。

### 出典ごとの取り込み方

参照系の出典（Wiki・公式サイト・書き起こし・画像の読み取り）は、人物名を含まない文も
主張にします。用語・設定・各話・制作の経緯は人物名を含まないことが多いためです。
ニュース記事や感想noteは案内と余談が大半なので、登録済みのエンティティに触れる文だけを採ります。

監督・声優・音楽家の記事のように「Wikipediaだが作品そのものの資料ではない」ものは、
出典ごとに切り替えられます。

```bash
cqs -w <作品> source-extract <出典ID> entity_only   # 触れる文だけ
cqs -w <作品> source-extract <出典ID> full          # 人物名の無い文も
cqs -w <作品> source-extract <出典ID> auto          # 出典種別に任せる（既定）
```

### 区分（関連作品がある場合）

TVシリーズ・劇場版・スピンオフが並ぶ作品世界では、1つのDBにまとめたうえで、出典ごとに
**どの作品についての記述か**を持たせます（`sources.segment`）。確からしさとは別の軸です。

```bash
cqs -w <作品> segment rule "example.com/tv" "TVシリーズ"   # URLから自動で付ける
cqs -w <作品> segment set 17 "スピンオフゲーム"              # 個別に付ける
cqs -w <作品> segment list                                  # 区分ごとの出典数
```

区分の付いた主張があると、回答時に「区分の違う主張を、区分を伏せたまま同じ作品の事実として
並べない」という制約が入り、UIでは絞り込みとバッジに出ます。1作品しか無ければ何も出ません。

## インストール

Python 3.10以上。コア機能（CLI・Web UI）は標準ライブラリだけで動きます。

```bash
git clone https://github.com/YSRKEN/contents-qa-system.git
cd contents-qa-system
pip install -e .                 # CLI と Web UI
pip install -e ".[mcp]"          # Claude Desktop / Claude Code から使う場合
pip install -e ".[llm]"          # cqs ask / Web UI にその場で回答させる場合
```

`.env.example` を見て、必要なら `CQS_DATA_DIR` や `ANTHROPIC_API_KEY` を設定してください。

## 使いはじめ

```bash
cqs new "作品名"                                   # data/作品名.db を作る
cqs works                                          # 作品一覧

cqs -w 作品名 entity add "キャラ名" --kind character --alias "略称"
cqs -w 作品名 fetch https://example.com/character   # URLを取得して原文層へ
cqs -w 作品名 propose 1                             # その版から主張候補を切り出す（登録はしない）
cqs -w 作品名 claim add --text "…" --source-version 1 --entity "キャラ名"

cqs -w 作品名 source-kind 3 fan_chronicle            # 取り込み後に出典種別を直す
cqs -w 作品名 entity coverage --kind character      # どのエンティティの情報が薄いか見る
cqs -w 作品名 images list 42                        # 記事に貼られた表・図の画像を一覧
cqs -w 作品名 images transcribe 42                  # 画像をClaudeに読み取らせ原文層へ（APIキーが要る）
cqs -w 作品名 images save 42 --out ./images         # キーが無ければ保存して自分で読む
cqs -w 作品名 rederive                              # 保存済みの生データから本文を作り直す（再取得しない）
cqs -w 作品名 wiki refs 17                          # Wikiの脚注URLを一覧（○=取り込み済み）
cqs -w 作品名 wiki fetch-refs 17                    # 未取得の脚注リンク先を原資料として取り込む
cqs -w 作品名 wiki annotate 17                      # Wiki由来の主張に、根拠の脚注を書き添える
cqs -w 作品名 claims --entity "キャラ名"            # 知識層を引く
cqs -w 作品名 search "キーワード"                   # 原文層を引く
cqs -w 作品名 ask "キャラ名の交流関係は？"
cqs -w 作品名 serve                                 # ブラウザUI（既定 http://127.0.0.1:8765）
```

作品固有の取り込み手順を書く例は `examples/ingest_cho_kaguyahime.py` にあります。

### 他AIの調査結果を取り込む

報告そのものは根拠になりません。`(URL, 主張)` の対を候補として積み、
**実際にURLを取得して主張がそのページに書かれているか照合してから**知識層に入れます。

```bash
cqs -w 作品名 add-report --title "Deep Research結果" --file report.md
cqs -w 作品名 candidates                 # 未照合の候補
cqs -w 作品名 verify --limit 5           # URLを取得して照合（登録はしない）
cqs -w 作品名 verify --limit 5 --promote 0.9   # 一致率0.9以上だけ自動登録
```

照合の一致率は目安です。たとえば公式サイトが「CV夏吉ゆうこ」と書いているとき、
「声優は夏吉ゆうこである」という主張は「声優」という語が無いぶん一致率が下がります。
値が低い＝誤りとは限らず、値が高い＝正しいとも限らないので、採否は人かLLMが本文を見て決めてください。

## 質問への答え方

質問は3段で処理します。

1. **計画** — 質問が複数の対象をまとめて尋ねているか（「それぞれ」「一覧」）、
   どれくらいの深さの答えが要るかを決めます。
2. **収集** — 対象ごとに枠を分けて材料を集めます。1回の検索では、どの一人ひとりを
   指すのかが質問文に書かれていないため、枠が一覧表や概要文に薄く広がって終わります。
3. **回答** — 対象ごとに見出しを付けて渡し、項を立てて答えさせます。

答えたあとは、次に訊きそうなことを5つ出します。回答が「この知識層には無い」で
終わった点や、触れただけで掘り下げていない点を優先します。

計画と次の質問はLLMを使うので、APIキーが無い場合（`cqs ask`）は飛ばして
従来どおりの一発検索になります。`--no-plan` で明示的に切れます。

## ブラウザUI

```bash
cqs serve
```

上部のタブで作品を切り替え、以下を行えます。

- **質問する** — 質問 → 検索 → 回答。APIキーがあればその場で回答し、無ければ Claude に貼れるプロンプトを出します
- **検索＆追加** — 知識層と原文層を同時に引き、原文の該当箇所からその場で主張を登録
- **直接入力** — 手元の資料の貼り付け／他AIの調査結果の貼り付け／URL取得
- **候補の照合** — 未照合候補の一覧と照合実行
- **管理** — 出典一覧・再取得・エンティティ登録・作品追加

127.0.0.1 にのみ待ち受け、認証は持ちません（自分専用の前提）。

## Claude Desktop / Claude Code から使う（MCP）

`pip install -e ".[mcp]"` のうえで、設定に追加します。

```jsonc
// claude_desktop_config.json
{
  "mcpServers": {
    "contents-qa": {
      "command": "cqs-mcp",
      "env": { "CQS_DATA_DIR": "/path/to/contents-qa-system/data" }
    }
  }
}
```

Claude Code なら:

```bash
claude mcp add contents-qa --env CQS_DATA_DIR=$PWD/data -- cqs-mcp
```

提供するツール:

| 分類 | ツール |
|---|---|
| 検索（中核の3つ） | `search_claims` / `search_sources` / `get_source` |
| 見取り図 | `list_works` / `list_entities` / `related_entities` / `gather_for_question` |
| 取り込み | `fetch_url` / `add_document` / `add_ai_report` |
| 照合 | `list_candidates` / `verify_candidates` |
| 知識層の編集 | `add_claim` / `link_claims` / `add_entity` / `propose_claims` |

返却値には確認状態・出典種別・その扱い方（`handling`）が必ず入ります。
回答側はその値に従い、公式確認は断定、記事のみは出典付き、ファン解釈は解釈として述べます。

## 取得の設定

- **robots.txt は既定では確認しません**。`CQS_RESPECT_ROBOTS=1`（または `--robots`）で確認するようになり、
  拒否されたURLは取得しなくなります。
- **同一ホストへの連続アクセス間隔（既定1.5秒）はどの設定でも常に効きます**。相手サイトへの負荷を決めるのは
  robots.txt の有無ではなくアクセス頻度なので、こちらは切れないようにしてあります。
- `405` や接続リセットで弾かれるサイトは `CQS_USER_AGENT` を変えると通ることがあります。
- **公式アカウントかどうかはホスト名では決まりません**。`x.com` は既定でファン扱いにし、公式アカウントは
  作品ごとのルールでアカウント名まで含めて指定します（`cqs -w <作品> rule "x.com/Cho_KaguyaHime" official_sns`）。
- **X（旧Twitter）は FxTwitter API 経由で取得します**。X 本体は通常の取得だと本文を返さないためです。
  - `https://x.com/<user>` → プロフィール（公式アカウントの概要・固定情報）
  - `https://x.com/<user>/status/<id>` → 投稿の本文・投稿者・投稿日時・引用元・反応数
  - タイムラインの一覧取得はできないので、投稿URLは個別に渡します。
- 取得した本文は各作品のDBファイルにのみ保存され、このリポジトリには入りません（`.gitignore` 済み）。

## リポジトリに入るもの／入らないもの

| | |
|---|---|
| push する | ソースコード、テスト、設計メモ、取り込みスクリプトの例 |
| push しない | `data/*.db`（原文スナップショットと抽出した主張）、`.env`、取得キャッシュ |

第三者の文章をそのまま保持するため、DBファイルは私的利用の範囲にとどめてください。

## 開発

```bash
pip install -e ".[dev]"
pytest
```

## ドキュメント

- [`docs/sourcing.md`](docs/sourcing.md) — 出典の集め方。媒体ごとの取り方、取れないときの回避策、分類の落とし穴
- [`docs/design.md`](docs/design.md) — 最初の設計メモ
- [`docs/design-review.md`](docs/design-review.md) — 実装して分かったこと、設計から変えた点、未解決事項

## ライセンス

MIT License（[LICENSE](LICENSE)）。
