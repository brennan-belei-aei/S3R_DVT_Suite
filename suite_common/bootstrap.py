"""Import for side effect: puts AESpecificDependencies on sys.path.

AE_SPECIFIC_DEPENDENCIES_DIR overrides the default repo-relative folder.
"""
import os
import sys
from pathlib import Path

from suite_common import AE_DEPENDENCIES_DIR


def dependency_folder() -> Path:
    override = os.environ.get("AE_SPECIFIC_DEPENDENCIES_DIR", "").strip()
    return Path(override).expanduser() if override else AE_DEPENDENCIES_DIR


def ensure_dependencies_on_path() -> Path:
    folder = dependency_folder().resolve()
    if not folder.is_dir():
        raise ModuleNotFoundError(
            f"AESpecificDependencies folder not found: {folder}\n"
            "Set AE_SPECIFIC_DEPENDENCIES_DIR to the folder that contains the AE command modules."
        )
    text = str(folder)
    if text not in sys.path:
        sys.path.insert(0, text)
    return folder


ensure_dependencies_on_path()
