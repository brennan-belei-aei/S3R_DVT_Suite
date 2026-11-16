"""Split-CW is the Quasi-CW procedure plus Split-CW enable/disable; wrappers must point at real scripts."""
from __future__ import annotations

import importlib.util
import re
import types

import pytest

from tests.conftest import REPO_ROOT

COLLECTOR = REPO_ROOT / "Rise and Fall" / "QuasiCWDataCollection_GUI.py"
WRAPPERS = [
    REPO_ROOT / "Rise and Fall" / "SplitCWDataAnalysis_GUI.py",
    REPO_ROOT / "Quasi CW Oscillations" / "SplitCWOscillationsCollection_GUI.py",
    REPO_ROOT / "Quasi CW Oscillations" / "SplitCWOscillationsAnalysis_GUI.py",
]


class RecordingGenerator:
    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.log.append(name)
            return 1000 if name == "reportUserPowerLimit" else "SN"
        return call


def _run_collection(split_cw: bool) -> list[str]:
    spec = importlib.util.spec_from_file_location(f"quasi_cw_{split_cw}", COLLECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    log: list[str] = []
    module.AECommands_forFastDAQ = types.SimpleNamespace(generator=lambda _ip: RecordingGenerator(log))
    module.get_user_settings = lambda: {"generator_ip": "G", "output_folder": ".", "initial_power": 100}
    module.read_fastdaq_capture = lambda _gen: ([0], [0])
    module.create_run_folder = lambda *_args: "OUT"
    module.save_settings_file = lambda *_args: None
    module.save_waveform_csv = lambda *_args: None
    module.time = types.SimpleNamespace(sleep=lambda _s: None)
    module.main(split_cw=split_cw)
    return log


def test_split_cw_adds_only_enable_and_disable():
    quasi, split = _run_collection(False), _run_collection(True)
    split_only = ["setSplitCWEnable", "setSplitCWDisable"]
    assert not set(split_only) & set(quasi)
    assert split.count("setSplitCWEnable") == 2
    assert split.count("setSplitCWDisable") == 1
    assert [c for c in split if c not in split_only] == quasi


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
def test_wrapper_targets_exist(wrapper):
    target = re.search(r'with_name\("([^"]+)"\)', wrapper.read_text(encoding="utf-8")).group(1)
    assert (wrapper.parent / target).is_file()
