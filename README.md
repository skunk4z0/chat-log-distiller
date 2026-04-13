# Chat Log Distiller

生チャットログを **抽出型**（要約禁止）で構造化 JSON にし、のち Obsidian 用 Markdown（YAML）へ載せる前提のツールです。

## セットアップ

```powershell
cd C:\Dev\chat-log-distiller
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy NUL .env
# .env に GOOGLE_API_KEY=...
```

## 使い方（単一チャンク）

```powershell
python scripts/distill.py --chunk-file input\sample.md -o output\sample.extracted.json
```

- `--print-schema` … 送っている JSON Schema を表示して終了。
- `--dry-run` … 文字数だけ確認（API なし）。
- `--prefer-verbatim-fences` … `code_snippets` を **markdown-it-py** のトークン列（`fence` / `code_block`）から抜いたものに**置き換え**（パーサと整合した機械抽出）。
- `python scripts/merge.py chunk1.json chunk2.json -o merged.json` … 複数の `ChunkExtraction` JSON を **LLM なし**で結合（`scripts/merge.py`）。
- `python scripts/chunker.py input\sample.md` … 長文を **AST 上のコード不可分領域**（`fence` / `code_block` の `map`）を守りつつチャンク分割（`--max-chars` / `--overlap-chars`）。
- `GEMINI_MODEL` 環境変数でモデル ID を変更可能（`distill.py` CLI 既定は `gemini-2.0-flash`、**オーケストレーター** `main.py` 既定は `gemini-2.5-flash`。無料枠なら `gemini-3.1-flash` 等も可）。
- `python scripts/main.py --once` … `input/` の `.md` / `.txt` をスキャンし、チャンク分割 → **リクエスト間 `sleep(5)`** 付きで `distill` → `merge` → `output/YYYY-MM-DD_<元ファイル名>.md` に保存し、元ファイルを `archive/` にタイムスタンプ付きで移動。ログは標準出力と `logs/pipeline.log`。`--dry-run` で API なし（チャンク数のみ）。ループは `python scripts/main.py`（既定 60 秒間隔）または `--interval 120`。
- **1ファイルをすばやく通しテスト:** `python scripts/main.py --once --only input\sample.md --fast --no-archive` … 成功チャンク間は既定 **3 秒**（`--fast-inter-chunk-sleep 5` で変更。`0` は連打で 503 になりやすい）。**503/429 時のリトライ待機は省略しない**。成功後も `input` を動かさない。

## 検証（オフライン）

```powershell
python scripts/selftest_fixtures.py
```

フィクスチャは `fixtures/*.md` と `fixtures/chunk_extractions/*.json`。手順の詳細は `docs/10_Specs/evaluation.md`。

## ディレクトリ

| フォルダ     | 用途                         |
|--------------|------------------------------|
| `input/`     | 生ログ断片                   |
| `output/`    | 抽出 JSON 等                 |
| `archive/`   | 処理済み退避                 |
| `fixtures/`  | テスト用 Markdown / merge JSON |
| `scripts/`   | Python                       |
| `docs/`      | 仕様・タスク                 |

## トラブルシューティング

### `ImportError: cannot import name 'genai' from 'google'`

`google-genai` が入っていない（または `pip uninstall google-genai` で消した）状態です。`google` は名前空間パッケージのため、他の `google.*` だけが残るとこのエラーになります。

```powershell
pip install -r requirements.txt
# 最低限: pip install "google-genai>=1.0.0"
```

古い **`google-generativeai`** と新しい **`google-genai`** を混ぜないでください。本リポジトリは **`google-genai`** のみを使います。

## 精度プレビュー（既定より下のモデル）

`main.py` の既定（`gemini-2.5-flash`）より**一段下**で中身を試すなら、まず **`gemini-2.0-flash`** が扱いやすいです（`distill.py` の既定と同じ）。

**1チャンクだけ JSON で見る（最短）:**

```powershell
python scripts/distill.py --chunk-file input\sample.md --model gemini-2.0-flash --prefer-verbatim-fences -o output\preview_2.0.json
```

**パイプライン1本（1ファイル・軽め待機・アーカイブなし）:**

```powershell
python scripts/main.py --once --only input\sample.md --fast --no-archive --model gemini-2.0-flash
```

しばらく同じモデルで試すなら `.env` に `GEMINI_MODEL=gemini-2.0-flash` を書くか、PowerShell で `$env:GEMINI_MODEL="gemini-2.0-flash"` でも可（`main.py` / `distill.py` 双方が参照）。

利用可能な ID は [Gemini API のモデル一覧](https://ai.google.dev/gemini-api/docs/models) を参照（プレビュー名は変わることがあります）。さらに安価に試すなら **`gemini-2.5-flash-lite`** も候補です（利用可否は上記ドキュメントで確認）。
