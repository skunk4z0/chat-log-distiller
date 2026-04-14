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
import re
import shutil
import subprocess
import sys
from pathlib import Path


RAW_NAME_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})_ai_raw")
DEFAULT_RAW_DIR = Path(r"C:\Users\daiya\Documents\Obsidian_Vault\300_Resources\AI_Logs\01_Raw")


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


def _import_raw_files(raw_dir: Path, input_dir: Path, dry_run: bool) -> int:
    input_dir.mkdir(parents=True, exist_ok=True)
    candidates = _collect_raw_files(raw_dir)
    if not candidates:
        print("No matching raw files found.")
        return 0

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
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    input_dir = repo_root / "input"

    if not args.raw_dir.exists():
        print(f"Raw directory not found: {args.raw_dir}", file=sys.stderr)
        return 2

    _import_raw_files(args.raw_dir, input_dir, args.dry_run)
    if args.dry_run:
        return 0

    rc = _run_step([sys.executable, "scripts/main.py", "--once"], repo_root)
    if rc != 0:
        return rc

    return _run_step([sys.executable, "scripts/router.py"], repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
