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
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


RAW_NAME_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})_ai_raw")
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
    cp = subprocess.run(cmd, cwd=str(cwd))
    return int(cp.returncode)


def _run_step_capture(cmd: list[str], cwd: Path) -> tuple[int, str]:
    print(f"RUN: {' '.join(cmd)}")
    cp = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
    )
    if cp.stdout:
        print(cp.stdout, end="")
    if cp.stderr:
        print(cp.stderr, end="", file=sys.stderr)
    combined = (cp.stdout or "") + "\n" + (cp.stderr or "")
    return int(cp.returncode), combined


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

    if not args.raw_dir.exists():
        print(f"Raw directory not found: {args.raw_dir}", file=sys.stderr)
        return 2

    if args.limit is not None and args.limit <= 0:
        print("--limit must be >= 1", file=sys.stderr)
        return 2
    if args.cooldown_hours <= 0:
        print("--cooldown-hours must be > 0", file=sys.stderr)
        return 2

    _import_raw_files(args.raw_dir, input_dir, args.dry_run, args.limit)
    if args.dry_run:
        return 0

    cooldown_file = _cooldown_path(repo_root, args.cooldown_file)
    now = datetime.now(timezone.utc)
    cooldown_until = _read_cooldown_until(cooldown_file)
    if cooldown_until and now < cooldown_until:
        remaining = cooldown_until - now
        print(
            f"Cooldown active until {cooldown_until.isoformat()} "
            f"(remaining ~{remaining}). Skip this run."
        )
        return 0
    if cooldown_until and now >= cooldown_until:
        try:
            cooldown_file.unlink()
        except OSError:
            pass

    rc, combined_output = _run_step_capture([sys.executable, "scripts/main.py", "--once"], repo_root)
    if rc != 0:
        return rc
    if _is_exhausted_output(combined_output):
        until = _write_cooldown(cooldown_file, args.cooldown_hours)
        print(f"Quota exhausted. Cooldown set until {until.isoformat()}. Exiting normally.")
        return 0

    if args.no_router:
        print("Skip router step (--no-router).")
        return 0
    return _run_step([sys.executable, "scripts/router.py"], repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
