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
## Phase 1b: `AECommandsUniversal` is the single DUT command adapter

### Pre-check: `reportUnlockedSubCommand`
The only method whose body differed between `AE_Commands_PMNT_HF_1XX` and `AECommandsUniversal`. The difference is **purely additive**: Universal adds `elif` branches for 27 subcommands that PMNT_HF_1XX did not support (64, 71, 88, 89, 111, 112, 114, 116, 120-122, 128-131, 134-139, 142, 145-147, 173, 174, 183-185, 227, 302). PMNT_HF_1XX raised `Exception('Invalid subcommand!')` for those. Every existing branch is unchanged, so no previously working call behaves differently. The second, dead `elif subcommand == 183` branch in Universal is harmless.

### Golden-frame harness
- `tests/tools/golden_frames.py` recorded the retired libraries **before deletion** into `tests/fixtures/golden_frames/` (about 200 KB). `tests/test_golden_frames.py` replays every case against the current code and requires identical frames `(cmd, send_sizes, recv_sizes, args)`, order, and outcome (return value, or exception type and message).
- **Adapter:** 568 cases, covering the 36 `generator` methods the 12 scripts plus RF Ramping call. Arguments are generic values plus every literal argument tuple found at the scripts' call sites. Each case runs under four reply profiles:
  - `bench`: replies shaped by `recv_sizes`, with host control mode and CSR 0;
  - `csr_ok`;
  - `data`;
  - `csr12_all`.

  Every method reaches a normal return in at least one case (701 frames in total).
- **DLP methods:** 210 cases for the 10 `AECommandsDLPUnits` methods, compared against their Universal names, under five profiles. The fifth, `csr12_on_cmd8`, injects a CSR error on the setpoint command.
- **DLP script sequence:** the real `DLPDataCollection_GUI.py` functions run in collection order (`connect_generator`, two `configure_generator_dlp_mode` conditions, `turn_rf_off_between_conditions`, `disable_generator_dlp_mode`, `close_connections`) under five profiles, including the CSR-error paths and the transport close.
- **Result:** the adapter replay passed **before** the swap (proof the swap is safe) and again after. The DLP replay passes after the Universal additions. **51/51.**

### Changes
- **12 scripts swapped** from `AE_Commands_PMNT_HF_1XX` to `AECommandsUniversal`. Only the import, constructor, and docstring lines changed:
  - the five Analog Port Timing collectors;
  - DC Operating Point, Harmonics and Spurious, Power Accuracy, Power Linearity, Power Profile, and SLP Rise/Fall;
  - `RF_Ramp_Collection.py`, whose `GENERATOR_MODULE` is now `AECommandsUniversal`. This one was not in the plan's list of 12, because it loads its library dynamically.
- **`AECommandsUniversal.py` additions** (nothing existing changed):
  - a module-level `CSRError`, with the same codes and messages as DLPUnits;
  - a private `_dlp_csr_cmd`, with DLPUnits `csr_cmd` semantics;
  - a DLP command section.

  `CSRError` was already referenced but undefined in `rep_cmd`/`rep_cmd_fastDAQ`. Those methods still fail first on `self.device` (`AttributeError`), so their behavior is unchanged.
- **DLP method mapping** (old `AECommandsDLPUnits` name to new `AECommandsUniversal` name):

  | Old | New | Why |
  |---|---|---|
  | `RF_ON` | `DLP_RF_ON` | Universal `rfON` sends a control-mode query first |
  | `RF_OFF` | `DLP_RF_OFF` | Universal `rfOFF` adds a CSR check |
  | `setRegulationMode` | `DLP_setRegulationMode` | Different frame (`'2:1'`, state selector 1) |
  | `DLP_setpoint` | `DLP_setpoint_csr` | Universal's `DLP_setpoint` sends a different frame |
  | `pulseEnable`, `setPulseMode`, `setDLPfreqMode`, `setDLPFreq_s1`, `setDLPFreq_s0`, `setPulseFreq_DutyCycle` | same names | New in Universal; copied verbatim (`self.device` became `self.gen`) |
- **`DLPDataCollection_GUI.py`** uses `AECommandsUniversal.generator`. `close_generator_socket()` now just calls `gen.close()`. The old `device` fallback existed only because `AECommandsDLPUnits.close()` referenced a nonexistent `self.gen`.
- **Deleted:** `AESpecificDependencies/AE_Commands_PMNT_HF_1XX.py`, `AESpecificDependencies/AECommandsDLPUnits.py`. Its unused `flackpack`, `navigator`, and `pinmatch` classes went with it; nothing imported them.
- **`tests/test_retired_modules.py`** now guards all three retired modules (imports, constructors, and module-name strings). I verified it fails on a reintroduced `import AECommandsDLPUnits`.

### Latent defects in `AECommandsUniversal` (reported, not fixed)
- `rep_cmd` / `rep_cmd_fastDAQ` use `self.device` (the constructor sets `self.gen`) and `raise 'unexpected'` (a string raise, which is a `TypeError`). No migrated script calls them.
- `DLP_setpoint` (pre-existing) passes `2` as `recv_sizes`, so it sends a malformed frame. Only the broken Quasi/Split-CW collectors call it. The DLP collector uses `DLP_setpoint_csr`.

Gate: **177 passed**, all three `--self-test`s pass, and RF Ramping resolves `AESpecificDependencies/AECommandsUniversal.py`.