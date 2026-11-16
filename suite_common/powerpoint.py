"""PowerPoint export helpers shared by the Rise and Fall analysis scripts."""

import os
from pathlib import Path


def powerpoint_export_requested():
    return os.environ.get("PARAMOUNT_POWERPOINT_EXPORT_ENABLED", "").strip() == "1"


def snapshot_png_files(folder):
    folder=Path(folder)
    if not folder.is_dir(): return {}
    return {str(x.resolve()):(x.stat().st_mtime_ns,x.stat().st_size) for x in folder.rglob("*.png") if x.is_file()}


def changed_png_files(before,folder):
    return [Path(x) for x,state in snapshot_png_files(folder).items() if before.get(x)!=state]
