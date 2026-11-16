"""Script discovery, portable settings paths, and child-process helpers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from suite_common import REPO_ROOT

# Folders never searched when discovering suite scripts.
EXCLUDED_DISCOVERY_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", "_archive", "archive"}


def discover_script(candidates: list[str], root: Path = REPO_ROOT) -> str:
    """Return the first candidate filename found anywhere under root."""
    for filename in candidates:
        direct = root / filename
        if direct.is_file():
            return str(direct)
        for match in sorted(root.rglob(filename)):
            if match.is_file() and not EXCLUDED_DISCOVERY_PARTS.intersection(match.relative_to(root).parts):
                return str(match)
    return str(root / candidates[0])


def to_portable_path(path: str | Path) -> str:
    """Store paths inside the repo as repo-relative so settings move between PCs."""
    text = str(path).strip()
    if not text:
        return ""
    try:
        return Path(text).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return text


def resolve_portable_path(text: str | Path) -> str:
    value = str(text).strip()
    if not value:
        return ""
    path = Path(value)
    return str(path if path.is_absolute() else (REPO_ROOT / path).resolve())


def resolve_script_setting(saved: str, candidates: list[str]) -> str:
    """Use a saved script path when it still exists, otherwise rediscover it."""
    resolved = resolve_portable_path(saved)
    if resolved and Path(resolved).is_file():
        return resolved
    return discover_script(candidates)


def resolve_saved_script(saved: str, default: str | Path) -> str:
    """Use a saved script path when it still exists, otherwise the plugin registry's default."""
    resolved = resolve_portable_path(saved)
    if resolved and Path(resolved).is_file():
        return resolved
    return str(default)


def load_json_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as error:
        print(f"Warning: ignoring unreadable settings file {path.name}: {error}")
        return {}


def save_json_settings(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def child_creation_flags(new_console: bool) -> int:
    if sys.platform.startswith("win") and new_console:
        return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return 0


def open_path(path: str | Path) -> None:
    target = str(path)
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
