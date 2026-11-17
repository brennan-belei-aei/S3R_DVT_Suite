"""Timestamped output folders and Windows-safe file/folder name components.

Every script builds its own folder name (so existing names are unchanged) and calls
create_unique_folder() for the shared "Run N" collision rule. path_component() and
path_component_strict() are the two filename-cleaning rules used across the plugins.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

INVALID_FOLDER_CHARACTERS = '<>:"/\\|?*'


def sanitize_folder_component(value, fallback: str = "Unknown Generator") -> str:
    cleaned = "".join("_" if ch in INVALID_FOLDER_CHARACTERS else ch for ch in str(value))
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned or fallback


def path_component(value, fallback: str = "Unknown") -> str:
    """Each run of <>:"/\\|?* becomes one '_'; surrounding whitespace and trailing dots are removed."""
    return re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip().rstrip(".") or fallback


def path_component_strict(value, fallback: str) -> str:
    """Each invalid or control character becomes '_'; whitespace runs collapse; edge spaces/dots are removed.

    Empty values (None, "") give the fallback."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    return re.sub(r"\s+", " ", text).strip(" .") or fallback


def create_unique_folder(parent_folder, base_name: str, *, parents: bool = False) -> Path:
    """Create parent/base_name, or parent/'base_name - Run N' (N = 2, 3, ...) if it already exists."""
    parent = Path(parent_folder)
    candidate = parent / base_name
    run_number = 2
    while candidate.exists():
        candidate = parent / f"{base_name} - Run {run_number}"
        run_number += 1
    candidate.mkdir(parents=parents, exist_ok=False)
    return candidate


def create_unique_test_output_folder(parent_folder, test_name, generator_name) -> Path:
    """Create '<timestamp> - <test> - <generator>[ - Run N]' inside parent_folder."""
    parent = Path(parent_folder).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = f"{timestamp} - {sanitize_folder_component(test_name)} - {sanitize_folder_component(generator_name)}"
    return create_unique_folder(parent, base_name)
