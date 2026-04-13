# 評価手順（chat-log-distiller）

品質と退行の見方を固定する。大きく **オフライン** と **API あり** に分ける。

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
