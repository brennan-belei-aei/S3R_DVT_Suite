# Plugin-architecture migration report

Branch: `refactor/plugin-architecture`. Each phase appends its section here. The final report (moves, deletions, diagram discrepancies, bench checklist) is completed in Phase 8.

## Phase 0: Safety net

### Backups
- `_backup_local_settings/` (gitignored) holds copies of `Bench_Profiles.json`, `Unified_Collection_GUI_Settings.json`, `Unified_Analysis_GUI_Settings.json`, and `RF Ramping/RF_Ramp_Collection_Settings.json`. `AE_Transparency_Testing_Suite_Settings.json` did not exist on this PC.
- A full pre-refactor zip (excluding `.venv` and `node_modules`) was saved outside the repo.

### Baseline checks (untouched code)
| Check | Result |
|---|---|
| `Paramount_Data_Collection_Unified_GUI.py --self-test` | pass. Reports `rf_tuning` script not discovered |
| `Paramount_Data_Analysis_Unified_GUI.py --self-test` | pass |
| `Transparency_Test_Suite_Launcher.py --self-test` | pass |
| `pyflakes suite_common` + root scripts | clean |
| `pytest tests` | 107 passed |

### Test harness added
- `tests/test_registry_snapshot.py` compares against `tests/fixtures/registry_baseline.json`: 14 collection tests, 3 rise/fall modes, 16 analyses, and the legacy aliases. Every definition field must match, and no script discoverable at baseline may become undiscoverable.
- `tests/test_compile_all.py`: every tracked `.py` outside the archives compiles.
- `tests/test_import_smoke.py`: modules that are safe to import. Most collection scripts connect to hardware at import time, so compile-only coverage applies to them.
- `tests/test_self_tests.py`: the three `--self-test` entry points.
- `tests/fakes/`: `FakeAEBus` records `create_buffer_cmd` frames, and `FakeResourceManager` records SCPI. `tests/test_fakes.py` proves both plug into the real libraries.
- `requirements-dev.txt` adds `pytest` and `pyflakes`.

### Pre-existing defects found (not fixed; behavior-preserving rule)
- **`rf_tuning` is not discoverable.** The registry expects `RF_Tuning_Collection.py` / `RF_Tuning_Analysis.py`, but the files are `Frequency Tuning/frequency_tuning_collection.py` and `frequency_tuning_analysis.py`. The plugin registry (Phase 3) will fix this naturally, because `plugin.json` names the entry explicitly.
- **Unregistered tests:** Arc Management, RF Ramping, Quasi CW Oscillations, Rise/Fall Quasi-CW and Split-CW, and Digital Timing exist on disk but are not in `TESTS`/`BUILTIN_ANALYSES`.
- **pyflakes `undefined name` hits:**
  - `AECommands_forFastDAQ` in `Quasi CW Oscillations/{QuasiCW,SplitCW}OscillationsCollection_GUI.py:128` and `Rise and Fall/{QuasiCW,SplitCW}DataCollection_GUI.py:~450`. The module does not exist in the repo.
  - `CSRError` in `AECommandsUniversal.py:149,155` (`rep_cmd`, `rep_cmd_fastDAQ`).
  - `memLocation` in `AE_Commands_7434753.py:1231`.
  - `exc` in `setpoint_transfer_function_analysis.py:320`.
  - `is_low_power_fall` in `CWDataAnalysis_GUI.py:1275`.
  - `j` in `MISC/helperFunctions.py:2341`.
- **Invalid-escape `SyntaxWarning`s** (`'C:\Windows\...'`, `'$\mu$'`) in the AE modules and `helperFunctions.py`. They are harmless today, but will become errors in a future Python.

## Phase 1: Hygiene

### Removed
| Path | Why |
|---|---|
| `RF Execution Timing/` (3 files) | Deprecated by the user. `Digital Timing/` is the live version, and its CLI and `UNITS` are supersets of the deprecated copy. |
| `suite_sdk/AE_Commands_PMNT_HF_1XX.py` | Byte-identical to `AESpecificDependencies/AE_Commands_PMNT_HF_1XX.py`. |
| `suite_sdk/AE_Com_Module.py` | An older strict subset of `AESpecificDependencies/AE_Com_Module.py`: it lacks `create_buffer_cmd_fastDAQ` and `AEB32.close` and has no unique code. |

