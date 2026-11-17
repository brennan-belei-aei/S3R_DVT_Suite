# Handoff prompt: S3R_DVT_Suite refactor (paste everything below into GitHub Copilot)

You are continuing a phased, behavior-preserving refactor of the S3R_DVT_Suite repository (Windows, PowerShell, Python 3.14 venv at `.venv\Scripts\python`). The target is the plugin-based architecture in `Block_Diagram_example.png` (untracked; never commit it). Work one phase or batch at a time. Every change must keep data collection working exactly as before and be proven by tests. Make one commit per phase or batch, never rewrite history, and stop for the owner's review after each batch.

## Branches

- `refactor/plugin-architecture` (tag `phase5-complete`): Phases 0–5. This is the known-good fallback; do not modify it.
- `refactor/phase6-instruments`: Phase 6 work. **Continue here.** Phase 6 is complete; run `git --no-pager log --oneline -5` for the latest commit.

## Owner decisions (Amendment A)

- `RF Execution Timing/` was deprecated and removed. `Digital Timing` (now `collection_plugins/rf_on_off_timing/`) is live and uses `AECommandsSerialTimingAdapter`.
- `AE_Commands_PMNT_HF_Anj`, `AE_Commands_PMNT_HF_1XX` and `AECommandsDLPUnits` are retired. `AECommandsUniversal` is the single DUT adapter; the DLP methods were copied into it wire-for-wire.
- `Lynx`, `AECommands` (Navigator), `AE_Commands_7434753` (Pulse Sync) and the Keysight spectrum analyzer drivers are kept as-is.

## Completed

- **Phase 0:** pytest harness, fakes, backups.
- **Phase 1 / 1b:** removed dead code and duplicates, archived old files to `archive/`, moved MISC tools to `tools/`, and swapped all scripts to AECommandsUniversal. Golden AE-frame fixtures are in `tests/fixtures/golden_frames/`.
- **Phase 2:** `config/` (committed) and `runtime/` (gitignored). JSONs are no longer at the root. Accessors are in `suite_common/config.py`.
- **Phase 3:** `suite_sdk` (plugin schema, validator, registry, SuiteContext). The GUIs build their lists from the registry.
- **Phase 4:** layout is `collection_plugins/<id>/`, `analysis_plugins/<id>/` (each with a `plugin.json`), `suite_apps/` (collection/analysis GUIs, process managers), and `assets/`.
- **Phase 5:** launcher process protocol (`suite_common/process_protocol.py`), firmware/adapter compatibility, and run/analysis manifests (`suite_common/manifests.py`).
- **Phase 6 part 1:** `suite_instruments/` instrument layer:
  - `factory.py`: `InstrumentFactory(addresses, adapters).create(role, address=..., resource_manager=..., open_attempts=...)`.
  - `adapters/__init__.py`: adapter catalog, `DEFAULT_ADAPTERS`, `csr_check`.
  - `transport/visa.py`: `VisaTransport`, `resource_manager()`.
  - `transport/ae_com.py`, `interfaces/`, `errors.py`.
  - The AE modules live in `suite_instruments/adapters/ae/`.
- **Phase 6 part 2:** collection scripts migrated onto `InstrumentFactory` (connection code only), each gated by golden SCPI/AE-bus logs:
  - Batch 1: CEX Lock, Interlock Response, RF Enable timing.
  - Batch 2: Setpoint Transfer Function, Analog Processing Timing.
  - Batch 3: Rise/Fall CW, SLP, DLP, and Pulse Sync (via a new `ae.7434753` adapter).
  - Batch 4: Arc Management, RF Ramping, RF Tuning, and the Digital Timing capture GUI plus CommandProcessing (via `ae.serial_timing`).
  - Batch 5: DC Operating Point, Power Accuracy/Linearity/Profile and Harmonics, plus the Quasi-CW collectors (connection code only, via `ae.fastdaq`; they still need the missing `AECommands_forFastDAQ.py`).
  - **Phase 6 exit criterion met**, and enforced by `test_no_collection_script_opens_instrument_drivers_directly`.
- Arc Management's default regulation mode is now 6 (owner decision).
- `docs/MIGRATION_REPORT.md` has a section per phase and batch, including bench checks for the owner.

