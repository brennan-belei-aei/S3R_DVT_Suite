"""AECommandsUniversal must reproduce the retired libraries' AE-bus traffic exactly.

Fixtures were recorded from AE_Commands_PMNT_HF_1XX and AECommandsDLPUnits before they
were deleted (see tests/tools/golden_frames.py). Each case compares frames
(cmd, send_sizes, recv_sizes, args), their order, and the call outcome.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from tests.tools.golden_frames import DLP_PROFILES, load_fixture, run_dlp_script_sequence, run_method


def _by_method(cases: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        grouped[case["method"]].append(case)
    return dict(sorted(grouped.items()))


ADAPTER_CASES = _by_method(load_fixture("dut_adapter_pmnt_hf_1xx.json"))
DLP_METHOD_CASES = _by_method(load_fixture("dlp_units_methods.json"))
DLP_SEQUENCES = {run["profile"]: run for run in load_fixture("dlp_script_sequence.json")}


def _mismatches(cases: list[dict], method_key: str) -> list[str]:
    bad = []
    for case in cases:
        got = run_method("AECommandsUniversal", case[method_key], case["args"], case["profile"])
        if got != {"frames": case["frames"], "outcome": case["outcome"]}:
            bad.append(f"{case[method_key]}{tuple(case['args'])} [{case['profile']}]: "
                       f"expected {case['frames']} -> {case['outcome']!r}, got {got['frames']} -> {got['outcome']!r}")
    return bad


@pytest.mark.parametrize("method", ADAPTER_CASES)
def test_universal_matches_pmnt_hf_1xx(method):
    bad = _mismatches(ADAPTER_CASES[method], "method")
    assert not bad, "\n".join(bad[:5])


@pytest.mark.parametrize("method", DLP_METHOD_CASES)
def test_universal_dlp_methods_match_dlp_units(method):
    bad = _mismatches(DLP_METHOD_CASES[method], "new_method")
    assert not bad, "\n".join(bad[:5])


@pytest.mark.parametrize("profile", DLP_PROFILES)
def test_dlp_script_sequence_unchanged(profile):
    assert run_dlp_script_sequence(profile) == DLP_SEQUENCES[profile]
