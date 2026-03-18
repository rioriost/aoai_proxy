# aoai-proxy

Azure OpenAI に対して Entra ID 認証でアクセスしつつ、クライアント側には OpenAI 互換 API として振る舞う軽量プロキシです。

この用途では、Zed などの OpenAI 互換エンドポイントを利用できるクライアントから `api_key` をダミー値で設定して接続し、実際の Azure OpenAI への認証はこのプロキシが `az login` 済みの Azure CLI 資格情報を使って行います。

## 想定ユースケース

- Azure OpenAI 側で API キー認証を無効化している
- ローカルでは `az login` 済み
- Zed から OpenAI 互換 API として接続したい
- Azure OpenAI 上の `GPT-5.4` デプロイを AI エージェント用途で使いたい

## 仕組み

このプロキシは以下のように動作します。

1. クライアントは OpenAI 互換 API としてこのプロキシへ接続
2. プロキシは `AzureCliCredential` を使って Entra ID アクセストークンを取得
3. Azure OpenAI へ `Authorization: Bearer ...` で転送
4. Azure OpenAI のレスポンスをそのままクライアントへ返却

現状、主に以下の OpenAI 互換パスを想定しています。

- `GET /v1/models`
- `POST /v1/responses`
- `POST /v1/embeddings`

このプロキシは **responses-first** の方針で実装しています。  
特に Zed からの利用では `POST /v1/responses` を正規ルートとして扱います。

## 前提条件

- Azure CLI が利用可能
- `az login` 済み
- 対象 Azure OpenAI リソースに対して必要な権限がある
- Azure OpenAI に `GPT-5.4` のデプロイを作成済み
- Python 3.12+ または Docker が利用可能

## 設定

環境変数は `AOAI_PROXY_` プレフィックス付きで指定します。

### 必須

- `AOAI_PROXY_AZURE_OPENAI_ENDPOINT`  
  Azure OpenAI のエンドポイント  
  例: `https://your-resource.openai.azure.com`

- `AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT`  
  利用する Azure OpenAI デプロイ名  
  例: `gpt-5-4`

### 任意

- `AOAI_PROXY_AZURE_OPENAI_API_VERSION`  
  Azure OpenAI の API バージョン  
  デフォルト: `preview`

- `AOAI_PROXY_HOST`  
  待受ホスト  
  デフォルト: `0.0.0.0`

- `AOAI_PROXY_PORT`  
  待受ポート  
  デフォルト: `8000`

- `AOAI_PROXY_LOG_LEVEL`  
  ログレベル  
  デフォルト: `INFO`

- `AOAI_PROXY_REQUEST_TIMEOUT_SECONDS`  
  Azure OpenAI へのリクエストタイムアウト秒数  
  デフォルト: `600`

- `AOAI_PROXY_TOKEN_SCOPE`  
  トークン取得時のスコープ  
  デフォルト: `https://cognitiveservices.azure.com/.default`

- `AOAI_PROXY_AZURE_OPENAI_BEARER_TOKEN`  
  Azure OpenAI へ転送する Bearer token を明示指定する場合に使います  
  指定した場合は Azure CLI による token 取得より優先されます

## 現在の動作状況

このプロキシは、少なくとも以下の構成で動作確認済みです。

- Azure OpenAI 認証: Entra ID
- 資格情報取得: `AzureCliCredential`
- Azure OpenAI エンドポイント: `https://rifujita-5627-resource.cognitiveservices.azure.com`
- Azure OpenAI デプロイ名: `gpt-5.4`
- クライアント: Zed の OpenAI compatible provider
- Zed 側設定: `chat_completions: false`

重要な点として、今回の `gpt-5.4` デプロイは Azure 側では `Responses API` を使う構成が安定しており、`/chat/completions` を直接使うより `/responses` を使うほうが適しています。

## ローカル実行

依存関係をインストール:

```sh
pip install .
```

環境変数を設定して起動:

```sh
export AOAI_PROXY_AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com"
export AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT="gpt-5-4"
export AOAI_PROXY_AZURE_OPENAI_API_VERSION="preview"

python -m aoai_proxy.main
```

またはエントリーポイント経由:

```sh
aoai-proxy
```

起動後のヘルスチェック:

```sh
curl http://localhost:8000/healthz
```

## Docker で使う

### イメージをビルド

```sh
docker build -t aoai-proxy .
```

### コンテナ起動

