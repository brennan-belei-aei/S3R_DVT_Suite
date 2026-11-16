"""Every tracked Python file outside the archives must at least compile."""
from __future__ import annotations

import subprocess

import pytest

from tests.conftest import REPO_ROOT

ARCHIVE_PARTS = {"_archive", "archive"}


def _tracked_python_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return sorted(p for p in out.split("\0") if p and not ARCHIVE_PARTS.intersection(p.split("/")))


@pytest.mark.parametrize("relpath", _tracked_python_files())
def test_compiles(relpath):
    source = (REPO_ROOT / relpath).read_bytes()
    compile(source, relpath, "exec", dont_inherit=True)
