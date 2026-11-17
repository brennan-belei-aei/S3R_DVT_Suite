"""Shared analysis helpers replace per-script copies (Phase 7)."""
from __future__ import annotations

import numpy as np
import pytest

from suite_common import reports
from suite_common.signals import centered_moving_average


# Verbatim copies of the per-script implementations (CW/SLP and DLP analyses).
def original_cw_slp(values, window):
    values = np.asarray(values, dtype=float)
    if window <= 1:
        return values.copy()
    if window % 2 == 0:
        window += 1
    pad = window // 2
    padded = np.pad(values, pad, mode="reflect")
    return np.convolve(padded, np.ones(window) / window, mode="valid")


def original_dlp(values, window):
    if window <= 1:
        return np.asarray(values, dtype=float).copy()
    if window % 2 == 0:
        window += 1
    pad = window // 2
    padded = np.pad(np.asarray(values, dtype=float), pad, mode="reflect")
    kernel = np.ones(window, dtype=float) / window
    return np.convolve(padded, kernel, mode="valid")


@pytest.mark.parametrize("window", [0, 1, 2, 3, 4, 7, 10])
def test_centered_moving_average_matches_the_original_copies(window):
    values = np.random.default_rng(window).normal(size=40).tolist()
    for original in (original_cw_slp, original_dlp):
        np.testing.assert_array_equal(centered_moving_average(values, window), original(values, window))


def test_open_with_default_app_reports_failures_only_when_asked(monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("no association")

    monkeypatch.setattr(reports.sys, "platform", "win32")
    monkeypatch.setattr(reports.os, "startfile", fail, raising=False)
    reports.open_with_default_app("result.xlsx")  # silently ignored, like most former copies
    errors = []
    reports.open_with_default_app("result.xlsx", on_error=errors.append)
    assert [str(e) for e in errors] == ["no association"]
