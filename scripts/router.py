#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

import yaml

FILENAME_STAMP_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?:\d{8}_\d{6}_)+(?P<rest>.+)$")


STATUS_MAP: dict[str, str] = {
    "draft": "下書き",
    "active": "進行中",
    "doing": "作業中",
    "done": "完了",
    "stable": "安定",
    "approved": "確認済み",
    "archive": "アーカイブ",
}

KEY_RENAMES: dict[str, str] = {
    "project": "プロジェクト",
    "automation_type": "自動化種別",
    "learning_level": "理解度",
    "source_origin": "情報源",
    "entry_type": "エントリ種別",
}

TAGS_MERGE_KEYS: tuple[str, ...] = ("topic", "entities", "tool_context")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_env(repo_root: Path) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(repo_root / ".env")
    except ImportError:
        pass


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x is not None and str(x).strip()]
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    s = str(value).strip()
    return [s] if s else []


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _split_front_matter(md: str) -> tuple[dict, str]:
    lines = md.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML front matter (expected leading '---')")

    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("unterminated YAML front matter (missing closing '---')")

    yaml_text = "\n".join(lines[1:end_idx]).strip("\n")
    body = "\n".join(lines[end_idx + 1 :])
    if body and not body.startswith("\n"):
        body = "\n" + body

    data = yaml.safe_load(yaml_text) if yaml_text.strip() else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("front matter must be a YAML mapping/object")
    return data, body


def _dump_front_matter(data: dict) -> str:
    header = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()
    return f"---\n{header}\n---"


def _map_status(value: object) -> object:
    if isinstance(value, str):
        return STATUS_MAP.get(value, value)
    if isinstance(value, list):
        out: list[object] = []
        for x in value:
            if isinstance(x, str):
                out.append(STATUS_MAP.get(x, x))
            else:
                out.append(x)
        return out
    return value


def rewrite_front_matter(front_matter: dict) -> dict:
    fm = dict(front_matter)

    if "status" in fm:
        fm["status"] = _map_status(fm["status"])

    for old_key, new_key in KEY_RENAMES.items():
        if old_key in fm:
            if new_key not in fm:
                fm[new_key] = fm[old_key]
            del fm[old_key]

    tags = _as_str_list(fm.get("tags"))
    for k in TAGS_MERGE_KEYS:
        tags.extend(_as_str_list(fm.get(k)))
        if k in fm:
            del fm[k]
    fm["tags"] = _dedupe_preserve_order([t for t in tags if t])

    fm["type"] = "ログ"
    fm["subtype"] = "構造化ログ"
    fm["area"] = "リソース"
    fm["review_status"] = "未読"

    return fm


def _dest_dir(vault_path: str) -> Path:
    return Path(vault_path) / "300_Resources" / "AI_Logs" / "02_Structured"


def _clean_md_filename(filename: str) -> str:
    m = FILENAME_STAMP_RE.match(filename)
    if not m:
        return filename
    return f"{m.group('date')}_{m.group('rest')}"


def _unique_dest_path(dst_dir: Path, filename: str) -> Path:
    base = Path(filename)
    candidate = dst_dir / base.name
    if not candidate.exists():
        return candidate
    stem = base.stem
    suffix = base.suffix
    for i in range(1, 10000):
        numbered = dst_dir / f"{stem}_{i}{suffix}"
        if not numbered.exists():
            return numbered
    raise RuntimeError(f"could not allocate unique filename for: {filename}")


def main() -> int:
    repo_root = _repo_root()
    _load_env(repo_root)

    vault_path = os.environ.get("OBSIDIAN_VAULT_PATH")
    if not vault_path or not vault_path.strip():
        print("OBSIDIAN_VAULT_PATH is not set (expected in .env or environment).", file=sys.stderr)
        return 2

    src_dir = repo_root / "output"
    if not src_dir.exists():
        print("output/ directory not found.", file=sys.stderr)
        return 2

    dst_dir = _dest_dir(vault_path)
    dst_dir.mkdir(parents=True, exist_ok=True)

    md_files = sorted([p for p in src_dir.glob("*.md") if p.is_file()])
    processed = 0
    skipped = 0

    for p in md_files:
        try:
            text = p.read_text(encoding="utf-8")
            fm, body = _split_front_matter(text)
            new_fm = rewrite_front_matter(fm)
            rewritten = _dump_front_matter(new_fm) + body
            p.write_text(rewritten, encoding="utf-8")

            cleaned_name = _clean_md_filename(p.name)
            dest_path = _unique_dest_path(dst_dir, cleaned_name)
            shutil.move(str(p), str(dest_path))
            processed += 1
            print(f"MOVED: output/{p.name} -> {dest_path}")
        except Exception as e:
            skipped += 1
            print(f"SKIP: {p.name}: {e}", file=sys.stderr)

    print(f"processed={processed} skipped={skipped}")
    print(f"moved to: {dst_dir}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

