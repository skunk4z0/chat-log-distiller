#!/usr/bin/env python3
"""
Semantic, AST-aware Markdown chunking for long chat logs.

Uses markdown-it-py block token `map` (half-open line indices) for fence/code_block
atomic ranges — never splits inside them (may exceed max_chars for one chunk).
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterator
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

DEFAULT_MAX_CHARS = 8000
DEFAULT_OVERLAP_CHARS = 1000

_TURN_LINE = re.compile(
    r"^(#{1,6}\s*(User|Assistant)\b|\*\*Assistant:\*\*|\*\*User:\*\*|(User|Assistant):)",
    re.IGNORECASE,
)
_HEADING_LINE = re.compile(r"^#+\s+\S")


def _walk_tokens(tokens: list[Token]) -> Iterator[Token]:
    for tok in tokens:
        yield tok
        if tok.children:
            yield from _walk_tokens(tok.children)


def collect_atomic_line_ranges(source: str) -> list[tuple[int, int]]:
    """
    Half-open line ranges [a, b) from markdown-it that must not be split internally.
    Uses `fence` and `code_block` token `map` only (no string toggling on backticks).
    """
    md = MarkdownIt("commonmark")
    tokens = md.parse(source)
    ranges: list[tuple[int, int]] = []
    for tok in _walk_tokens(tokens):
        if tok.type not in ("fence", "code_block"):
            continue
        m = tok.map
        if not m or len(m) < 2:
            continue
        a, b = int(m[0]), int(m[1])
        if a < b:
            ranges.append((a, b))
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for a, b in ranges:
        if not merged or merged[-1][1] < a:
            merged.append((a, b))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
    return merged


def _partial_atomic(s: int, e: int, atomics: list[tuple[int, int]]) -> bool:
    """True if [s, e) intersects some atomic but does not fully contain that atomic."""
    for a, b in atomics:
        if b <= s or a >= e:
            continue
        if a >= s and b <= e:
            continue
        return True
    return False


def extend_for_atomics(s: int, e: int, atomics: list[tuple[int, int]], n_lines: int) -> int:
    """Smallest e' >= e such that [s, e') does not partially cut any atomic range."""
    e_out = min(e, n_lines)
    changed = True
    while changed:
        changed = False
        for a, b in atomics:
            if b <= s or a >= e_out:
                continue
            if a >= s and b <= e_out:
                continue
            e_out = min(max(e_out, b), n_lines)
            changed = True
    return e_out


def char_count(lines: list[str], s: int, e: int) -> int:
    return sum(len(lines[i]) for i in range(s, e))


def grow_chunk_end(
    lines: list[str],
    s: int,
    max_chars: int,
    atomics: list[tuple[int, int]],
    n: int,
) -> int:
    """
    Exclusive end line `e` (>= s): grow by lines until char_count(s, e) <= max_chars,
    extending past max_chars only to close a partially touched atomic block.
    """
    e = s
    if s >= n:
        return n
    while e < n:
        cand = extend_for_atomics(s, e + 1, atomics, n)
        cc = char_count(lines, s, cand)
        if cc <= max_chars:
            e = cand
            continue
        if e > s:
            return e
        return cand
    return n


def _blank_line(line: str) -> bool:
    return not line.strip()


def boundary_priority(lines: list[str], split: int, s: int) -> int | None:
    """Priority 1 (best) .. 4 for starting chunk2 at line `split`. None if invalid."""
    n = len(lines)
    if split <= s or split > n:
        return None
    line0 = lines[split].strip() if split < n else ""
    if _TURN_LINE.match(line0):
        return 1
    if _HEADING_LINE.match(line0):
        return 2
    if split > 0 and _blank_line(lines[split - 1]) and _blank_line(lines[split]):
        return 3
    return 4


def find_semantic_split(
    lines: list[str],
    s: int,
    e_grow: int,
    atomics: list[tuple[int, int]],
) -> int:
    """
    Exclusive line index `split`: emit first chunk [s, split), remainder from overlap
    next pass. Search backward from e_grow for best priority boundary (P1..P4).
    """
    for pri in (1, 2, 3, 4):
        for split in range(e_grow - 1, s, -1):
            if _partial_atomic(s, split, atomics):
                continue
            if boundary_priority(lines, split, s) == pri:
                return split
    # whole window as one chunk (valid atomic window)
    return e_grow


def adjust_overlap_start_line(ns: int, atomics: list[tuple[int, int]]) -> int:
    """If line ns falls inside [a, b), move to max(0, a - 1)."""
    for a, b in atomics:
        if a <= ns < b:
            return max(0, a - 1)
    return ns


def overlap_next_start(
    lines: list[str],
    s: int,
    split: int,
    overlap_chars: int,
    atomics: list[tuple[int, int]],
) -> int:
    """
    First line index of next chunk: ~overlap_chars taken from the *tail* of [s, split),
    snapped to the start of a line (last newline boundary inside that tail window),
    then moved before an atomic block if the snapped line falls inside a fence/code_block.
    """
    if split <= s:
        return split
    chunk1 = "".join(lines[s:split])
    if not chunk1:
        return min(s + 1, split)
    if len(chunk1) <= overlap_chars:
        ns = s
    else:
        tail = chunk1[-overlap_chars:]
        offset_in_chunk = len(chunk1) - len(tail)
        prefix = chunk1[:offset_in_chunk]
        last_nl = prefix.rfind("\n")
        snap_offset = 0 if last_nl < 0 else last_nl + 1
        extra_lines = chunk1[:snap_offset].count("\n")
        ns = s + extra_lines
    ns = adjust_overlap_start_line(ns, atomics)
    if ns >= split:
        ns = max(s, split - 1)
    if ns <= s and split > s + 1:
        ns = split - 1
    if ns <= s:
        ns = min(s + 1, split)
    return ns


def chunk_markdown(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split Markdown `text` into overlapping semantic chunks."""
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    n = len(lines)
    atomics = collect_atomic_line_ranges(text)
    chunks: list[str] = []
    s = 0
    guard = 0
    while s < n:
        guard += 1
        if guard > max(200, n * 2):
            raise RuntimeError(f"chunk_markdown: iteration guard exceeded (s={s}, n={n})")
        e_grow = grow_chunk_end(lines, s, max_chars, atomics, n)
        if e_grow >= n:
            split = n
        else:
            split = find_semantic_split(lines, s, e_grow, atomics)
        # Always consume at least one line when lines remain (avoids split==s / empty chunk loops).
        split = min(max(split, s + 1), n)
        chunks.append("".join(lines[s:split]))
        if split >= n:
            break
        ns = overlap_next_start(lines, s, split, overlap_chars, atomics)
        if ns <= s:
            ns = min(s + 1, split)
        if ns >= n:
            # Overlap start past EOF: continue from end of emitted chunk (no overlap tail).
            if split < n:
                s = split
                continue
            break
        s = ns
    return chunks


