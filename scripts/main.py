#!/usr/bin/env python3
"""
Orchestrator: poll `input/` for .md / .txt, chunk → distill (rate-limited) → merge → Obsidian Markdown.

Run from repo root:
  set GOOGLE_API_KEY=...
  python scripts/main.py --once
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Ensure sibling modules resolve when run as `python scripts/main.py`
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import yaml

import chunker
import distill
import merge
from models import ChunkExtraction, MergedExtraction

# Between every Gemini API call (per spec).
INTER_REQUEST_SLEEP_SEC = 5
# --fast 時もチャンク間は 0 にしない（直前成功直後の連打で 503 になりやすい）
FAST_INTER_CHUNK_SLEEP_DEFAULT = 3.0
# After a failed distill attempt, before retry (exponential).
RETRY_BACKOFF_SEC = (10, 20, 40)
# 503 / 429 など一時的な過負荷向け（--fast でもリトライ前は必ず待つ）
TRANSIENT_BACKOFF_SEC = (30, 60, 90)
MAX_DISTILL_ATTEMPTS = 6  # initial + 5 retries (503 が続きやすいため多め)


def _dedupe_preserve_order_str(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def _discover_inputs(input_dir: Path) -> list[Path]:
    out: list[Path] = []
    for pat in ("*.md", "*.txt"):
        out.extend(input_dir.glob(pat))
    return sorted(p for p in out if p.is_file())


def _archive_name(original: Path) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{original.name}"


def _failed_name(original: Path) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{original.name}"


def _output_name(process_date: str, original: Path) -> str:
    return f"{process_date}_{original.stem}.md"


def _sanitize_tag(s: str, max_len: int = 48) -> str:
    t = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in s.strip().lower())
    t = "-".join(x for x in t.split("-") if x)
    return (t[:max_len] or "entity")[:max_len]


def _resolve_only_file(repo_root: Path, only: str) -> Path | None:
    """Resolve `--only` to an existing file (repo-relative, under input/, or absolute)."""
    raw = Path(only)
    if raw.is_absolute():
        candidates = [raw]
    else:
        candidates = [repo_root / raw, repo_root / "input" / raw]
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r.is_file():
            return r
    return None


def _is_transient_api_error(exc: BaseException) -> bool:
    """503 / 429 など、待てば通る可能性がある API 側の失敗。"""
    try:
        from google.genai.errors import ClientError, ServerError

        if isinstance(exc, ServerError):
            code = getattr(exc, "status_code", None)
            if code == 503:
                return True
        if isinstance(exc, ClientError):
            code = getattr(exc, "status_code", None)
            if code == 429:
                return True
    except ImportError:
        pass
    s = str(exc).lower()
    if (
        "503" in s
        or "unavailable" in s
        or "429" in s
        or "resource_exhausted" in s
        or "rate limit" in s
        or "quota" in s
        or "too many requests" in s
    ):
        return True
    return False


def _is_daily_quota_exceeded(exc: BaseException) -> bool:
    s = str(exc).lower()
    return (
        "requestsperday" in s
        or "perday" in s
        or "rpd" in s
        or "quota exceeded for metric" in s and "requests" in s
        or "quotaid': 'generaterequestsperday" in s
    )


def _sleep_before_distill_retry(
    attempt: int,
    exc: BaseException,
    rate_limit: bool,
    logger: logging.Logger,
) -> None:
    """失敗後の待機。--fast でもここは省略しない（503 連打を防ぐ）。"""
    if _is_transient_api_error(exc):
        waits = TRANSIENT_BACKOFF_SEC
        kind = "transient"
    else:
        waits = RETRY_BACKOFF_SEC
        kind = "normal"
    idx = min(attempt, len(waits) - 1)
    primary = waits[idx]
    extra = INTER_REQUEST_SLEEP_SEC if rate_limit else 0
    logger.info(
        "retry backoff (%s): sleeping %ss before attempt %s/%s (+%ss rate-limit padding=%s)",
        kind,
        primary,
        attempt + 2,
        MAX_DISTILL_ATTEMPTS,
        extra,
        rate_limit,
    )
    time.sleep(primary)
    if rate_limit:
        time.sleep(INTER_REQUEST_SLEEP_SEC)


def _distill_one_chunk(
    *,
    chunk_text: str,
    chunk_index: int,
    model: str,
    provider: str,
    fallback_providers: list[str],
    model_by_provider: dict[str, str],
    exhausted_providers: set[str],
    provider_attempt_counts: dict[str, int],
    provider_success_counts: dict[str, int],
    api_key: str | None,
    logger: logging.Logger,
    prefer_verbatim_fences: bool,
    no_verify: bool,
    rate_limit: bool,
    max_output_tokens: int | None,
) -> ChunkExtraction:
    last_exc: BaseException | None = None
    provider_order = _dedupe_preserve_order_str([provider] + fallback_providers)
    for attempt in range(MAX_DISTILL_ATTEMPTS):
        available = [p for p in provider_order if p not in exhausted_providers]
        if not available:
            raise RuntimeError("All providers are exhausted by daily quota limits")
        provider_idx = min(attempt, len(available) - 1)
        active_provider = available[provider_idx]
        active_model = model_by_provider.get(active_provider, model)
        try:
            provider_attempt_counts[active_provider] = provider_attempt_counts.get(active_provider, 0) + 1
            result = distill.run_extraction(
                provider=active_provider,
                chunk_text=chunk_text,
                model=active_model,
                api_key=api_key,
                max_output_tokens=max_output_tokens,
            )
            if prefer_verbatim_fences:
                verbatim = distill.verbatim_code_snippets_from_ast(chunk_text)
                result = result.model_copy(update={"code_snippets": verbatim})
            if not no_verify:
                for w in distill.verify_code_snippets_are_substrings(result, chunk_text):
                    logger.warning("chunk %s verify: %s", chunk_index + 1, w)
            provider_success_counts[active_provider] = provider_success_counts.get(active_provider, 0) + 1
            return result
        except BaseException as e:
            last_exc = e
            if _is_daily_quota_exceeded(e):
                exhausted_providers.add(active_provider)
                logger.warning(
                    "provider exhausted by daily quota; skip for remaining chunks: provider=%s",
                    active_provider,
                )
            logger.warning(
                "chunk %s distill attempt %s/%s failed provider=%s model=%s: %s",
                chunk_index + 1,
                attempt + 1,
                MAX_DISTILL_ATTEMPTS,
                active_provider,
                active_model,
                e,
            )
            if attempt < MAX_DISTILL_ATTEMPTS - 1:
                _sleep_before_distill_retry(attempt, last_exc, rate_limit, logger)
    assert last_exc is not None
    raise last_exc


def _build_topic(merged: MergedExtraction, source_stem: str) -> str:
    if merged.entities:
        return merged.entities[0][:200]
    for c in merged.contexts:
        if c:
            one = c.strip().split("\n", 1)[0]
            return one[:200]
    return source_stem[:200]


def _build_body(merged: MergedExtraction) -> str:
    parts: list[str] = []

    parts.append("## Context\n")
    for i, ctx in enumerate(merged.contexts, start=1):
        parts.append(f"### Chunk {i}\n")
        if ctx:
            parts.append(ctx.strip() + "\n\n")
        else:
            parts.append("_（なし）_\n\n")

    parts.append("## Entities\n")
    if merged.entities:
        for e in merged.entities:
            parts.append(f"- {e}\n")
    else:
        parts.append("_（なし）_\n")
    parts.append("\n")

    parts.append("## Decisions\n")
    if merged.decisions:
        for d in merged.decisions:
            parts.append(f"- {d}\n")
    else:
        parts.append("_（なし）_\n")
    parts.append("\n")

    parts.append("## Rejected ideas\n")
    if merged.rejected_ideas:
        for ri in merged.rejected_ideas:
            parts.append(f"- **Idea:** {ri.idea}\n")
            parts.append(f"  - **Reason:** {ri.reason}\n")
    else:
        parts.append("_（なし）_\n")
    parts.append("\n")

    parts.append("## Code snippets\n")
    if merged.code_snippets:
        for sn in merged.code_snippets:
            lang = sn.language or ""
            parts.append(f"```{lang}\n{sn.code.rstrip()}\n```\n\n")
    else:
        parts.append("_（なし）_\n")

    return "".join(parts)


def _build_obsidian_note(
    merged: MergedExtraction,
    *,
    source_rel: str,
    model: str,
) -> str:
    base_tags = ["chat-distilled", "distilled-log"]
    entity_tags = [_sanitize_tag(e) for e in merged.entities[:30]]
    tags = _dedupe_preserve_order(base_tags + entity_tags)

    fm: dict = {
        "tags": tags,
        "topic": _build_topic(merged, Path(source_rel).stem),
        "status": "active",
        "type": "メモ",
        "project": merged.project,
        "tool_context": merged.tool_context,
        "automation_type": merged.automation_type,
        "learning_level": merged.learning_level,
        "source_origin": merged.source_origin,
        "entry_type": merged.entry_type,
        "source_log": source_rel,
        "distilled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chunk_count": merged.chunk_count,
        "model": model,
    }
    header = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip()
    body = _build_body(merged)
    return f"---\n{header}\n---\n\n{body}"


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _unique_output_path(output_dir: Path, name: str) -> Path:
    p = output_dir / name
    if not p.exists():
        return p
    stem = p.stem
    for i in range(2, 1000):
        cand = output_dir / f"{stem}_{i}.md"
        if not cand.exists():
            return cand
    raise RuntimeError("could not allocate output filename")


def process_one_file(
    path: Path,
    *,
    repo_root: Path,
    model: str,
    provider: str,
    fallback_providers: list[str],
    model_by_provider: dict[str, str],
    api_key: str | None,
    logger: logging.Logger,
    max_chars: int,
    overlap_chars: int,
    dry_run: bool,
    prefer_verbatim_fences: bool,
    no_verify: bool,
    rate_limit: bool,
    no_archive: bool,
    fast_inter_chunk_sec: float,
    max_output_tokens: int | None,
) -> bool:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        rel = path
    logger.info("start file=%s", rel)
    if not rate_limit and not dry_run:
        logger.warning(
            "rate_limit=off (--fast): チャンク間は %ss（--fast-inter-chunk-sleep で変更）。"
            "503/429 時のリトライ待機は省略しません。本番では --fast を使わないでください。",
            fast_inter_chunk_sec,
        )

    if dry_run:
        text = path.read_text(encoding="utf-8")
        chunks = chunker.chunk_markdown(text, max_chars=max_chars, overlap_chars=overlap_chars)
        logger.info("dry-run: would chunk into n=%s (chars total=%s)", len(chunks), len(text))
        return True

    text = path.read_text(encoding="utf-8")
    chunks = chunker.chunk_markdown(text, max_chars=max_chars, overlap_chars=overlap_chars)
    if not chunks:
        logger.warning("skip empty after chunk: %s", rel)
        return False

    extractions: list[ChunkExtraction] = []
    exhausted_providers: set[str] = set()
    provider_attempt_counts: dict[str, int] = {}
    provider_success_counts: dict[str, int] = {}
    try:
        for i, ch in enumerate(chunks):
            logger.info("distill chunk %s/%s chars=%s", i + 1, len(chunks), len(ch))
            ex = _distill_one_chunk(
                chunk_text=ch,
                chunk_index=i,
                model=model,
                provider=provider,
                fallback_providers=fallback_providers,
                model_by_provider=model_by_provider,
                exhausted_providers=exhausted_providers,
                provider_attempt_counts=provider_attempt_counts,
                provider_success_counts=provider_success_counts,
                api_key=api_key,
                logger=logger,
                prefer_verbatim_fences=prefer_verbatim_fences,
                no_verify=no_verify,
                rate_limit=rate_limit,
                max_output_tokens=max_output_tokens,
            )
            extractions.append(ex)
            # Spacing between successful chunk requests (last chunk: nothing follows).
            if i < len(chunks) - 1:
                if rate_limit:
                    time.sleep(INTER_REQUEST_SLEEP_SEC)
                elif fast_inter_chunk_sec > 0:
                    logger.info(
                        "fast mode: sleeping %ss between chunks (%s/%s)",
                        fast_inter_chunk_sec,
                        i + 1,
                        len(chunks),
                    )
                    time.sleep(fast_inter_chunk_sec)

        merged: MergedExtraction = merge.merge_chunk_extractions(extractions)
        process_date = datetime.now().strftime("%Y-%m-%d")
        out_name = _output_name(process_date, path)
        body = _build_obsidian_note(merged, source_rel=str(rel), model=model)

        output_dir = repo_root / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = _unique_output_path(output_dir, out_name)
        out_path.write_text(body, encoding="utf-8")
        logger.info("wrote %s", out_path.relative_to(repo_root))

        if no_archive:
            logger.info("skip archive (--no-archive)")
        else:
            archive_dir = repo_root / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            if path.exists():
                dest = archive_dir / _archive_name(path)
                shutil.move(str(path), str(dest))
                logger.info("archived -> %s", dest.relative_to(repo_root))
            else:
                logger.warning("archive skipped: source file already missing: %s", rel)
        logger.info(
            "provider usage summary attempts=%s successes=%s exhausted=%s",
            provider_attempt_counts,
            provider_success_counts,
            sorted(exhausted_providers),
        )
        return True
    except BaseException:
        logger.error("failed file=%s\n%s", rel, traceback.format_exc())
        try:
            failed_dir = repo_root / "failed"
            failed_dir.mkdir(parents=True, exist_ok=True)
            if path.exists():
                failed_dest = failed_dir / _failed_name(path)
                shutil.move(str(path), str(failed_dest))
                logger.info("failed -> %s", failed_dest.relative_to(repo_root))
            else:
                logger.warning("failed routing skipped: source file already missing: %s", rel)
        except BaseException:
            logger.error("failed routing error file=%s\n%s", rel, traceback.format_exc())
        return False


def main(argv: list[str] | None = None) -> int:
    repo_root = _repo_root()
    parser = argparse.ArgumentParser(description="Poll input/, distill chat logs to Obsidian Markdown.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan cycle then exit (otherwise polls every --interval seconds).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Seconds between scans when looping (ignored if --once).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id override for primary provider.",
    )
    parser.add_argument(
        "--provider",
        choices=sorted(distill.SUPPORTED_PROVIDERS),
        default=os.environ.get("DISTILL_PROVIDER", "gemini"),
        help="Primary LLM provider (default: env DISTILL_PROVIDER or gemini).",
    )
    parser.add_argument(
        "--fallback-providers",
        default=os.environ.get("DISTILL_FALLBACK_PROVIDERS", ""),
        help="Comma-separated fallback providers in retry order (e.g. openrouter,groq,mistral).",
    )
    parser.add_argument("--max-chars", type=int, default=chunker.DEFAULT_MAX_CHARS)
    parser.add_argument("--overlap-chars", type=int, default=chunker.DEFAULT_OVERLAP_CHARS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No API calls; log chunk counts only.",
    )
    parser.add_argument(
        "--prefer-verbatim-fences",
        action="store_true",
        default=True,
        help="Replace code_snippets with markdown-it fence extraction (default: on).",
    )
    parser.add_argument(
        "--no-prefer-verbatim-fences",
        action="store_false",
        dest="prefer_verbatim_fences",
        help="Keep model-produced code_snippets.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip code_snippets substring verification.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="テスト用: 成功チャンク間は --fast-inter-chunk-sleep（既定3s）。失敗リトライの待機は省略しない。",
    )
    parser.add_argument(
        "--fast-inter-chunk-sleep",
        type=float,
        default=FAST_INTER_CHUNK_SLEEP_DEFAULT,
        metavar="SEC",
        help="--fast 時、成功したチャンク間の待機秒（0=無効・503 が出やすい）。",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="テスト用: 成功後も input から archive へ移動しない。",
    )
    parser.add_argument(
        "--only",
        metavar="PATH",
        help="この1ファイルだけ処理（例: input/sample.md または sample.md）。",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Optional per-call output token cap passed to distill.py.",
    )
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(repo_root / ".env")
    except ImportError:
        pass

    log_path = repo_root / "logs" / "pipeline.log"
    logger = _setup_logging(log_path)
    primary_model = args.model or distill.default_model_for_provider(args.provider)
    fallback_providers = [p.strip() for p in args.fallback_providers.split(",") if p.strip()]
    fallback_providers = [p for p in fallback_providers if p in distill.SUPPORTED_PROVIDERS and p != args.provider]
    provider_order = _dedupe_preserve_order_str([args.provider] + fallback_providers)
    model_by_provider = {p: distill.default_model_for_provider(p) for p in provider_order}
    model_by_provider[args.provider] = primary_model
    api_key: str | None = None

    if not args.dry_run:
        missing: list[str] = []
        env_by_provider = {
            "gemini": "GOOGLE_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "mistral": "MISTRAL_API_KEY",
        }
        for p in provider_order:
            if not os.environ.get(env_by_provider[p]):
                missing.append(env_by_provider[p])
        if missing:
            logger.error("Missing API keys for configured providers: %s", ", ".join(sorted(set(missing))))
            return 2

    input_dir = repo_root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    def cycle() -> None:
        if args.only:
            one = _resolve_only_file(repo_root, args.only)
            if not one:
                logger.error("--only: file not found: %s", args.only)
                return
            files = [one]
        else:
            files = _discover_inputs(input_dir)
        if not files:
            logger.info("no input files")
            return
        rate_limit = not args.fast
        fast_gap = args.fast_inter_chunk_sleep if args.fast else 0.0
        for f in files:
            try:
                process_one_file(
                    f,
                    repo_root=repo_root,
                    model=primary_model,
                    provider=args.provider,
                    fallback_providers=fallback_providers,
                    model_by_provider=model_by_provider,
                    api_key=api_key,
                    logger=logger,
                    max_chars=args.max_chars,
                    overlap_chars=args.overlap_chars,
                    dry_run=args.dry_run,
                    prefer_verbatim_fences=args.prefer_verbatim_fences,
                    no_verify=args.no_verify,
                    rate_limit=rate_limit,
                    no_archive=args.no_archive,
                    fast_inter_chunk_sec=fast_gap,
                    max_output_tokens=args.max_output_tokens,
                )
            except BaseException:
                logger.error("unexpected outer failure on %s\n%s", f, traceback.format_exc())

    if args.once or args.interval <= 0:
        cycle()
        return 0

    logger.info(
        "poll loop interval=%ss provider=%s fallbacks=%s model=%s",
        args.interval,
        args.provider,
        ",".join(fallback_providers) or "-",
        primary_model,
    )
    while True:
        cycle()
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
