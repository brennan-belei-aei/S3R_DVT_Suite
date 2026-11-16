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
