# aoai-proxy

Zed からは OpenAI 互換 API サーバとして見えつつ、Azure OpenAI には Entra ID 認証で接続するための軽量プロキシです。

Azure OpenAI 側で API キー認証を無効化していても、ローカルで `az login` 済みであれば、Zed の OpenAI compatible provider から `gpt-5.4` を利用できます。

## これは何？

このプロキシは、次のような用途を想定しています。

- Azure OpenAI 側では API キー認証を無効化している
- Azure OpenAI には Entra ID 認証でアクセスしたい
- Zed からは OpenAI compatible provider として使いたい
- Azure OpenAI 上の `gpt-5.4` deployment を Zed の AI Agent として使いたい

このプロキシは **responses-first** の方針で実装しています。  
主に `POST /v1/responses` を Zed から受け取り、Azure OpenAI の `Responses API` に中継します。

## 事前準備

以下を準備してください。

- Azure OpenAI resource
- Azure OpenAI 上の `gpt-5.4` deployment
- Azure CLI
- `az login` 済みのローカル環境
- Zed
- Python 3.12+ または Docker / Docker Compose
- Docker Desktop

また、Azure OpenAI の endpoint と deployment 名が必要です。

例:

- endpoint: `https://your-resource.cognitiveservices.azure.com`
- deployment: `gpt-5.4`

## クイックスタート

### 1. 設定ファイルを作る

`.env.example` をコピーして `.env` を作ります。

```/dev/null/sh#L1-1
cp .env.example .env
```

`.env` に最低限以下を設定してください。

```/dev/null/text#L1-3
AOAI_PROXY_AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com
AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT=gpt-5.4
AOAI_PROXY_AZURE_OPENAI_API_VERSION=preview
```

### 2. Docker で起動する

```/dev/null/sh#L1-1
docker compose up --build
```

バックグラウンドで起動する場合:

```/dev/null/sh#L1-1
docker compose up -d --build
```

### 3. 動作確認

ヘルスチェック:

```/dev/null/sh#L1-1
curl http://127.0.0.1:8000/healthz
```

モデル一覧:

```/dev/null/sh#L1-1
curl http://127.0.0.1:8000/v1/models
```

Responses API の簡単な確認:

```/dev/null/sh#L1-7
curl http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4",
    "input": "こんにちは。1文で返答してください。"
  }'
```

### 4. ローカルで直接起動したい場合

依存を入れて起動します。

```/dev/null/sh#L1-2
pip install .
python -m aoai_proxy.main
```

または:

```/dev/null/sh#L1-1
aoai-proxy
```

## Zed の設定方法

Zed では OpenAI compatible provider としてこのプロキシを追加します。

### 重要なポイント

- Base URL は `http://localhost:8000/v1`
- API Key はダミー値でよい
- Model は Azure 側の deployment 名を使う
- **`chat_completions` は `false` にする**

このプロキシは `responses-first` なので、Zed には `/v1/responses` を使わせる構成を推奨します。

### 設定例

```/dev/null/json#L1-20
{
  "language_models": {
    "openai_compatible": {
      "aoai_proxy": {
        "api_url": "http://localhost:8000/v1",
        "available_models": [
          {
            "name": "gpt-5.4",
            "max_tokens": 200000,
            "max_output_tokens": 32000,
            "max_completion_tokens": 200000,
            "capabilities": {
              "tools": true,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false,
              "chat_completions": false
            }
          }
        ]
      }
    }
  }
}
```

### Zed での確認ポイント

- モデル: `gpt-5.4`
- OpenAI compatible provider の接続先: `http://localhost:8000/v1`
- `chat_completions: false`

## 補足

### なぜ `/responses` を使うのか

今回の Azure OpenAI `gpt-5.4` deployment では、`/chat/completions` よりも `Responses API` を使う構成の方が安定していました。

そのため、このプロキシは `/v1/responses` を正規ルートとして扱います。

### どこまで動作確認できているか

少なくとも以下は確認済みです。

- Zed の OpenAI compatible provider から接続できる
- Azure OpenAI へ Entra ID 認証で接続できる
- `POST /v1/responses` が成功する
- Zed の AI Agent で通常応答が表示される
- terminal tool 呼び出しと、その結果を踏まえた応答が返る

### 注意点

- 問題切り分け時は、長い既存 session ではなく **新しい clean session / thread** で確認してください
- 長い session では `function_call` / `function_call_output` の履歴が大量に蓄積し、不安定化の原因になることがあります
- 対象ファイルに未保存変更がある場合、Zed の edit 系 tool は安全のため停止することがあります
- Azure CLI 認証を使うため、Docker 利用時は `~/.azure` をコンテナにマウントする必要があります

### 主な環境変数

必須:

- `AOAI_PROXY_AZURE_OPENAI_ENDPOINT`
- `AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT`

任意:

- `AOAI_PROXY_AZURE_OPENAI_API_VERSION`
- `AOAI_PROXY_AZURE_OPENAI_BEARER_TOKEN`
- `AOAI_PROXY_HOST`
- `AOAI_PROXY_PORT`
- `AOAI_PROXY_LOG_LEVEL`
- `AOAI_PROXY_REQUEST_TIMEOUT_SECONDS`
- `AOAI_PROXY_TOKEN_SCOPE`

## テスト

テスト依存を入れる:

```/dev/null/sh#L1-1
uv sync --extra test
```

テスト実行:

```/dev/null/sh#L1-1
uv run pytest -q
```

## ライセンス

MIT
