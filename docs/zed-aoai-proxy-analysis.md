# Zed + Azure OpenAI Proxy 分析メモ

## 目的

このプロジェクトの目的は、Zed からは OpenAI 互換 API サーバとして見え、Azure OpenAI からは Entra ID 認証済みクライアントとして見えるプロキシを実装すること。

前提:

- Azure OpenAI 側では API キー認証が無効
- `az login` 済み
- Azure OpenAI 上に `gpt-5.4` デプロイあり
- Zed の LLM Provider として利用したい

---

## Zed の LLM Provider ルート整理

Zed で GPT-5.4 系モデルを使うとき、大きく分けて以下のルートがある。

1. OpenAI provider
   - OpenAI の API を直接使う
2. GitHub Copilot provider
   - GitHub Copilot / Copilot Chat 系 API を使う
3. OpenAI compatible provider
   - ユーザー定義の OpenAI 互換 endpoint を使う
   - このプロジェクトの `aoai_proxy` はこのルート

今回の主調査対象は **3. OpenAI compatible provider** である。

ただし、OpenAI compatible provider は内部で OpenAI 系の request / response 変換ロジックを再利用しているため、補助的に以下も参照した。

- `crates/language_models/src/provider/open_ai.rs`

一方で、GitHub Copilot provider の本格的な比較調査はまだ限定的であり、主に以下が対象になる。

- `crates/language_models/src/provider/copilot_chat.rs`
- `crates/copilot_chat/...`

---

## 安定性比較の現時点での結論

ユーザー観測としては次の比較が重要である。

- 1. OpenAI provider: 安定
- 2. GitHub Copilot provider: 安定
- 3. OpenAI compatible provider (`aoai_proxy`): 不安定

この比較から、問題は Zed の AI Agent 一般機構そのものではなく、**OpenAI compatible provider ルートにおける request / response の意味互換性** にある可能性が高い。

特に注目すべき点:

- OpenAI compatible provider は `/responses` または `/chat/completions` を capability で切り替える
- 今回は `chat_completions: false` で `/responses` を使用している
- Azure OpenAI 側は HTTP 200 を返している
- したがって transport / auth よりも、tool call / tool result 継続ターンの意味差分が本命
- 実装方針としては **responses-first / passthrough-first / minimal-sanitization** が妥当

このため、今後の分析では **1 や 2 と 3 の差分**、特に

- tool result の履歴の持ち方
- `function_call_output` の扱い
- `/responses` ストリームの event semantics
- provider ごとの mapper 差分

を重点的に見るべきである。

---

## 設計の基本方針

理想的な責務は以下。

1. Azure OpenAI には Entra ID Bearer Token 付きでアクセスする
2. Zed には OpenAI 互換 API を提供する
3. Zed からのリクエストは、可能な限りそのまま Azure OpenAI に中継する
4. Azure OpenAI からのレスポンスも、可能な限りそのまま Zed に返す

この方針自体は正しい。

---

## 重要な結論

### 1. `gpt-5.4` は Azure 側では `Responses API` 前提で扱うのが安定

今回の Azure OpenAI デプロイでは:

- `/openai/v1/responses` は成功
- `/openai/deployments/.../chat/completions` は失敗

という挙動だった。

そのため、Zed 側でも `/v1/responses` を使わせる構成が第一候補になる。

---

### 2. Zed の OpenAI Compatible Provider は `chat_completions` 設定で分岐する

Zed の実装上、OpenAI compatible provider はモデル capability に応じて以下を切り替える。

- `chat_completions: true`
  - `/chat/completions`
- `chat_completions: false`
  - `/responses`

今回の推奨設定は以下。