### Untracked (files kept locally; now gitignored)
- `logs/log_1..12.csv`: output of `Digital Timing/CommandProcessing.py` (`CSV_PATH = "logs/log_%d.csv"`, relative to the working directory).
- `RF Ramping/RF_Ramp_Collection_Settings.json`: per-PC settings. Phase 2 migrates it to `runtime/`.
- New ignore rules: `logs/`, `runtime/`, `*_Settings.json`.

### Moved
| From | To |
|---|---|
| `AESpecificDependencies/_archive/*` | `archive/` (`README.txt` became `README.md`) |
| `Analog Port Timing/CollectionBeta.py`, `AnalysisBeta.py` | `archive/analog_port_timing/` |
| `AESpecificDependencies/MISC/ParamPorter/`, `PulseEyePPTGenerator.py`, `omegaSigmaFastDAQPowerPointGenerator.py` | `tools/` |

**Plan deviation:** `MISC/helperFunctions.py` (with `MISC/__init__.py`) stays in `AESpecificDependencies/`. It is live code: `Harmonics_Spurious_Collection.py` does `from MISC.helperFunctions import *`.

### Changed
- **`Digital Timing/CommandProcessing.py`** now builds `AECommandsSerialTimingAdapter.generator(0, COM_PORT)` / `(IP_ADDRESS, None)` instead of the nonexistent `AE_Commands_PMNT_HF_Anj`. All 23 generator methods it calls exist on `AECommandsUniversal.generator`. The unused `from turtle import delay` is removed.
- **`AEBus_Command_Latency_Collection_GUI.py`:**
  - **Pre-existing bug fixed as part of the required repoint.** `find_script` searched only its own folder, so both latency modes failed with "Missing required script". It now uses `suite_common.paths.discover_script`, which resolves both collectors in `Digital Timing/`.
  - The `rf_scope` mode runs `rf_onoff_scope_capture_GUI.py` with `--no-gui --output-parent <run folder>/rf_onoff_scope --dut-name <generator>`. The Digital Timing script otherwise opens its own settings GUI and writes to `PARAMOUNT_COLLECTION_PARENT_FOLDER` instead of the AEBus run folder.
  - The checkbox label is updated.
- **`RF_Ramp_Collection.py`:** the module search checks `AESpecificDependencies/` where it used to check `suite_sdk/`. It now loads the superset `AE_Com_Module`.
- **Quasi-CW / Split-CW collapse:**
  - `Rise and Fall/QuasiCWDataCollection_GUI.py`: `main(split_cw=False)` and `capture_transition(..., split_cw=False)`. `SplitCWDataCollection_GUI.py` is now a wrapper calling `main(split_cw=True)`.
  - A recording mock showed identical call sequences against the original files: 29 calls for Quasi-CW, 32 for Split-CW.
  - `SplitCWDataAnalysis_GUI.py`, `SplitCWOscillationsCollection_GUI.py`, and `SplitCWOscillationsAnalysis_GUI.py` were byte-identical to their Quasi counterparts and are now `runpy` wrappers.
- **`suite_common/paths.py`:** discovery also skips `archive/`.
- **`Unified_Collection_GUI_Settings.example.json`:** the legacy `analog_port_timing -> CollectionBeta.py` script entry is dropped. If a saved settings file still points there, `load_config` now rediscovers `analog_processing_timing_collection.py`, because the archived path no longer resolves.

### Observations for the user
- `Quasi CW Oscillations/SplitCWOscillationsCollection_GUI.py` was byte-identical to the Quasi version. Unlike the Rise and Fall Split-CW collector, it never called `setSplitCWEnable`. That behavior is preserved, but it may be an oversight.
- All four Quasi/Split-CW collectors still reference the missing `AECommands_forFastDAQ` module and methods absent from `AECommandsUniversal` (`setStartFreq`, `RF_ON`, `serialNumber`, ...). They cannot run today.

### Tests added
`test_aebus_latency_collectors.py` (both collectors resolve to Digital Timing, and every patched constant matches exactly once), `test_quasi_split_cw.py`, and `test_retired_modules.py`. Gate: **122 passed**, all three `--self-test`s pass, and the AEBus adapter resolves `AESpecificDependencies/`.