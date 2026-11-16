"""Standard timestamped output folders used by every collection script."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

INVALID_FOLDER_CHARACTERS = '<>:"/\\|?*'


def sanitize_folder_component(value, fallback: str = "Unknown Generator") -> str:
    cleaned = "".join("_" if ch in INVALID_FOLDER_CHARACTERS else ch for ch in str(value))
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned or fallback


def create_unique_test_output_folder(parent_folder, test_name, generator_name) -> Path:
    """Create '<timestamp> - <test> - <generator>[ - Run N]' inside parent_folder."""
    parent = Path(parent_folder).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = f"{timestamp} - {sanitize_folder_component(test_name)} - {sanitize_folder_component(generator_name)}"
    candidate = parent / base_name
    run_number = 2
    while candidate.exists():
        candidate = parent / f"{base_name} - Run {run_number}"
        run_number += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate
