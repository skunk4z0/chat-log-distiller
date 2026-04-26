# Structured Log Format Guide for AI Agents

## Purpose

This document explains the output format produced by `chat-log-distiller` so that another AI agent can:

- correctly parse structured log files,
- understand why each field/section exists,
- propose practical reuse workflows in Obsidian.

The target output files are Markdown notes with YAML front matter, generated in `output/` and moved into an Obsidian vault by `scripts/router.py`.

---

## Design Philosophy

The pipeline is optimized for **knowledge preservation under noisy chat logs**.

Core principles:

1. **Preserve factual density**
   - Keep concrete identifiers (library names, versions, error codes, file paths).
   - Avoid vague summaries that remove troubleshooting value.
2. **Enable machine-readable management**
   - Store metadata in YAML front matter for Dataview and automated routing.
3. **Keep human-readable evidence**
   - Preserve contextual excerpts and code snippets in Markdown body.
4. **Support incremental recovery**
   - Chunk-level processing/caching and retry/fallback routing are used to survive API failures.

---

## File Lifecycle (Operational Context)

1. Raw logs are placed into `input/` (or imported from `01_Raw` by `import_and_run.py`).
2. `scripts/main.py` distills chunk extractions and merges them into one Markdown file.
3. File is written to `output/YYYY-MM-DD_<source>.md`.
4. `scripts/router.py` normalizes front matter and moves file to Obsidian:
   - destination filename: `<source-date>_structured.md` (with suffix numbering on conflict)
   - destination directory: `300_Resources/AI_Logs/02_Structured`

---

## Output Schema (YAML Front Matter)

Typical front matter keys:

- `tags`: list of tags for search/classification.
- `topic`: short top-level topic label.
- `status`: workflow status (mapped to Japanese values by router).
- `type`, `subtype`, `area`, `review_status`: Obsidian management fields.
- `source_log`: original raw source path.
- `distilled_at`: UTC timestamp when distillation completed.
- `chunk_count`: number of distilled chunks.
- `model`: primary model label used by pipeline context.

Signal/semantic fields (may be null/empty depending on evidence):

- `プロジェクト` (from `project`)
- `自動化種別` (from `automation_type`)
- `理解度` (from `learning_level`)
- `情報源` (from `source_origin`)
- `エントリ種別` (from `entry_type`)

Important normalization behavior:

- `scripts/router.py` renames selected English keys to Japanese keys.
- `status` values are normalized via mapping table.
- tags may be merged/deduplicated from multiple metadata fields.

---

## Output Body Structure (Markdown Sections)

The body is designed for both quick scanning and evidence traceability:

1. `## Entities`
   - deduplicated technical nouns, product names, versions, error names.
2. `## Decisions`
   - adopted decisions/confirmed directions extracted from conversation.
3. `## Rejected ideas`
   - failed/rejected approaches with reasons.
4. `## Context`
   - per-chunk evidence highlights + collapsible raw context.
5. `## Code snippets`
   - extracted code blocks (intended to preserve reusable fragments).

Interpretation guidance for AI:

- Treat `Entities` and `Decisions` as indexable signals for retrieval/recommendation.
- Treat `Context` as evidence support (not necessarily concise summary).
- Treat `Rejected ideas` as anti-pattern memory.

---

## Why This Structure Exists

This format balances two competing requirements:

- **Operational retrieval** in Obsidian (Dataview-friendly front matter).
- **Post-mortem reliability** for debugging (raw evidence retained in body).

Chat logs often include trial/error loops, contradictory paths, and partial failures.
Therefore, the format intentionally keeps:

- explicit rejected paths,
- model/provider failure traces reflected in resulting knowledge,
- enough source context to avoid hallucinated reinterpretation.

---

## Expected Obsidian Usage

Primary usage in Obsidian:

1. **Dataview filtering**
   - filter by `type`, `review_status`, `プロジェクト`, `理解度`, `情報源`, tags.
2. **Troubleshooting recall**
   - search by `Entities` terms (error name, tool, package, API).
3. **Decision intelligence**
   - query `Rejected ideas` and `Decisions` to avoid repeated failure paths.
4. **Daily review workflow**
   - review newly generated `_structured` files and mark status progression.

Recommended query ideas for AI to suggest:

- "Show unread structured logs for project X."
- "List logs where source_origin indicates official documentation."
- "Find logs containing entity Y and rejected idea reason matching Z."
- "Find recurring error-related entities over the last N days."

---

## AI Agent Guidance: How to Propose Reuse

When another AI reads these files, it should:

1. Parse YAML first (management/filtering layer).
2. Use section semantics (`Entities`, `Decisions`, `Rejected ideas`) for structured reasoning.
3. Use `Context` and snippets as evidence before making recommendations.
4. Prefer proposals that can be implemented in Obsidian workflows:
   - Dataview dashboards
   - review queue design
   - tag normalization
   - anti-pattern alert lists

Avoid:

- Re-summarizing away concrete technical facts.
- Ignoring rejected paths (they are core value, not noise).

---

## Caveats for AI Interpretation

- Some fields may be null due to strict evidence-only extraction.
- Value vocabularies may be partially normalized (English/Japanese mix can exist).
- Large logs are chunked; not all relationships are guaranteed to be globally canonical.
- Router normalization may change visible key names compared to raw distillation output.

---

## Minimal Example (Conceptual)

```markdown
---
tags: [chat-distilled, distilled-log, api, python]
status: 進行中
type: ログ
subtype: 構造化ログ
area: リソース
source_log: input/2026-04-11_ai_raw_gemini.md
distilled_at: 2026-04-15T08:31:36Z
chunk_count: 3
モデル: gemini-2.5-flash-lite
プロジェクト: Chat Log Distiller
理解度: mastered
情報源: official_doc
---

## Entities
- Gemini API
- RESOURCE_EXHAUSTED
- Dataview

## Decisions
- Use fallback providers on transient API failures.

## Rejected ideas
- **Idea:** Retry same provider indefinitely
  - **Reason:** Repeated quota/transient failures reduce throughput.
```

---

## Summary for AI Consumers

Use this format as a **hybrid of metadata index + evidence notebook**.
It is not just a summary artifact; it is a recoverable operational memory store intended for:

- retrieval,
- decision reuse,
- anti-pattern prevention,
- Obsidian-native knowledge operations.