Azure CLI の認証情報をコンテナから使える必要があります。  
もっとも簡単なのは、ホスト側の Azure CLI 設定ディレクトリをマウントする方法です。

```sh
docker run --rm -p 8000:8000 \
  -e AOAI_PROXY_AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com" \
  -e AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT="gpt-5-4" \
  -e AOAI_PROXY_AZURE_OPENAI_API_VERSION="preview" \
  -v "$HOME/.azure:/root/.azure" \
  aoai-proxy
```

### Docker Compose で使う

`.env.example` をコピーして `.env` を作成します。

```sh
cp .env.example .env
```

必要に応じて `.env` を編集します。

```sh
AOAI_PROXY_AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT=gpt-5-4
AOAI_PROXY_AZURE_OPENAI_API_VERSION=preview
AOAI_PROXY_PORT=8000
AOAI_PROXY_LOG_LEVEL=INFO
AOAI_PROXY_REQUEST_TIMEOUT_SECONDS=600
AOAI_PROXY_TOKEN_SCOPE=https://cognitiveservices.azure.com/.default
```

起動:

```sh
docker compose up --build
```

バックグラウンド起動:

```sh
docker compose up -d --build
```

停止:

```sh
docker compose down
```

### 注意点

- コンテナ内では `AzureCliCredential` が Azure CLI のログイン状態を参照します
- そのため、通常はホストの `~/.azure` をマウントする必要があります
- Azure CLI は `~/.azure` 配下に `versionCheck.json` や `commands/*.log` などを書き込むため、`read-only` マウントでは動作しません
- `docker-compose.yml` では `${HOME}/.azure:/root/.azure` を使って読み書き可能でマウントします
- `docker run` を使う場合も `-v "$HOME/.azure:/root/.azure"` のように `:ro` を付けないでください
- 必要に応じて Azure CLI バイナリ自体を含む構成に拡張することもできますが、この実装では主に既存ログイン情報の参照を前提としています
- Docker Desktop や実行環境によっては、追加の調整が必要になる場合があります

もしコンテナ内で `AzureCliCredential` が期待どおり動かない場合は、ホスト上で直接 `python -m aoai_proxy.main` を実行する構成のほうがシンプルです。

## Zed との互換性メモ

Zed の OpenAI compatible provider を使う場合、少なくとも今回の構成では `Supports /chat/completions` を無効にし、`/responses` を使わせる構成のほうが適しています。

設定イメージ:

- API URL: `http://localhost:8000/v1`
- API Key: 任意のダミー値
- Model: `gpt-5.4`
- `chat_completions`: `false`

補足:

- `chat_completions: true` の場合、Zed は `/v1/chat/completions` を使います
- このプロキシには `/chat/completions -> /responses` の変換レイヤーも入れています
- ただし、GPT-5.4 の Azure デプロイでは `/responses` を直接使うほうが自然です
- AI agent として使う場合、Zed は多量の `tools` と長い履歴を含む `/responses` リクエストを送ることがあります
- 対象ファイルに未保存変更があると、Zed 側の edit 系ツールが安全のため停止することがあります

## 動作確認

### `GET /v1/models`

```sh
curl http://localhost:8000/v1/models
```

### `POST /v1/responses`

```sh
curl http://localhost:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4",
    "input": "こんにちは。1文で返答してください。"
  }'
```

### `POST /v1/responses` with structured input

```sh
curl http://localhost:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4",
    "stream": true,
    "input": [
      {
        "type": "message",
        "role": "user",
        "content": [
          { "type": "input_text", "text": "Hello!" }
        ]
      }
    ]
  }'
```

## Zed から使う

Zed 側では OpenAI 互換プロバイダとしてこのプロキシを指定します。

考え方としては次の通りです。

- Base URL: `http://localhost:8000/v1`
- Model: `gpt-5.4` または Azure 側のデプロイ名
- API Key: 任意のダミー文字列で可
- `Supports /chat/completions`: 無効
- `chat_completions`: `false`

例:

- Base URL: `http://127.0.0.1:8000/v1`
- API Key: `dummy`
- Model: `gpt-5.4`
- `chat_completions`: `false`

クライアントによっては `Authorization: Bearer <api_key>` を必須で送るものがありますが、このプロキシはクライアントからの API キーを Azure OpenAI には使いません。Azure 向けには Entra ID トークンへ差し替えて転送します。

## GPT-5.4 デプロイについて

