# MCPサーバーとして使う

MCP（Model Context Protocol）に対応したツールから、知識層と原文層を直接引けます。
**この使い方ならAPIキーは要りません。** 検索はこのシステムが行い、回答はつないだ側のAIが書くためです。

```bash
pip install -e ".[mcp]"      # mcp パッケージが要る（唯一の依存）
```

`CQS_DATA_DIR` に作品DBの置き場所を指定します。指定しなければカレントディレクトリの `data/` です。

## Claude Code

```bash
claude mcp add contents-qa --env CQS_DATA_DIR=$PWD/data -- cqs-mcp
```

## Claude Desktop

`claude_desktop_config.json`（macOS は `~/Library/Application Support/Claude/`、
Windows は `%APPDATA%\Claude\`）に追記します。

```jsonc
{
  "mcpServers": {
    "contents-qa": {
      "command": "cqs-mcp",
      "env": { "CQS_DATA_DIR": "/path/to/contents-qa-system/data" }
    }
  }
}
```

## Codex CLI

コマンドで追加するか、

```bash
codex mcp add contents-qa --command cqs-mcp --env CQS_DATA_DIR=/path/to/contents-qa-system/data
codex mcp list
```

`~/.codex/config.toml`（プロジェクト単位なら `.codex/config.toml`）に直接書きます。

```toml
[mcp_servers.contents-qa]
command = "cqs-mcp"

[mcp_servers.contents-qa.env]
CQS_DATA_DIR = "/path/to/contents-qa-system/data"
```

## その他のクライアント（Cursor、VS Code、Cline、Zed など）

多くは Claude Desktop と同じ `mcpServers` 形式のJSONを使います。書き込む場所が違うだけです。

```jsonc
{
  "mcpServers": {
    "contents-qa": {
      "command": "cqs-mcp",
      "args": [],
      "env": { "CQS_DATA_DIR": "/path/to/contents-qa-system/data" }
    }
  }
}
```

仮想環境に入れた場合は、`command` を絶対パス（`/path/to/.venv/bin/cqs-mcp`）にすると確実です。
`cqs-mcp` は標準入出力で話すので、動作確認は次で足ります。

```bash
CQS_DATA_DIR=$PWD/data cqs-mcp   # 何も出力せず待てば起動している（Ctrl-C で終了）
```

## 使い方の勘所

つないだ側のAIには、次のように頼むと噛み合います。

- 「contents-qa で『作品名』の○○について調べて」
- 「`gather_for_question` で材料を集めてから答えて」（1回で材料一式が返ります）

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
