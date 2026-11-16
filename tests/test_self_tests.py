"""The built-in --self-test of each top-level app must keep passing."""
from __future__ import annotations

import subprocess
import sys

import pytest

from tests.conftest import REPO_ROOT

APPS = [
    "Transparency_Test_Suite_Launcher.py",
    "Paramount_Data_Collection_Unified_GUI.py",
    "Paramount_Data_Analysis_Unified_GUI.py",
]


@pytest.mark.parametrize("script", APPS)
def test_self_test(script):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / script), "--self-test"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "self-test passed" in result.stdout