このプロキシは、実際に Azure OpenAI 上で作成したデプロイ名を使ってリクエストを転送します。  
そのため、`GPT-5.4` を使いたい場合は、Azure OpenAI で対象モデルをデプロイし、そのデプロイ名を `AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT` に設定してください。

例:

- Azure モデル: `GPT-5.4`
- Azure デプロイ名: `gpt-5-4`

このとき環境変数は以下のようになります。

```sh
export AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT="gpt-5-4"
```

## 実装上の補足

- `GET /v1/models` は静的に 1 モデルを返します
- 実際の Azure OpenAI への転送時は、設定されたデプロイ名を使用します
- `/v1/responses` は Azure OpenAI の `openai/v1/responses` に転送します
- `/v1/responses` は raw SSE passthrough を優先し、上流の Responses API イベント列を極力そのまま返します
- `function_call_output.output` は Azure 側の安定性向上のため、最小限の正規化を行います
- `stream: true` を含む JSON リクエストはストリーミングとして上流へ転送します
- ストリーミングレスポンスは `StreamingResponse` でクライアントへそのまま返します
- `/v1/chat/completions` はこのプロキシの正規ルートではありません
- `/v1/embeddings` は Azure OpenAI の deployment ベースのエンドポイントに転送します
- `OPTIONS` を含む一般的な OpenAI 互換クライアントの呼び出しにも対応します
- それ以外のパスは、そのまま Azure OpenAI 側へ中継しますが、互換性はエンドポイント次第です

## トラブルシュート

### 1. 401 / 403 になる

確認ポイント:

- `az login` 済みか
- 正しい Azure テナントでログインしているか
- Azure OpenAI リソースへの権限があるか
- `AOAI_PROXY_AZURE_OPENAI_ENDPOINT` が正しいか

Azure CLI の状態確認:

```sh
az account show
```

### 2. モデルが見つからない

- `AOAI_PROXY_AZURE_OPENAI_DEPLOYMENT` に指定した値が Azure 上のデプロイ名と一致しているか確認してください
- モデル名そのものではなく、デプロイ名が必要です

### 3. Docker では動かないがローカルでは動く

- コンテナ内に Azure CLI が存在するか
- `~/.azure` のマウントが正しいか
- `~/.azure` を `:ro` 付きで read-only マウントしていないか
- コンテナ内から Azure CLI 資格情報が参照可能か
- `AOAI_PROXY_AZURE_OPENAI_BEARER_TOKEN` を使うと Azure CLI 依存を避けられます
- まずはホスト実行で動作確認してから Docker 化すると切り分けしやすいです

### 4. Zed から接続できない / `response failed` になる

- Base URL が `http://localhost:8000/v1` になっているか
- モデル名に Azure デプロイ名を指定しているか
- `chat_completions` を `false` にしているか
- Zed が `/v1/responses` を使っているか
- 対象ファイルに未保存変更があり、edit 系 tool が止まっていないか
- tool 呼び出し履歴が長大になっていないか
- ローカルファイアウォールやポート競合がないか

## セキュリティ上の注意

- このプロキシ自体にはクライアント認証を入れていません
- ローカル利用または信頼できるネットワーク内利用を前提にしてください
- 外部公開する場合は、少なくともリバースプロキシ・IP 制限・認証を追加してください
- Azure CLI の認証情報ディレクトリを扱うため、コンテナ共有時はアクセス権に注意してください

## テスト

テスト依存を入れる:

```sh
uv sync --extra test
```

テスト実行:

```sh
uv run pytest -q
```

pytest は `tests/` 配下のみを対象にするよう設定済みです。  
そのため、同じリポジトリ内に clone した別プロジェクトや fixture のテストを誤って拾わない構成になっています。

現在は主に以下の単体テストを入れています。

- `chat/completions -> responses` 変換
- `responses -> chat.completions` 変換
- streaming event の変換補助関数

## 今後の拡張候補

- `/v1/responses` の安定化と Zed agent 向け最適化
- `function_call_output` の追加正規化
- 長大な tool 呼び出し履歴に対する圧縮・最適化
- API キーや Basic 認証によるプロキシ自身の保護
- 複数デプロイの動的ルーティング
- ヘッダーや監査ログの強化
- モデル名と Azure デプロイ名のマッピング機能
- 必要なら `/v1/chat/completions` の完全削除

## ライセンス

必要に応じて追加してください。