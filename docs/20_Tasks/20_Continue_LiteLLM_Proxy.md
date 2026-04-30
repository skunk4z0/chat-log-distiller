# Continue.dev + LiteLLM Proxy 運用ガイド

## 概要

chat-log-distiller は **LiteLLM Proxy** を経由して Continue.dev から無料API（Gemini/Groq/Mistral/OpenRouter）を利用します。

```
Continue.dev → LiteLLM Proxy (port 8000) → 各無料プロバイダ
```

## ファイル構成

| ファイル | 説明 |
|----------|------|
| `continue_proxy_config.yaml` | LiteLLM Proxy 設定（モデル一覧・優先順位・ルーティング） |
| `start-vibe-proxy` | プロキシ起動スクリプト（bash） |
| `scripts/litellm_router.py` | Python から直接 LiteLLM Router を使う場合 |

## 前提条件

### 1. 環境変数の設定

`.env` ファイルに以下を設定：

```bash
# Gemini（必須）
GEMINI_API_KEY=your_gemini_key

# Groq（必須）
GROQ_API_KEY=your_groq_key

# Mistral（任意）
MISTRAL_API_KEY=your_mistral_key

# OpenRouter（任意）
OPENROUTER_API_KEY=your_openrouter_key
```

### 2. LiteLLM のインストール

```bash
pip install litellm
```

## 起動方法

### 方法A: start-vibe-proxy スクリプト

```bash
./start-vibe-proxy
```

※ 現在の内容はプレースホルダーで、実装が必要です

### 方法B: 直接 LiteLLM コマンド

```bash
litellm --config continue_proxy_config.yaml
```

## Continue.dev 設定

`continue_proxy_config.yaml` で定義されたモデル名：

```yaml
model_name: "vibe-master-free"
```

Continue.dev の `config.json` で以下を指定：

```json
{
  "models": [{
    "model": "vibe-master-free",
    "apiBase": "http://localhost:8000",
    "apiKey": "null"
  }]
}
```

## ルーティング戦略

### 現在の設定（free tier 向け）

```yaml
router_settings:
  routing_strategy: "simple-shuffle"  # latency 不安定回避
  num_retries: 3
  timeout: 60
  allowed_fails: 5
```

### 優先順位（order）

| 優先度 | プロバイダ | モデル |
|--------|------------|--------|
| 1 | gemini | gemini-2.5-flash-lite |
| 2 | gemini | gemini-2.5-flash |
| 3 | gemini | gemini-3.1-flash-lite-preview |
| 4 | groq | llama-3.1-8b-instant |
| 5 | groq | llama-3.3-70b-versatile |
| ... | ... | ... |
| 11 | openrouter | auto (fallback) |

### フォールバックチェーン

429 エラー発生時の自動フォールバック：

```
gemini-2.5-flash (429) → groq/llama-3.3-70b-versatile → openrouter/auto
groq/* (429) → gemini-2.5-flash → openrouter/auto
mistral/* (429) → groq/llama-3.3-70b-versatile → openrouter/auto
```

## トラブルシューティング

### プロキシが起動しない

```bash
# API Key 確認
echo $GEMINI_API_KEY
echo $GROQ_API_KEY

# ログ確認
litellm --config continue_proxy_config.yaml --verbose
```

### 429 エラーが頻発

- `api_limits.json` で RPM/TPM を確認
- プロバイダ切り替わりを確認：`router.model_list` で現在有効なモデルを確認

### Continue.dev から接続できない

1. プロキシが `localhost:8000` で起動しているか確認
2. Continue.dev の `apiBase` が `http://localhost:8000` か確認
3. モデル名が `vibe-master-free` と一致するか確認

## 参考

- [LiteLLM 公式ドキュメント](https://docs.litellm.ai/)
- [Continue.dev ドキュメント](https://continue.dev/)