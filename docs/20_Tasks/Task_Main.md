# Task_Main — chat-log-distiller

## 入口

- Vault 横断ハブ: `Obsidian_Vault` / `100_Projects/Task.md` の **Chat Log Distiller** 節
- 実装: `C:\Dev\chat-log-distiller\scripts\distill.py`
- 評価手順: `docs/10_Specs/evaluation.md`

## Phase 1（進行）

- [x] リポジトリ雛形・`distill.py`（チャンク抽出のみ）+ `merge.py`（統合のみ）+ `models.py` / `md_nodes.py`
- [x] コードブロック機械抽出: **markdown-it-py** AST（`fence` / `code_block`）
- [x] チャンキング実装（`scripts/chunker.py`・markdown-it `map` ベース）
- [x] `distill` → `merge` のパイプライン化（`scripts/main.py`・`input/` ポーリング・`output/` / `archive/`）
- [x] `md_nodes`: `tok.map` からソース行をスライスし、`code_snippets` 検証の偽陽性（インデント code_block の正規化差）を解消
- [x] 検証用フィクスチャ（`fixtures/`）・オフライン自己検査（`scripts/selftest_fixtures.py`）・評価手順（`docs/10_Specs/evaluation.md`）

## Phase 2（次・実装バックログ）

- [x] **抽出スキーマ拡張（Signal対応）**: `project` / `tool_context` / `automation_type` / `learning_level` / `source_origin` / `entry_type` を `ChunkExtraction`・`MergedExtraction`・`merge.py`・`main.py`（YAML）まで一貫対応
- [ ] **単一ファイル E2E 完遂（最優先）**: 1ファイルごとに必ず `output+archive`（成功）または `failed/`（失敗）へ着地させ、宙ぶらりんをなくす
- [ ] **`failed/`（または `quarantine/`）**: API・パースで落ちた元ファイルを移し、処理全体は継続（次ファイルへ進む）
- [ ] **Vault 語彙との整合**: `main.py` 出力 YAML を `Obsidian_Vault` の採用語彙（`type` / `subtype` / `area` 等）に合わせるオプション（またはテンプレ後処理スクリプト）
- [ ] **論理整合・二次パス**（方針メモを `10_Specs` に切り出し、必要なら別スクリプト）

## Phase 3（進行中：Proactive Routing / Resume）

- [x] **プロバイダ/モデル別の制限外部管理**: `api_limits.json` を追加し、RPD/RPM/TPM を外部化
- [x] **tiktoken による事前トークン予測**: encoding `cl100k_base`（入力 + `--max-output-tokens`）で予測
- [x] **メモリ上の消費量トラッキング（sliding window 60秒）**: `scripts/waterfall_router.py` の `TokenTracker`
- [x] **全滅時の動的 sleep**: 各プロバイダのウィンドウ（deque）の最古履歴から「最も早く1枠空く時間」を算出して待機
- [x] **先手ウォーターフォール切替**: 送信前に「次リクエストで制限超過」を予測したら即座に次プロバイダへ切替（失敗も消費扱い）
- [x] **巨大チャンクの動的再分割（再帰）**: `scripts/chunker.py` に `rechunk_by_tokens()` を追加し、全プロバイダの TPM に収まるまで分割
- [x] **チャンク単位キャッシュ & レジューム**: `output/.cache_<YYYY-MM-DD_元名.md>/chunk_0000.json` 形式で逐次保存し、既存キャッシュはAPIをスキップ
- [x] **Partial 出力**: 制限中断時に `[Partial]_<元名>.md` を出力し、YAML に `review_status: 進行中（中断）` / `is_partial: true`
- [x] **完了時クリーンアップ**: 100%完了時はキャッシュディレクトリを削除（Partial 時は保持）

## Obsidian 取り込み（後処理）

- [x] `scripts/router.py`: `output/` の構造化 Markdown を Vault（`OBSIDIAN_VAULT_PATH` 配下）へ移動し、YAML を採用語彙へ正規化
  - ファイル名クリーンアップ（連続タイムスタンプ除去）+ 重複時の連番付与（`_1`, `_2`, ...）

## Future Backlog（現時点では見送り）

- [ ] **処理状態の永続化（Resume・強化）**: 未処理 / 成功 / 失敗 を JSON または SQLite で記録し、再実行時の整合をより強固にする（現状は `output/.cache_*` を利用）