```json
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

現在の `aoai_proxy` 実装も、この設定を前提に **`/v1/responses` を正規ルート** としている。

---

### 3. `/responses` 経路では passthrough-first が正しい

Zed が `/v1/responses` を使うなら、プロキシはまず以下を優先すべき。

- request は極力そのまま Azure `/openai/v1/responses` に流す
- response stream も極力そのまま返す
- ただし `function_call_output.output` についてのみ最小限の request 正規化を行う

特に SSE は途中で再構成するとクライアント互換性を壊しやすいので、response 側は raw passthrough を優先する。

---

## なぜ内部加工が入ったのか

議論の中で「なぜそのまま中継しないのか」という論点が出たが、最終的な方針としては **`/responses` については可能な限りそのまま中継する** に寄せている。

ただし、Azure 側の安定性のため、現在は以下の最小限の加工のみを許容している。

### A. `function_call_output.output` の最小限正規化

長大な tool result や `null` / 空文字 / object / array をそのまま後続 turn に持ち回すと、不安定化を招く可能性があるため、`function_call_output.output` のみ正規化している。

現在の正規化方針:

- `str`
  - そのまま
  - 空文字なら `<Tool returned an empty string>`
- `None`
  - `<Tool returned no output>`
- `dict` / `list` / その他
  - JSON文字列化
- 長すぎるもの
  - 前後を残して切り詰める

### B. `chat/completions` 互換レイヤーは縮小対象

当初は `/chat/completions -> /responses` 変換レイヤーを実装したが、現在の Zed 設定では `chat_completions: false` を前提としているため、これは正規ルートではない。

そのため、現在の実装では `/v1/chat/completions` は **responses-first の方針に反するため未サポート** とし、クライアントには `/v1/responses` を使うよう案内する。

---

## 現在の問題認識

`/responses` に統一しても、Zed の AI Agent 実行では `response failed` が発生することがある。

ただし、ログから分かったこと:

- Azure OpenAI 側は毎回 `200 OK`
- Entra ID 認証も成功
- Zed は `/v1/responses` を実際に叩いている
- tool call と text response は Azure から返ってきている

したがって、少なくとも HTTP レベルや認証レベルの失敗ではない。

---

## Zed 側の実装から分かったこと

### `/responses` では tool call / tool result を履歴として構築する

Zed は `/responses` 向け request を作るとき、単なる message だけでなく以下を input history に含める。

- `message`
- `function_call`
- `function_call_output`

つまり、会話が進むほど tool 履歴が大量に蓄積する。

### 実際の観測ログ

大きな session では次のような request になっていた。

- `input_items=264`
- `tools=25`

内訳:

- `message`: 72
- `function_call`: 96
- `function_call_output`: 96

message role:

- `system`: 1
- `user`: 44
- `assistant`: 27

content type:

- `input_text`: 53
- `output_text`: 27
- `input_image`: 1

これは、Zed agent がかなり長い tool 履歴を `/responses` に毎回再送していることを意味する。

---

## 本命の問題仮説

### 仮説1: `function_call_output` の意味差分

GHC の GPT-5.4 では安定して進むのに、Azure OpenAI の `gpt-5.4` + aoai_proxy では不安定という比較から考えると、最も怪しいのは:

- `function_call_output.output` の形式
- tool result の整形
- tool failure / partial result の扱い

あたり。

### 仮説2: 長大な tool 履歴による継続不安定化

Azure 側は 200 OK でも、長大な `function_call` / `function_call_output` を含む継続会話が、GHC と同じ安定性で動くとは限らない。

### 仮説3: Zed の内部期待との差分

Zed は `/responses` streaming を解釈して `LanguageModelCompletionEvent` に変換しているため、Azure が返す event sequence や後続 turn の意味内容が Zed の期待と微妙にズレると、結果として UI 上は `response failed` になる可能性がある。

---

## Zed 側で確認した重要箇所

### OpenAI Compatible Provider の分岐

- `crates/language_models/src/provider/open_ai_compatible.rs`

`chat_completions` capability に応じて:

- `into_open_ai(...)`
- `into_open_ai_response(...)`

を切り替える。

これは **3. OpenAI compatible provider** の主要実装である。

### OpenAI provider との関係

- `crates/language_models/src/provider/open_ai.rs`

このファイルは 1. OpenAI provider の実装に関係するだけでなく、OpenAI compatible provider が再利用する変換ロジックも持つ。

そのため、今回の調査では「OpenAI provider そのもの」というより、**OpenAI compatible provider が依存している共通変換部** として読んだ。

### GitHub Copilot provider の比較対象

- `crates/language_models/src/provider/copilot_chat.rs`
- `crates/copilot_chat/...`

2. GitHub Copilot provider が安定しているという観測を踏まえると、今後はこのルートと 3. OpenAI compatible provider の差分比較が重要になる。

### `/responses` request の構築

- `crates/language_models/src/provider/open_ai.rs`

`into_open_ai_response(...)` が以下を組み立てる。

- `input`
- `tools`
- `tool_choice`
- `parallel_tool_calls`
- `reasoning`
- `prompt_cache_key`

### `/responses` event の mapping

- `crates/language_models/src/provider/open_ai.rs`

`OpenAiResponseEventMapper` が以下を処理する。

- `response.output_item.added`
- `response.output_text.delta`
- `response.function_call_arguments.delta`
- `response.function_call_arguments.done`
- `response.completed`
- `response.incomplete`
- `response.failed`

### agent 側の error 伝播

- `crates/agent/src/thread.rs`

`CompletionError::Other` が発生すると `event_stream.send_error(...)` に流れ、UI 上は "An Error Happened" に繋がる。

---

## 実装・検証で分かったこと

### 成功したこと

- Docker 化
- Azure CLI を含むイメージ化
- Entra ID 認証
- Azure OpenAI `responses` 呼び出し
- `/responses` passthrough
- basic streaming
- `function_call_output.output` の最小限正規化
- unit test 基盤追加
- responses-first 方針に沿った単体テスト整備

### 特に重要な動作確認

- `GET /v1/models` は成功
- `POST /v1/responses` は成功
- Zed から `/v1/responses` を叩いていることを確認
- Azure 側は 200 OK を返していることを確認
- `/v1/chat/completions` は正規ルートではなく、responses-first 方針に合わせて縮小対象と判断

---

## テスト整備

### 追加したもの

- `tests/test_main.py`

対象:

- `sanitize_responses_request`
- `function_call_output.output` の正規化
- 長い output の切り詰め
- `/responses` URL 構築
- responses-first 方針の確認

### pytest 設定

`pyproject.toml` に以下を追加し、`tests/` のみ探索対象にした。

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
```

