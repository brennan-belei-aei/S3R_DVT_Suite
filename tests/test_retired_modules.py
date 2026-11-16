"""Retired DUT command modules must not come back into live code.

Comments may mention them; imports, constructors, and module-name strings may not.
archive/, docs/, and tests/ are exempt.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.conftest import REPO_ROOT

RETIRED = ["AE_Commands_PMNT_HF_Anj", "AE_Commands_PMNT_HF_1XX", "AECommandsDLPUnits"]
ALLOWED_PREFIXES = ("archive/", "docs/", "tests/")


@pytest.mark.parametrize("name", RETIRED)
def test_not_used(name):
    pattern = rf"^\s*(import|from)\s+{name}\b|{name}\.generator\(|['\"]{name}(\.py)?['\"]"
    result = subprocess.run(
        ["git", "grep", "-n", "-E", pattern, "--", "*.py", "*.json"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    hits = [line for line in result.stdout.splitlines() if not line.startswith(ALLOWED_PREFIXES)]
    assert not hits, f"{name} still used:\n" + "\n".join(hits)


@pytest.mark.parametrize("name", RETIRED)
def test_module_file_absent(name):
    assert not list(REPO_ROOT.glob(f"AESpecificDependencies/**/{name}.py"))