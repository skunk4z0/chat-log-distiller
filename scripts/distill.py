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

SUPPORTED_PROVIDERS: Final[set[str]] = {"gemini", "openrouter", "groq", "mistral"}

SYSTEM_INSTRUCTION: Final[str] = """You are a strict data extraction agent. From the user message, output JSON only.

CRITICAL rules:
1. No summarization, abstraction, or paraphrasing of facts.
2. Copy proper nouns, version strings (e.g. Flutter 3.x), numbers, error codes, and file paths exactly as substrings of the input — character-for-character.
3. Do not modify code inside code blocks; reproduce them exactly (downstream may replace code_snippets with parser output).
4. Do not infer user intent or background. If something is not stated in the chunk, use null for optional string fields or [] for lists.

Field `context` (string or null):
- Must be built ONLY from verbatim substrings of the input (concatenate short quotes with newlines if needed).
- Temperature is 0: still do not paraphrase; if you cannot cite verbatim, use null.

Structured field policy (must match provided JSON schema):
- Existing required fields: entities, context, decisions, rejected_ideas, code_snippets.
- Additional fields:
  - project: string|null. Candidate values include preflop-trainer-android, chat-log-distiller, talent-rag-bot, recipe-web, ec-sales-automation, vault-admin.
  - tool_context: string[]. Candidate values include Syncthing, Metadata_Menu, Clibor, Web_Clipper, Dataview, Bases, Python, GAS.
  - automation_type: string|null. Candidate values include auto_routing, data_sync, sales_tracking.
  - learning_level: string|null. Allowed mapped values: vibe, understood, mastered.
  - source_origin: string|null. Allowed mapped values: official_doc, github_issue, ai_hallucination, manual_test.
  - entry_type: string|null. Allowed values: troubleshooting, idea, research.

Signal mapping rules (Japanese cues -> normalized values):
- learning_level:
  - vibe <- [なんとなく], [なぜか動いた], [雰囲気]
  - understood <- [納得], [仕組み理解], [理屈わかった]
  - mastered <- [完璧], [完全に理解した], [人に教えれる]
- source_origin:
  - official_doc <- [公式], [リファレンス], [ドキュメント]
  - github_issue <- [ギットハブ], [issue], [解決策発見]
  - ai_hallucination <- [AIの嘘], [ハルシ], [嘘つかれた]
  - manual_test <- [手作業], [泥臭い検証], [実機確認]
- entry_type guidance:
  - troubleshooting should be used only when troubleshooting wording is explicit.
  - idea should be used only when idea/proposal wording is explicit.
  - research should be used only when investigation/research wording is explicit.

Strict anti-inference rule for mapped fields:
- For learning_level, source_origin, and entry_type, DO NOT guess from tone.
- If no explicit matching signal exists in the chunk, return null.
- For project, automation_type, and tool_context, extract only when explicitly stated; otherwise null or [].

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


def _provider_api_key(provider: str, explicit_api_key: str | None) -> str | None:
    if explicit_api_key:
        return explicit_api_key
    key_by_provider = {
        "gemini": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
        "mistral": "MISTRAL_API_KEY",
    }
    env_name = key_by_provider.get(provider)
    if not env_name:
        return None
    return os.environ.get(env_name)


def default_model_for_provider(provider: str) -> str:
    if provider == "gemini":
        return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    if provider == "openrouter":
        return os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")
    if provider == "groq":
        return os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    if provider == "mistral":
        return os.environ.get("MISTRAL_MODEL", "mistral-large-latest")
    raise ValueError(f"unsupported provider: {provider}")


def _run_extraction_openai_compatible(
    *,
    provider: str,
    base_url: str,
    api_key: str | None,
    model: str,
    chunk_text: str,
    max_output_tokens: int | None,
) -> ChunkExtraction:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError(
            "Could not import `openai` package for non-Gemini providers. "
            "Install dependencies: pip install -r requirements.txt"
        ) from e

    key = _provider_api_key(provider, api_key)
    if not key:
        raise RuntimeError(f"{provider} API key is not set")

    client = OpenAI(api_key=key, base_url=base_url)
    kwargs: dict[str, object] = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": build_user_content(chunk_text)},
        ],
    }
    if max_output_tokens is not None:
        kwargs["max_tokens"] = max_output_tokens

    response = client.chat.completions.create(**kwargs)
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError(f"Empty model response ({provider})")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return TypeAdapter(ChunkExtraction).validate_json(text)

    # Some providers may return relaxed shapes even with JSON mode.
    if isinstance(obj, dict):
        entities = obj.get("entities")
        if isinstance(entities, dict):
            # Flatten dict-form entities into string list.
            flat: list[str] = []
            for _, val in entities.items():
                if isinstance(val, list):
                    flat.extend(str(x) for x in val)
                elif val is not None:
                    flat.append(str(val))
            obj["entities"] = flat
        elif isinstance(entities, list):
            flat = []
            for e in entities:
                if isinstance(e, str):
                    flat.append(e)
                elif isinstance(e, dict):
                    if "name" in e:
                        flat.append(str(e.get("name", "")))
                    elif "value" in e:
                        flat.append(str(e.get("value", "")))
            obj["entities"] = [x for x in flat if x]

        decisions = obj.get("decisions")
        if decisions is None:
            obj["decisions"] = []
        elif isinstance(decisions, list):
            out_decisions: list[str] = []
            for d in decisions:
                if isinstance(d, str):
                    out_decisions.append(d)
                elif isinstance(d, dict):
                    if "decision" in d:
                        out_decisions.append(str(d.get("decision", "")))
                    elif "description" in d:
                        out_decisions.append(str(d.get("description", "")))
            obj["decisions"] = [x for x in out_decisions if x]
        else:
            obj["decisions"] = []

        snippets = obj.get("code_snippets")
        if snippets is None:
            obj["code_snippets"] = []
        elif isinstance(snippets, list):
            fixed_snippets: list[dict[str, str]] = []
            for sn in snippets:
                if isinstance(sn, str):
                    fixed_snippets.append({"language": "", "code": sn})
                elif isinstance(sn, dict):
                    lang = sn.get("language", "")
                    code = sn.get("code", "")
                    fixed_snippets.append({"language": str(lang), "code": str(code)})
            obj["code_snippets"] = fixed_snippets
        else:
            obj["code_snippets"] = []

        rej = obj.get("rejected_ideas")
        if rej is None:
            obj["rejected_ideas"] = []
        elif isinstance(rej, list):
            fixed_rej: list[dict[str, str]] = []
            for r in rej:
                if isinstance(r, str):
                    fixed_rej.append({"idea": r, "reason": ""})
                elif isinstance(r, dict):
                    fixed_rej.append(
                        {
                            "idea": str(r.get("idea", "")),
                            "reason": str(r.get("reason", "")),
                        }
                    )
            obj["rejected_ideas"] = fixed_rej
        else:
            obj["rejected_ideas"] = []

    return TypeAdapter(ChunkExtraction).validate_python(obj)


def run_extraction(
    *,
    provider: str,
    chunk_text: str,
    model: str,
    api_key: str | None,
    max_output_tokens: int | None = None,
) -> ChunkExtraction:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")

    if provider in {"openrouter", "groq", "mistral"}:
        base_url = {
            "openrouter": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "groq": os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            "mistral": os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
        }[provider]
        return _run_extraction_openai_compatible(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            chunk_text=chunk_text,
            max_output_tokens=max_output_tokens,
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise ImportError(
            "Could not import `google.genai` (needed for Gemini). "
            "Install the SDK: pip install google-genai\n"
            "Avoid `pip uninstall google-genai`; `from google import genai` is provided by that package."
        ) from e

    key = _provider_api_key("gemini", api_key)
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    client = genai.Client(api_key=key)
    config_kwargs: dict[str, object] = {
        "system_instruction": SYSTEM_INSTRUCTION,
        "temperature": 0.0,
        "top_p": 1.0,
        "response_mime_type": "application/json",
        "response_json_schema": extraction_json_schema(),
    }
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens
    config = types.GenerateContentConfig(
        **config_kwargs,
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
        "--provider",
        choices=sorted(SUPPORTED_PROVIDERS),
        default=os.environ.get("DISTILL_PROVIDER", "gemini"),
        help="LLM provider to use (default: env DISTILL_PROVIDER or gemini).",
    )
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
        default=None,
        help="Model id override. If omitted, provider-specific env default is used.",
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
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Optional generation cap for output tokens (helps avoid truncated JSON).",
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

    model = args.model or default_model_for_provider(args.provider)
    api_key = _provider_api_key(args.provider, None)
    if not api_key:
        missing_env = {
            "gemini": "GOOGLE_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "mistral": "MISTRAL_API_KEY",
        }[args.provider]
        print(f"{missing_env} is not set", file=sys.stderr)
        return 2

    result = run_extraction(
        provider=args.provider,
        chunk_text=chunk_text,
        model=model,
        api_key=api_key,
        max_output_tokens=args.max_output_tokens,
    )

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