これにより、`zed/` 配下の clone した外部 fixture test を誤って実行しないようにした。

---

## 今後の方針

### 方針1: `responses-first`
このプロジェクトでは、Azure `gpt-5.4` 向けに:

- `/responses` を正規ルート
- `/chat/completions` は正規ルートではない

と位置づけるのがよい。

### 方針2: passthrough-first
`/responses` request/response は、可能な限り加工しない。

### 方針3: 必要最小限の stabilization
現在すでに導入済み、または今後必要になる可能性があるものは以下。

- `function_call_output.output` の最小限正規化（導入済み）
- 長い tool 履歴の圧縮
- Azure 向け軽量化
- Zed agent 継続ターン最適化

---

## いまの実務的な結論

このプロキシはすでに以下の用途ではかなり実用に近い。

- Azure OpenAI を Entra ID で使う
- Zed から OpenAI compatible provider として接続する
- GPT-5.4 deployment を `responses` API 経由で使う

一方で、**Zed の AI Agent としての完全な安定動作** には、まだ深い互換調整が必要。

特に次に調べるべき本命は:

1. `function_call_output.output` の実際の形式と正規化の十分性
2. 長大な tool 呼び出し履歴の影響
3. GHC と Azure Responses の tool 継続挙動差
4. `/chat/completions` 削除をいつ実施するか

---

## 推奨事項

### 設定
- `chat_completions: false`
- `tools: true` は必要時のみ
- 未保存変更があるファイルでは edit 系 tool が止まりうる

### 運用
- まず短い prompt で確認
- 新しい session / thread で試す
- 長い tool 履歴を持つ thread は不安定になりやすい可能性がある

### 実装の次手
- `function_call_output` の正規化検討
- 履歴圧縮戦略の検討
- Zed 側の `response failed` 条件の更なる追跡
- GitHub Copilot provider と OpenAI compatible provider の差分比較

### 比較調査の優先順位
1. `copilot_chat.rs` 側で tool result をどう再投入しているか確認
2. OpenAI compatible provider と Copilot provider の mapper 差分を比較
3. provider ごとの `responses` event 処理差分を比較
4. 必要なら `aoai_proxy` 側で Azure 向け最小限の正規化を導入

---

## メモ

今回の議論を通して、単純な「プロキシの通信成否」ではなく、

- Zed の provider 実装
- Responses API の event semantics
- tool call / tool result 履歴の持ち方
- Azure と GHC の振る舞い差

が主要論点であることが明確になった。

このプロジェクトの今後の改善は、HTTP プロキシ層というより、**agent 継続ターンの意味互換性** をどう安定化させるかに寄っていく。