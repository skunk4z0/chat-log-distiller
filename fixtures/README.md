# 検証用フィクスチャ（Markdown）

`scripts/selftest_fixtures.py` が読み込み、**API なし**で次を検証する。

| ファイル | 目的 |
|----------|------|
| `01_minimal_fence.md` | 最小フェンス（`fence`） |
| `02_indented_codeblock.md` | インデント `code_block` と `tok.map` スライス整合 |
| `03_turns_and_fence.md` | 見出し風ターン + JSON フェンス |

## JSON（merge 手動試験用）

`chunk_extractions/*.json` … `merge.py` に渡す `ChunkExtraction` 1 オブジェクトずつ。

```powershell
python scripts/merge.py fixtures/chunk_extractions/min_a.json fixtures/chunk_extractions/min_b.json -o output\_merged_fixture.json
```
