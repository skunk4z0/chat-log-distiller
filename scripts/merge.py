#!/usr/bin/env python3
"""
Merge multiple ChunkExtraction JSON files (one object per file) into one MergedExtraction.

Pure merge — no LLM. Chunk extractor remains scripts/distill.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from models import ChunkExtraction, CodeSnippet, MergedExtraction, RejectedIdea

_ADAPTER: Final = TypeAdapter(ChunkExtraction)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _dedupe_adjacent_decisions(items: list[str]) -> list[str]:
    out: list[str] = []
    for x in items:
        if not out or out[-1] != x:
            out.append(x)
    return out


def _pick_first_non_null(values: list[str | None]) -> str | None:
    for v in values:
        if v is not None:
            return v
    return None


def merge_chunk_extractions(chunks: list[ChunkExtraction]) -> MergedExtraction:
    entities: list[str] = []
    contexts: list[str | None] = []
    decisions: list[str] = []
    projects: list[str | None] = []
    tool_context: list[str] = []
    automation_types: list[str | None] = []
    learning_levels: list[str | None] = []
    source_origins: list[str | None] = []
    entry_types: list[str | None] = []
    rejected: list[RejectedIdea] = []
    codes: list[CodeSnippet] = []

    for ch in chunks:
        entities.extend(ch.entities)
        contexts.append(ch.context)
        decisions.extend(ch.decisions)
        projects.append(ch.project)
        tool_context.extend(ch.tool_context)
        automation_types.append(ch.automation_type)
        learning_levels.append(ch.learning_level)
        source_origins.append(ch.source_origin)
        entry_types.append(ch.entry_type)
        rejected.extend(ch.rejected_ideas)
        codes.extend(ch.code_snippets)

    return MergedExtraction(
        chunk_count=len(chunks),
        entities=_dedupe_preserve_order(entities),
        contexts=contexts,
        decisions=_dedupe_adjacent_decisions(decisions),
        project=_pick_first_non_null(projects),
        tool_context=_dedupe_preserve_order(tool_context),
        automation_type=_pick_first_non_null(automation_types),
        learning_level=_pick_first_non_null(learning_levels),
        source_origin=_pick_first_non_null(source_origins),
        entry_type=_pick_first_non_null(entry_types),
        rejected_ideas=rejected,
        code_snippets=codes,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge ChunkExtraction JSON files.")
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Paths to JSON files each containing one ChunkExtraction object.",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        help="Write merged JSON here (default: stdout).",
    )
    args = parser.parse_args(argv)

    chunks: list[ChunkExtraction] = []
    for p in args.inputs:
        raw = json.loads(p.read_text(encoding="utf-8"))
        chunks.append(_ADAPTER.validate_python(raw))

    merged = merge_chunk_extractions(chunks)
    text = merged.model_dump_json(indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
