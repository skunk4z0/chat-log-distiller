#!/usr/bin/env python3
"""
Chat Log Distiller — single-chunk extraction via Gemini structured outputs.

Code fences for --prefer-verbatim-fences are taken from markdown-it-py AST (fence / code_block).

Run from repo root:
  pip install -r requirements.txt
  set GOOGLE_API_KEY=...
  python scripts/distill.py --chunk-file input/sample.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from md_nodes import fenced_blocks_from_markdown
from models import ChunkExtraction, CodeSnippet, extraction_json_schema

SYSTEM_INSTRUCTION: Final[str] = """You are a strict data extraction agent. From the user message, output JSON only.

CRITICAL rules:
1. No summarization, abstraction, or paraphrasing of facts.
2. Copy proper nouns, version strings (e.g. Flutter 3.x), numbers, error codes, and file paths exactly as substrings of the input — character-for-character.
3. Do not modify code inside code blocks; reproduce them exactly (downstream may replace code_snippets with parser output).
4. Do not infer user intent or background. If something is not stated in the chunk, use null for optional string fields or [] for lists.

Field `context` (string or null):
- Must be built ONLY from verbatim substrings of the input (concatenate short quotes with newlines if needed).
- Temperature is 0: still do not paraphrase; if you cannot cite verbatim, use null.

Output must conform to the provided JSON schema."""


USER_WRAPPER: Final[str] = """## Chat log chunk (verbatim, do not invent content outside it)

```
{chunk}
```

Extract according to the schema. For `context`, use a single string made of verbatim excerpts from the chunk only; if nothing to cite, use null."""


def build_user_content(chunk_text: str) -> str:
    return USER_WRAPPER.format(chunk=chunk_text)


def verbatim_code_snippets_from_ast(chunk: str) -> list[CodeSnippet]:
    """Deterministic code bodies from markdown-it token stream (fence + code_block)."""
    return [CodeSnippet(language=lang, code=body) for lang, body in fenced_blocks_from_markdown(chunk)]


def verify_code_snippets_are_substrings(result: ChunkExtraction, chunk: str) -> list[str]:
    """Post-check: each snippet.code must appear verbatim in the original chunk."""
    warnings: list[str] = []
    for i, sn in enumerate(result.code_snippets):
        if sn.code and sn.code not in chunk:
            warnings.append(f"code_snippets[{i}] body not found verbatim in input (len={len(sn.code)})")
    return warnings


def run_extraction(
    *,
    chunk_text: str,
    model: str,
    api_key: str | None,
) -> ChunkExtraction:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise ImportError(
            "Could not import `google.genai` (needed for Gemini). "
            "Install the SDK: pip install google-genai\n"
            "Avoid `pip uninstall google-genai`; `from google import genai` is provided by that package."
        ) from e

    client = genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.0,
        top_p=1.0,
        response_mime_type="application/json",
        response_json_schema=extraction_json_schema(),
    )
    response = client.models.generate_content(
        model=model,
        contents=build_user_content(chunk_text),
        config=config,
    )
    if not response.text:
        raise RuntimeError("Empty model response")
    return TypeAdapter(ChunkExtraction).validate_json(response.text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract structured facts from one chat chunk.")
    parser.add_argument(
        "--chunk-file",
        type=Path,
        help="Path to UTF-8 text/markdown file containing one chunk.",
    )
    parser.add_argument(
        "--chunk-stdin",
        action="store_true",
        help="Read chunk text from stdin (UTF-8).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        help="Gemini model id (default: env GEMINI_MODEL or gemini-2.0-flash).",
    )
    parser.add_argument(
        "--print-schema",
        action="store_true",
        help="Print JSON Schema and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load chunk and print length only; no API call.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip substring verification for code_snippets.",
    )
    parser.add_argument(
        "--prefer-verbatim-fences",
        action="store_true",
        help="After API call, replace code_snippets with markdown-it-py AST (fence/code_block) extraction.",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        help="Write validated JSON result to this path.",
    )
    args = parser.parse_args(argv)

    if args.print_schema:
        json.dump(extraction_json_schema(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.chunk_stdin:
        chunk_text = sys.stdin.read()
    elif args.chunk_file:
        chunk_text = args.chunk_file.read_text(encoding="utf-8")
    else:
        parser.error("Provide --chunk-file PATH or --chunk-stdin")

    if args.dry_run:
        print(f"chunk_chars={len(chunk_text)}", file=sys.stderr)
        return 0

    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("GOOGLE_API_KEY is not set", file=sys.stderr)
        return 2

    result = run_extraction(chunk_text=chunk_text, model=args.model, api_key=api_key)

    if args.prefer_verbatim_fences:
        verbatim = verbatim_code_snippets_from_ast(chunk_text)
        result = result.model_copy(update={"code_snippets": verbatim})

    if not args.no_verify:
        for w in verify_code_snippets_are_substrings(result, chunk_text):
            print(f"WARNING: {w}", file=sys.stderr)

    out_json = result.model_dump_json(indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(out_json + "\n", encoding="utf-8")
    else:
        sys.stdout.write(out_json + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
