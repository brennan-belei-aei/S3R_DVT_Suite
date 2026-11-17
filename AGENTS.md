# AGENTS.md: working in S3R_DVT_Suite

Read this before changing code. It is the entry point for AI coding agents (GitHub Copilot, Claude Code and similar) and a short checklist for people. [README.md](README.md) covers the layout, and [docs/MIGRATION_REPORT.md](docs/MIGRATION_REPORT.md) explains why things are the way they are.

## What this repo is

Windows bench automation for AE RF generator transparency testing. The launcher starts two GUIs (collection and analysis). Each GUI runs plugin scripts as child processes, and those scripts drive real instruments. A wrong command can put RF power into a load, so **treat every change to a collection script as safety-relevant.**

## Rules

1. **Instruments come from `suite_instruments`.** Collection scripts open instruments with `InstrumentFactory(...).create(role, ...)`.
   - Never `import pyvisa` or an AE module (`AECommandsUniversal`, `Lynx`, `AE_Com_Module`, ...) in `collection_plugins/`.
   - `tests/test_golden_scpi.py::test_no_collection_script_opens_instrument_drivers_directly` enforces this.
2. **AE modules are imported by flat name,** after `import suite_common.bootstrap` (for example `import AECommandsUniversal`). Never use `suite_instruments.adapters.ae.X`; that loads a second copy of the module.
3. **The repo-root line.** Every plugin script that uses `suite_common` starts with:
   ```python
   sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root
   ```
   Never use `parents[N]` (a test forbids it).
4. **No hardcoded addresses.** Use `suite_common.bench.bench_default("<equipment key>")`. Settings paths come from `suite_common.config`. `runtime/` is per-PC and gitignored.
5. **Tests are defined in `plugin.json`,** never in the GUIs. Declare every new `PARAMOUNT_*` variable in `suite_common/process_protocol.py`.
6. **Reuse the shared helpers** instead of copying:
   - `suite_common.output_folders` (`create_unique_folder`, `path_component`, `path_component_strict`);
   - `suite_instruments.tektronix.read_waveform`;
   - `suite_common.reports` and `suite_common.signals`.
7. **Safety defaults.**
   - No instrument I/O at import time.
   - Keep Pulse Sync's `DRY_RUN = True` / `ENABLE_RF = False` defaults.
   - Keep power ceilings (Arc Management `MAX_POWER_W`) and RF-off cleanup in `finally` blocks.
8. **Behavior-preserving changes to collection scripts are proven, not assumed:**
   1. Add or extend a scenario in `tests/tools/golden_scpi.py`.
   2. Record it from the **unchanged** script: `.venv\Scripts\python -W ignore -m tests.tools.golden_scpi record <name>`.
   3. Commit the fixture.
   4. Make the change. The golden log (every VISA write/query and AE-bus frame, in order) must stay identical.
   5. If a change is meant to alter instrument traffic, re-record the fixture in a separate, explained commit.
9. **Don't silently fix pre-existing defects.** Report them (see Known issues), and fix only with the owner's agreement.

## Checks

```powershell
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider -W ignore::SyntaxWarning
.venv\Scripts\python -m pyflakes suite_common suite_sdk suite_apps suite_instruments\tektronix.py suite_instruments\factory.py suite_instruments\adapters\__init__.py Transparency_Test_Suite_Launcher.py tests
.venv\Scripts\python Transparency_Test_Suite_Launcher.py --self-test
.venv\Scripts\python suite_apps\collection_gui.py --self-test
.venv\Scripts\python suite_apps\analysis_gui.py --self-test
```

- No hardware is needed.
- If pytest cannot use its default temp folder, add `--basetemp "$env:TEMP\pt"`.
- For plugin scripts, compare pyflakes findings before and after your change rather than expecting zero.
- Only pass `--self-test` to scripts that handle it (Arc Management, RF Ramping and RF Tuning, both collection and analysis). Other scripts open their GUI or connect to hardware.

## Editing gotchas

- **Line endings and BOMs:** many files use CRLF and some start with a UTF-8 BOM. Preserve both. Edits that rewrite whole files with LF show up as full-file diffs.
- **Path length:** Windows' 260-character path limit bites deep output folders. Tests that write run folders use short temp folders.
- **Never commit** `Block_Diagram_example.png`, `runtime/`, `_backup_local_settings/` or `logs/`.
- **Moving files:** `git mv` with wildcards fails in PowerShell, so move files one at a time.

## Known issues (open; owner decisions recorded)

- **RF Tuning** needs `keysight_53220a_fast.py`, which has never been in the repo. It belongs in `suite_instruments/adapters/keysight/`; the owner left this open.
- **Quasi-CW and Split-CW** need `AECommands_forFastDAQ.py`, which is not in the repo. Their connection code already goes through the `ae.fastdaq` adapter, so copying the module into `suite_instruments/adapters/ae/` makes them runnable; record goldens for them then.
- **Undefined names (pre-existing):**
  - `is_low_power_fall` in `analysis_plugins/rise_fall_time/CWDataAnalysis_GUI.py`;
  - `exc` in `analysis_plugins/analog_port_timing/setpoint_transfer_function_analysis.py`;
  - `j` in `collection_plugins/harmonics_spurious/helperFunctions.py`.
- **AECommandsUniversal latent defects:** `rep_cmd`/`rep_cmd_fastDAQ` use `self.device`; `DLP_setpoint` sends a malformed frame. No migrated script calls them.
- **Per-bench adapter selection is not used by collection scripts yet.** Scripts build `InstrumentFactory` from their GUI addresses, so a bench profile's `"adapters"` section only matters to code that uses `InstrumentFactory.from_bench()`.
- **Not done yet:** the PowerPoint file is not named with the product and date (diagram), and there is no Cancel button (`ChildProcessManager.cancel()` exists).
