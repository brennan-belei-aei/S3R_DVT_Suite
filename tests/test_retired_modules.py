"""Retired modules must not come back into live code (archive/ and docs/ may mention them)."""
from __future__ import annotations

import subprocess

import pytest

from tests.conftest import REPO_ROOT

RETIRED = ["AE_Commands_PMNT_HF_Anj"]
ALLOWED_PREFIXES = ("archive/", "docs/", "tests/")


@pytest.mark.parametrize("name", RETIRED)
def test_not_referenced(name):
    result = subprocess.run(
        ["git", "grep", "-l", "-F", name, "--", "*.py", "*.json"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    hits = [p for p in result.stdout.splitlines() if not p.startswith(ALLOWED_PREFIXES)]
    assert not hits, f"{name} still referenced in: {hits}"
