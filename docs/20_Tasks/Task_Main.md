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

## Future Backlog（現時点では見送り）

- [ ] **処理状態の永続化（Resume）**: 未処理 / 成功 / 失敗 を JSON または SQLite で記録し、クラッシュ後再開を可能にする（現行運用ボリュームではオーバースペック）
