#!/usr/bin/env python3
"""
Offline regression checks: fixtures + chunker + md_nodes + merge.

No Gemini API. Run from repo root:
  python scripts/selftest_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import chunker
import merge
from md_nodes import fenced_blocks_from_markdown
from models import ChunkExtraction, CodeSnippet


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def test_fixture_md_substrings() -> None:
    fix_dir = _REPO / "fixtures"
    if not fix_dir.is_dir():
        _fail(f"missing fixtures dir: {fix_dir}")
    md_files = sorted(fix_dir.glob("*.md"))
    if not md_files:
        _fail("no fixtures/*.md")

    for path in md_files:
        text = path.read_text(encoding="utf-8")
        for i, (lang, body) in enumerate(fenced_blocks_from_markdown(text)):
            if body and body not in text:
                _fail(f"{path.name}: fence[{i}] lang={lang!r} body not substring of file (len={len(body)})")

        chunks = chunker.chunk_markdown(text, max_chars=8000, overlap_chars=500)
        if not chunks:
            _fail(f"{path.name}: chunk_markdown returned empty")
        for ci, ch in enumerate(chunks):
            for i, (lang, body) in enumerate(fenced_blocks_from_markdown(ch)):
                if body and body not in ch:
                    _fail(
                        f"{path.name} chunk {ci + 1}: fence[{i}] lang={lang!r} "
                        f"body not substring of chunk (len={len(body)})"
                    )


def test_chunker_guard_long_synthetic() -> None:
    """Many lines to force multiple chunks; must terminate without guard blow."""
    lines = [f"synthetic line {i:05d} content-xyz\n" for i in range(1200)]
    text = "## User\n\n" + "".join(lines) + "\n## Assistant\n\ndone.\n"
    chunks = chunker.chunk_markdown(text, max_chars=4000, overlap_chars=200)
    if len(chunks) < 2:
        _fail(f"synthetic long doc expected >=2 chunks, got {len(chunks)}")
    for ci, ch in enumerate(chunks):
        for i, (lang, body) in enumerate(fenced_blocks_from_markdown(ch)):
            if body and body not in ch:
                _fail(f"synthetic chunk {ci + 1}: fence[{i}] not substring")


def test_merge_invariants() -> None:
    a = ChunkExtraction(
        entities=["Rust"],
        context=None,
        decisions=["Use cargo."],
        rejected_ideas=[],
        code_snippets=[],
    )
    b = ChunkExtraction(
        entities=["Rust", "Tokio"],
        context="Use cargo.",
        decisions=["Use cargo.", "Add tokio."],
        rejected_ideas=[],
        code_snippets=[CodeSnippet(language="toml", code='tokio = "1"\n')],
    )
    m = merge.merge_chunk_extractions([a, b])
    if m.chunk_count != 2:
        _fail(f"merge chunk_count expected 2, got {m.chunk_count}")
    if m.entities != ["Rust", "Tokio"]:
        _fail(f"merge entities dedupe order: {m.entities}")
    if m.contexts != [None, "Use cargo."]:
        _fail(f"merge contexts: {m.contexts}")
    if m.decisions != ["Use cargo.", "Add tokio."]:
        _fail(f"merge decisions adjacent-dedupe: {m.decisions}")
    if len(m.code_snippets) != 1:
        _fail(f"merge code_snippets len: {len(m.code_snippets)}")


def test_merge_fixture_json_files() -> None:
    from pydantic import TypeAdapter

    adapter = TypeAdapter(ChunkExtraction)
    p1 = _REPO / "fixtures" / "chunk_extractions" / "min_a.json"
    p2 = _REPO / "fixtures" / "chunk_extractions" / "min_b.json"
    if not p1.is_file() or not p2.is_file():
        _fail("missing fixtures/chunk_extractions/min_*.json")
    chunks = [
        adapter.validate_json(p1.read_text(encoding="utf-8")),
        adapter.validate_json(p2.read_text(encoding="utf-8")),
    ]
    m = merge.merge_chunk_extractions(chunks)
    if m.chunk_count != 2 or "Tokio" not in m.entities:
        _fail("fixture JSON merge sanity failed")


def main() -> int:
    test_fixture_md_substrings()
    test_chunker_guard_long_synthetic()
    test_merge_invariants()
    test_merge_fixture_json_files()
    print("OK: selftest_fixtures passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