def rechunk_by_tokens(
    text: str,
    tracker_estimate_fn,
    max_tokens: int,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """
    Recursively split text until every chunk is within `max_tokens` when passed to `tracker_estimate_fn`.
    """
    if tracker_estimate_fn(text) <= max_tokens:
        return [text]

    # Binary search approach for max_chars to meet token budget
    low_chars = 100
    high_chars = len(text)
    best_chunks = []
    
    while low_chars <= high_chars:
        mid_chars = (low_chars + high_chars) // 2
        try:
            cand_chunks = chunk_markdown(text, max_chars=mid_chars, overlap_chars=overlap_chars)
            # Check if all cand_chunks fit the token budget
            all_fit = all(tracker_estimate_fn(c) <= max_tokens for c in cand_chunks)
            if all_fit:
                best_chunks = cand_chunks
                low_chars = mid_chars + 1 # try to make them as large as possible
            else:
                high_chars = mid_chars - 1
        except RuntimeError:
            high_chars = mid_chars - 1

    if not best_chunks:
        # Fallback if binary search fails to find a strictly compliant semantic split:
        # Just return extremely aggressive char split
        best_chunks = chunk_markdown(text, max_chars=500, overlap_chars=0)
        
    return best_chunks



def _preview_lines(text: str, k: int = 5) -> str:
    parts = text.splitlines()
    head = parts[:k]
    return "\n".join(f"  | {ln}" for ln in head)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AST-aware semantic Markdown chunker.")
    parser.add_argument("input", type=Path, help="Markdown file path (UTF-8).")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    args = parser.parse_args(argv)

    text = args.input.read_text(encoding="utf-8")
    chunks = chunk_markdown(text, max_chars=args.max_chars, overlap_chars=args.overlap_chars)

    print(f"file={args.input}")
    print(f"chunks={len(chunks)}")
    for i, ch in enumerate(chunks):
        print(f"--- chunk {i + 1} chars={len(ch)} ---")
        print(_preview_lines(ch, 5))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
