"""Shared name-cleaning and run-folder helpers replace per-script copies (Phase 7); names must not change."""
from __future__ import annotations

import random
import re

import pytest

from suite_common.output_folders import create_unique_folder, path_component, path_component_strict

ALPHABET = list('abcXYZ019 ._-<>:"/\\|?*\t\n\x00\x01\x1f\x7féµ')


# Verbatim copies of the per-script implementations these helpers replaced.
def original_simple(value, fallback):  # AEBus, Arc, Psync, RF Ramp, RF Tuning, analysis GUI
    return re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip().rstrip(".") or fallback


def original_strict(value, fallback):  # analog-port-timing collectors and analyses
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    return re.sub(r"\s+", " ", text).strip(" .") or fallback


def original_strict_setpoint_collector(value, fallback):
    text = str(value or "").strip()
    text = re.sub(r'[<>:\"/\\|?*\x00-\x1F]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def original_strict_str(value, fallback):  # Digital Timing capture, setpoint analysis (callers pass non-empty text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value).strip())
    return re.sub(r"\s+", " ", text).strip(" .") or fallback


def _samples(count=3000):
    rng = random.Random(7)
    texts = ["".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 14))) for _ in range(count)]
    return texts + ["", " ", "...", "a.", " a b ", "PMNT 0451C5", "10.9.13.212", "COM5", "A:B/C"]


def test_path_component_matches_the_original_copies():
    for text in _samples():
        assert path_component(text, "Fallback") == original_simple(text, "Fallback"), repr(text)
    for value in (0, 12.5, None):  # copies that called str() first
        assert path_component(value, "F") == original_simple(value, "F")


def test_path_component_strict_matches_the_original_copies():
    for text in _samples():
        expected = original_strict(text, "Fallback")
        assert path_component_strict(text, "Fallback") == expected, repr(text)
        assert original_strict_setpoint_collector(text, "Fallback") == expected
        if text:
            assert original_strict_str(text, "Fallback") == expected
    for value in (None, 0, ""):
        assert path_component_strict(value, "F") == original_strict(value, "F") == "F"


def test_create_unique_folder_adds_run_numbers(tmp_path):
    first = create_unique_folder(tmp_path, "2026-01-01_00-00-00 - Test - Gen")
    second = create_unique_folder(tmp_path, "2026-01-01_00-00-00 - Test - Gen")
    third = create_unique_folder(tmp_path, "2026-01-01_00-00-00 - Test - Gen")
    assert [p.name for p in (first, second, third)] == [
        "2026-01-01_00-00-00 - Test - Gen",
        "2026-01-01_00-00-00 - Test - Gen - Run 2",
        "2026-01-01_00-00-00 - Test - Gen - Run 3",
    ]
    assert all(p.is_dir() for p in (first, second, third))


def test_create_unique_folder_creates_parents_only_when_asked(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_unique_folder(tmp_path / "missing", "Run")
    assert create_unique_folder(tmp_path / "missing", "Run", parents=True).is_dir()