## Remaining tasks

1. **Phase 7 (next):** deduplicate code that exists in 3 or more copies, such as scope waveform download, run-folder creation, `safe_component`/`safe_name`, and `visa_from_ip`-style helpers. Move them into `suite_common`/`suite_instruments`, keeping all goldens identical.
2. **Phase 8:** update `README.md` and docs, add `AGENTS.md`, write the final migration report and bench checklist. Delete `_backup_local_settings/` only after the owner confirms.
3. **Open issues (owner decisions recorded; do not silently fix):**
   - RF Tuning needs `keysight_53220a_fast.py`, which has never been in the repo; it belongs in `suite_instruments/adapters/keysight/`. The owner chose to leave this open for now.
   - The Quasi/Split-CW collectors need `AECommands_forFastDAQ.py`, which is not in the repo. Their connection code already goes through `ae.fastdaq`; copying the module into `suite_instruments/adapters/ae/` makes them runnable. Record goldens for them then.
   - The pptx naming is deferred; the Paramount Plus and Apex firmware lists are empty.

## Required method for each migration batch

1. **Record first.** Add scenarios and drivers to `tests/tools/golden_scpi.py`:
   - Function-level scripts use a driver function.
   - Module-level Tk scripts run as `__main__` via `GuiRun`, using `tests/tools/fake_tk.py`, which fills labelled fields and clicks Start.
   - Use the Bench model switches (`trigger_model`, `acquisition_model`, `pulse_model`, `ae_trigger_model`, `identities`, `navigator_port`, `visa_faults`, `fixed_frequency_hz`, `power_limits_w`, `ae_values`) as needed. Include fallback and error-path scenarios.
   - Record **from the unmodified scripts** with `.venv\Scripts\python -W ignore -m tests.tools.golden_scpi record <name>`. Check that the logs are deterministic (no temp paths or timestamps), then commit the fixtures **before** migrating.
2. **Migrate connection code only.** Pattern:
   - Replace `import pyvisa` / `import AE...` with `from suite_instruments.factory import InstrumentFactory` and `from suite_instruments.transport.visa import resource_manager`.
   - Open instruments with `InstrumentFactory({role: addr}).create(role, resource_manager=rm, open_attempts=1)`.
   - Open the DUT with `.create("generator")`.
   - Handlers for `pyvisa.VisaIOError` become `suite_instruments.errors.InstrumentError`. Preserve each branch's meaning: `InstrumentTimeout` is also a `TimeoutError`.
3. **Verify:**
   - `.venv\Scripts\python -m pytest tests -q -p no:cacheprovider -W ignore::SyntaxWarning` (415 tests currently pass; if pytest cannot access its default temp folder, add `--basetemp "$env:TEMP\pt"`), with all goldens identical and the guard test in `tests/test_golden_scpi.py` passing.
   - `.venv\Scripts\python -m pyflakes suite_common suite_sdk suite_apps Transparency_Test_Suite_Launcher.py tests` must be clean. The migrated scripts' pyflakes findings must be unchanged from before the migration.
   - Run `--self-test` on `Transparency_Test_Suite_Launcher.py`, `suite_apps\collection_gui.py` and `suite_apps\analysis_gui.py`. Only run a plugin script with `--self-test` if it literally handles that flag; otherwise it launches its GUI.
4. Append a batch section with bench checks to `docs/MIGRATION_REPORT.md`, commit with the trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`, and stop for review.

## Conventions and gotchas

- **AE modules:** always import them by flat name (`import AECommandsUniversal`) after `import suite_common.bootstrap`. Never use `suite_instruments.adapters.ae.X`, which loads a duplicate module.
- **Repo root:** every plugin script uses this repo-root line, and tests forbid `parents[N]`:
  `sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "suite_common").is_dir())))  # repo root`
- **Line endings:** edit with patch scripts that preserve CRLF and assert each replacement matches exactly once. `git mv` with globs fails in PowerShell; move files one at a time.
- **Staging:** stage with `git add -A -- . ':!Block_Diagram_example.png'`. Delete `tests/**/__pycache__` before committing.
- **Path length:** golden tests use short temp folders because of Windows' 260-character path limit.
