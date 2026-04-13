# 評価手順（chat-log-distiller）

品質と退行の見方を固定する。大きく **オフライン** と **API あり** に分ける。

## 0. API 制限チェック（実行前・必須）

無料枠では **RPM / TPM / RPD** のいずれか超過で 429 が返る。  
また、混雑時は 503 が返るため、実行前に「今日の残り枠で完走できるか」を確認する。

- 公式の確認先: [Gemini API Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- 実際の上限は AI Studio のプロジェクト設定が正（モデルごとに異なる）。
- このリポジトリで 2026-04-13 に観測した例:
  - `gemini-2.5-flash`: 503（高負荷）連続で失敗
  - `gemini-2.0-flash`: 429 + `GenerateRequestsPerDay... limit: 0`
  - `gemini-2.5-flash-lite`: 429 + `GenerateRequestsPerDay... quotaValue: 20`

### 実行可否の判断式

1. 先に dry-run でチャンク数を確認:

```powershell
python scripts/main.py --once --only <target-file> --dry-run --max-chars <N>
```

2. 判定:
   - `必要リクエスト数 = chunk_count`
   - `必要リクエスト数 <= 当日残RPD` を満たすときのみ実行
   - 超える場合は、その日は実行しない（翌日リセット後に再実行）

### 実務運用の推奨

- まず `--max-chars` を小さくしすぎない（小さすぎると chunk 数が増えて RPD を消費）。
- JSON 末尾欠け（`EOF while parsing a string`）が出る場合のみ `--max-chars` を段階的に下げる。
- `503` は混雑なので時間を空ける。`429` で `...PerDay...` が出たら当日中の再実行は原則停止。
- `failed/` に落ちたファイルを翌日 `--only failed/<file>` で再実行し、成功時に `archive/` へ着地させる。

### RPM/TPM は余裕でも RPD で止まる問題（実測）

大きいログでは 1 ファイル中のチャンク数がそのまま API リクエスト数になるため、  
**RPM/TPM に余裕があっても RPD で先に止まる**ケースがある。

運用ルール:

- 実行前に `dry-run` で `chunk_count` を確認し、`chunk_count <= その日の残RPD` であることを確認。
- `--max-chars` を下げると安定性は上がるが、`chunk_count` 増加で RPD 消費は悪化する。  
  原則は 3500〜5000 で試し、JSON 破損時のみ下げる。
- 複数プロバイダを併用して RPD を分散:
  - 例: `--provider mistral --fallback-providers openrouter,groq,gemini`
- `main.py` は `PerDay` 系 429 を検知したプロバイダを、同一 run 内で自動的にスキップする。

## 1. オフライン（API なし・必須）

フィクスチャ、`chunker`、`md_nodes`（コードブロックのソース整合）、`merge` の不変条件を確認する。

```powershell
cd C:\Dev\chat-log-distiller
python scripts/selftest_fixtures.py
```

期待: 最後に `OK: selftest_fixtures passed` のみ（stderr に FAIL が出ない）。

### 手動: merge フィクスチャ JSON

```powershell
python scripts/merge.py fixtures/chunk_extractions/min_a.json fixtures/chunk_extractions/min_b.json -o output\_merged_eval.json
```

`chunk_count: 2` と `entities` に `Tokio` が含まれることを確認。

## 2. 単一チャンク抽出（API あり・目視）

代表フィクスチャ 1 件で JSON を出し、**要約ではなく引用**になっているか目視する。

```powershell
python scripts/distill.py --chunk-file fixtures/01_minimal_fence.md --prefer-verbatim-fences -o output\_eval_01.json
```

チェックリスト（目安）:

- `entities` がログに出る用語と一致しているか
- `context` が null か、チャンク内の**原文の断片**の連結になっているか（言い換えだけの段落は NG）
- `code_snippets` がフェンス内容と一致しているか（`--prefer-verbatim-fences` 利用時）
- `project` / `automation_type` / `tool_context` が**明示記述のみ**で埋まっているか（推測で増えていないか）
- `learning_level` / `source_origin` / `entry_type` は、明示シグナルがない場合に **null** になっているか

### 2.1 シグナルマッピング（API あり・目視）

`learning_level` / `source_origin` の日本語シグナルが期待どおりに正規化されるか確認する。

最小テストの観点:

- シグナルありチャンク:
  - `[なんとなく]` -> `learning_level: vibe`
  - `[納得]` -> `learning_level: understood`
  - `[完全に理解した]` -> `learning_level: mastered`
  - `[公式]` -> `source_origin: official_doc`
  - `[issue]` -> `source_origin: github_issue`
  - `[AIの嘘]` -> `source_origin: ai_hallucination`
  - `[手作業]` -> `source_origin: manual_test`
- シグナルなしチャンク:
  - `learning_level: null`
  - `source_origin: null`
  - `entry_type: null`

判定ルール:

- 文脈推定だけで値が入っていたら NG（要プロンプト修正）。
- `tool_context` はリストで返ること（単一文字列で返らないこと）。

## 3. パイプライン E2E（API あり・任意）

長文は負荷・503 の影響を受けるため、まず `input/sample.md` または `fixtures` を `input` にコピーして試す。

```powershell
python scripts/main.py --once --only input\sample.md --fast --no-archive --model gemini-2.0-flash
```

出力 `output/YYYY-MM-DD_*.md` の YAML と各 `##` セクションを確認。本番寄りの待機なら `--fast` を外す。

## 4. Vault との接続（運用）

出力ノートの **置き場**・**YAML キー**を Vault の採用語彙（例: `900_System/90_Template/メタデータ値_最終採用語彙.md`）に揃える作業は、リポジトリ外の運用ノートで決めたあと、`main.py` のフロントマター生成を調整する（Phase 2 バックログ: `docs/20_Tasks/Task_Main.md`）。

## 5. 退行時の切り分け

| 症状 | まず見る所 |
|------|------------|
| `selftest` 失敗 | `chunker.py` / `md_nodes.py` / `merge.py` |
| 抽出 JSON の幻覚・言い換え | `distill.py` の system 文面、温度（0）、モデル ID |
| 503 / 429 | モデル負荷・`main.py` の待機・時刻をずらして再実行 |
