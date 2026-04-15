#!/usr/bin/env python3
"""
Import raw logs from Obsidian vault, then run distill + router.

Target import pattern:
  - filename contains `YYYY-MM-DD_ai_raw`
Order:
  - latest date first
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, TextIO


RAW_NAME_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})_ai_raw")
FAILED_PREFIX_RE = re.compile(r"^(?P<stamp>\d{8}_\d{6})_(?P<rest>.+)$")
DEFAULT_RAW_DIR = Path(r"C:\Users\daiya\Documents\Obsidian_Vault\300_Resources\AI_Logs\01_Raw")
DEFAULT_COOLDOWN_FILE = ".quota_cooldown.json"
EXHAUSTION_MARKERS = (
    "aborting chunk processing due to limit exhaustion",
    "all providers are exhausted by daily quota limits",
    "all providers have exhausted their daily quotas",
    "generaterequestsperday",
    "quota exceeded for metric",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _collect_raw_files(raw_dir: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for p in raw_dir.iterdir():
        if not p.is_file():
            continue
        m = RAW_NAME_RE.search(p.name)
        if not m:
            continue
        files.append((m.group("date"), p))
    files.sort(key=lambda x: (x[0], x[1].name.lower()), reverse=True)
    return files


def _extract_date_for_sort(name: str) -> str:
    m = re.search(r"\d{4}-\d{2}-\d{2}", name)
    return m.group(0) if m else "0000-00-00"


def _collect_input_files(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []
    files = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in {".md", ".txt"}]
    files.sort(key=lambda p: (_extract_date_for_sort(p.name), p.name.lower()), reverse=True)
    return files


def _restore_failed_basename(name: str) -> str:
    """
    failed/ 退避名 `YYYYMMDD_HHMMSS_<original>` から元ファイル名を復元する。
    形式に一致しない場合はそのまま返す。
    """
    m = FAILED_PREFIX_RE.match(name)
    if not m:
        return name
    return m.group("rest")


def _collect_failed_candidates(failed_dir: Path) -> list[tuple[Path, str]]:
    if not failed_dir.exists():
        return []
    out: list[tuple[Path, str]] = []
    for p in failed_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in {".md", ".txt"}:
            continue
        restored_name = _restore_failed_basename(p.name)
        out.append((p, restored_name))
    out.sort(key=lambda x: (_extract_date_for_sort(x[1]), x[1].lower()), reverse=True)
    return out


def _resume_from_failed(
    failed_dir: Path,
    input_dir: Path,
    *,
    dry_run: bool,
    limit: int | None,
) -> list[str]:
    candidates = _collect_failed_candidates(failed_dir)
    if not candidates:
        return []
    if limit is not None:
        candidates = candidates[:limit]

    resumed_names: list[str] = []
    for src, restored_name in candidates:
        dest = input_dir / restored_name
        if dest.exists():
            print(f"SKIP failed resume (already exists in input): {dest}")
            resumed_names.append(restored_name)
            continue
        print(f"RESUME FAILED: {src} -> {dest}")
        if not dry_run:
            shutil.move(str(src), str(dest))
        resumed_names.append(restored_name)
    if resumed_names:
        print(f"Resumed from failed: {len(resumed_names)}")
    return resumed_names


def _import_raw_files(raw_dir: Path, input_dir: Path, dry_run: bool, limit: int | None) -> int:
    input_dir.mkdir(parents=True, exist_ok=True)
    candidates = _collect_raw_files(raw_dir)
    if not candidates:
        print("No matching raw files found.")
        return 0
    if limit is not None:
        candidates = candidates[:limit]

    imported = 0
    for date_str, src in candidates:
        dest = input_dir / src.name
        if dest.exists():
            print(f"SKIP (already exists): {dest}")
            continue
        print(f"IMPORT [{date_str}]: {src} -> {dest}")
        if not dry_run:
            shutil.move(str(src), str(dest))
        imported += 1
    print(f"Imported files: {imported}")
    return imported


def _run_step(cmd: list[str], cwd: Path) -> int:
    print(f"RUN: {' '.join(cmd)}")
    env = _subprocess_child_env()
    cp = subprocess.run(cmd, cwd=str(cwd), env=env)
    return int(cp.returncode)


def _subprocess_child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _drain_text_stream(stream: IO[str], *, echo: TextIO | None) -> str:
    """Read a text-mode subprocess stream line-by-line; echo each line if requested."""
    parts: list[str] = []
    readline = getattr(stream, "readline", None)
    close = getattr(stream, "close", None)
    if readline is None:
        return ""
    while True:
        line = readline()
        if line == "":
            break
        parts.append(line)
        if echo is not None and line:
            print(line, end="", file=echo)
    if close:
        close()
    return "".join(parts)


def _run_step_capture(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """
    Run a subprocess and capture combined output for quota detection.

    Avoids PIPE deadlocks on large logs by draining stdout/stderr concurrently
    (subprocess.run(capture_output=True) buffers entire streams in memory).
    """
    print(f"RUN: {' '.join(cmd)}")
    env = _subprocess_child_env()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
        env=env,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def _drain_stdout() -> None:
        if proc.stdout is not None:
            stdout_parts.append(_drain_text_stream(proc.stdout, echo=sys.stdout))

    def _drain_stderr() -> None:
        if proc.stderr is not None:
            stderr_parts.append(_drain_text_stream(proc.stderr, echo=sys.stderr))

    t_out = threading.Thread(target=_drain_stdout, name="pipe-stdout", daemon=True)
    t_err = threading.Thread(target=_drain_stderr, name="pipe-stderr", daemon=True)
    t_out.start()
    t_err.start()
    rc = int(proc.wait())
    t_out.join()
    t_err.join()
    stdout_s = stdout_parts[0] if stdout_parts else ""
    stderr_s = stderr_parts[0] if stderr_parts else ""
    combined = stdout_s + "\n" + stderr_s
    return rc, combined


def _cooldown_path(repo_root: Path, configured: str) -> Path:
    p = Path(configured)
    return p if p.is_absolute() else repo_root / p


def _read_cooldown_until(path: Path) -> datetime | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        raw = str(obj.get("retry_after_utc", "")).strip()
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _write_cooldown(path: Path, hours: float) -> datetime:
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {"retry_after_utc": until.isoformat()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return until


def _is_exhausted_output(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in EXHAUSTION_MARKERS)


def _run_main_for_targets(
    repo_root: Path,
    targets: list[Path],
    cooldown_file: Path,
    cooldown_hours: float,
) -> int:
    for target in targets:
        rel = target.relative_to(repo_root)
        rc, combined_output = _run_step_capture(
            [sys.executable, "scripts/main.py", "--once", "--only", str(rel)],
            repo_root,
        )
        if rc != 0:
            return rc
        if _is_exhausted_output(combined_output):
            until = _write_cooldown(cooldown_file, cooldown_hours)
            print(f"Quota exhausted. Cooldown set until {until.isoformat()}. Exiting normally.")
            return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import raw logs to input/, then run main.py and router.py.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
        help="Raw log source directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview import targets without moving files or running pipeline.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Import only latest N files (e.g. --limit 1).",
    )
    parser.add_argument(
        "--no-router",
        action="store_true",
        help="Run import + distill only (skip router.py).",
    )
    parser.add_argument(
        "--cooldown-hours",
        type=float,
        default=24.0,
        help="Cooldown duration after quota exhaustion before next run.",
    )
    parser.add_argument(
        "--cooldown-file",
        default=DEFAULT_COOLDOWN_FILE,
        help="Path to cooldown state file (absolute or repo-relative).",
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    input_dir = repo_root / "input"
    failed_dir = repo_root / "failed"

    if not args.raw_dir.exists():
        print(f"Raw directory not found: {args.raw_dir}", file=sys.stderr)
        return 2

    if args.limit is not None and args.limit <= 0:
        print("--limit must be >= 1", file=sys.stderr)
        return 2
    if args.cooldown_hours <= 0:
        print("--cooldown-hours must be > 0", file=sys.stderr)
        return 2

    input_dir.mkdir(parents=True, exist_ok=True)
    resumed_names = _resume_from_failed(failed_dir, input_dir, dry_run=args.dry_run, limit=args.limit)
    if resumed_names:
        print("Prioritizing resumed failed files over existing input/ and raw import.")
    else:
        pending_input = _collect_input_files(input_dir)
        if pending_input:
            print(
                f"Pending input files detected ({len(pending_input)}). "
                "Prioritizing input/ and skipping raw import."
            )
        else:
            _import_raw_files(args.raw_dir, input_dir, args.dry_run, args.limit)

    cooldown_file = _cooldown_path(repo_root, args.cooldown_file)
    now = datetime.now(timezone.utc)
    cooldown_until = _read_cooldown_until(cooldown_file)
    if cooldown_until and now < cooldown_until:
        remaining = cooldown_until - now
        print(
            f"Cooldown active until {cooldown_until.isoformat()} "
            f"(remaining ~{remaining}). "
            "Continue run to allow non-exhausted providers."
        )
    if cooldown_until and now >= cooldown_until:
        try:
            cooldown_file.unlink()
        except OSError:
            pass

    targets = _collect_input_files(input_dir)
    if args.dry_run and resumed_names:
        known = {p.name for p in targets}
        for name in resumed_names:
            if name not in known:
                targets.append(input_dir / name)
    if resumed_names:
        resumed_set = set(resumed_names)
        resumed_targets = [p for p in targets if p.name in resumed_set]
        other_targets = [p for p in targets if p.name not in resumed_set]
        targets = resumed_targets + other_targets
    if args.limit is not None:
        targets = targets[: args.limit]
    if targets:
        print(f"Processing input targets: {len(targets)}")
        for p in targets:
            print(f" - {p.name}")
    else:
        print("No input targets found.")

    if args.dry_run:
        return 0

    rc = _run_main_for_targets(repo_root, targets, cooldown_file, args.cooldown_hours)
    if rc != 0:
        return rc

    if args.no_router:
        print("Skip router step (--no-router).")
        return 0
    return _run_step([sys.executable, "scripts/router.py"], repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
