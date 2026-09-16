# LLMの設定

このシステムは**LLMが無くても動きます**。取得・抽出・登録・検索はすべて手元で完結し、
外に出るのは取得先のサイトだけです。LLMを使うのは次の3か所だけで、いずれも代わりの手順があります。

| 使う場所 | LLMが無い場合 |
|---|---|
| `cqs ask` の回答生成 | 材料と**そのまま貼れるプロンプト**を出力します。お使いのAIチャットに貼れば同じ答えになります |
| 質問の計画・次に訊けること | 飛ばして、従来どおりの一発検索になります |
| `images transcribe`（画像の読み取り） | `cqs images save` で画像を保存し、読んだ結果を `cqs add-text --kind image_transcript` で入れます |

**貼って使うのが正式な使い方のひとつです。** キーが無い環境でも機能が削られるわけではなく、
回答を書く相手が自分の使っているチャットに変わるだけです。出典・確認状態・区分の扱い方は
プロンプトに全部入っているので、貼られた側は同じ制約で答えます。

```
$ cqs -w 作品名 ask "ほむらとまどかの関係は？"
# 使った検索語: 暁美, 鹿目, 関係
# 知識層の主張
- [claim 2645] 第1話: まどかの通うクラスにやってきた、一人の転校生・暁美ほむら。
    確認状態: 公式確認（official） / 出典種別: 公式サイト / 区分: TVシリーズ / 出典: https://…
…
LLMの設定が無いため、回答生成は行いませんでした。上のプロンプトを、お使いのAIチャットに
そのまま貼れば同じ答えが得られます。
```

## 自動で答えさせる

提供元は問いません。**OpenAI互換のAPI**（OpenAI本体、LM Studio、Ollama、vLLM、
各種ホスティング）と **AnthropicのMessages API** のどちらでも動きます。
どちらも標準ライブラリのHTTPで叩くので、**追加のパッケージは要りません**。

```bash
# OpenAI
export OPENAI_API_KEY=sk-...
export CQS_MODEL=gpt-4o-mini

# OpenAI互換のローカルサーバ（LM Studio など）。キーは不要なことが多い
export CQS_LLM_BASE_URL=http://localhost:1234/v1
export CQS_MODEL=<そのサーバでのモデル名>

# Ollama
export CQS_LLM_BASE_URL=http://localhost:11434/v1
export CQS_MODEL=qwen3:8b

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
export CQS_MODEL=claude-opus-5
```

### 環境変数

| 変数 | 既定 | 用途 |
|---|---|---|
| `CQS_LLM_PROVIDER` | 自動判定 | `openai` / `anthropic` / `none`。`none` で明示的に切る |
| `CQS_LLM_BASE_URL` | 提供元の既定 | APIの基点。`OPENAI_BASE_URL` / `OPENAI_API_BASE` でも可 |
| `CQS_LLM_API_KEY` | — | APIキー。`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` でも可 |
| `CQS_MODEL` | `gpt-4o-mini` / `claude-opus-5` | モデル名。`OPENAI_MODEL` でも可 |
| `CQS_LLM_TIMEOUT` | `180` | 1回の問い合わせの上限秒数 |

自動判定は「`CQS_LLM_BASE_URL` か `OPENAI_API_KEY` があれば OpenAI互換、
`ANTHROPIC_API_KEY` があれば Anthropic、どちらも無ければ未設定」です。
ローカルのサーバはキーを要らないことが多いので、基点だけでも動きます。

現在の設定は `python -c "import cqs.llm as l; print(l.describe())"` で確認できます。

## 画像の読み取りについて

`images transcribe` は画像をそのままモデルへ送ります。画像を受け取れないモデルでは
失敗するので、その場合は `images save` で保存し、手元のチャットに読ませてください。
