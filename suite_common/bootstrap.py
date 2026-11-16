"""Import for side effect: puts the AE command modules (suite_instruments/adapters/ae) on sys.path.

Scripts import them flat (`import AECommandsUniversal`), and the modules import each other the
same way (`import AE_Com_Module`). Always import them by that flat name, never as
`suite_instruments.adapters.ae.X`: two names would load two copies of the module.

AE_ADAPTERS_DIR (or the legacy AE_SPECIFIC_DEPENDENCIES_DIR) overrides the folder.
"""
import os
import sys
from pathlib import Path

from suite_common import AE_ADAPTERS_DIR


def dependency_folder() -> Path:
    override = (os.environ.get("AE_ADAPTERS_DIR", "") or os.environ.get("AE_SPECIFIC_DEPENDENCIES_DIR", "")).strip()
    return Path(override).expanduser() if override else AE_ADAPTERS_DIR


def ensure_dependencies_on_path() -> Path:
    folder = dependency_folder().resolve()
    if not folder.is_dir():
        raise ModuleNotFoundError(
            f"AE command module folder not found: {folder}\n"
            "Set AE_ADAPTERS_DIR to the folder that contains AE_Com_Module.py and AECommandsUniversal.py."
        )
    text = str(folder)
    if text not in sys.path:
        sys.path.insert(0, text)
    return folder


ensure_dependencies_on_path()
