# AI review and quick wins: 2026-11-03

**Branch:** `ai-review` (based on `plugin-architecture` @ `a8bef59`)
**Method:** Three read-only Haiku explore agents surveyed the repo, a human chose the scope, four Haiku agents applied the fixes in parallel, and the lead agent reviewed and validated everything. The full process is in [../ai/AI_WORKFLOW.md](../ai/AI_WORKFLOW.md).

## Summary
| Area | Before | After |
|---|---|---|
| Instrument addresses | Hardcoded IPs, serial-number hostnames, and COM ports in about 10 scripts | Bench profiles selectable in the GUI (`Bench_Profiles.json`), with **Detect Instruments** |
| Address hand-off | Regex rewrite of a temporary copy of each script | Env vars only; scripts launched directly |
| Script discovery | Candidate filenames that didn't exist; settings pinned to one user's OneDrive | Recursive discovery by real name; repo-relative paths; auto-rediscovery |
| Shared code | 6 copies of the output-folder helpers, 5 of the number parsers, 3 of the PowerPoint helpers, 10 different `sys.path` hacks | `suite_common/` package |
| Safety | Pulse Sync defaulted to `DRY_RUN=False` and `ENABLE_RF=True` despite its docstring | Defaults are now dry-run with RF off. Missing addresses raise an error before any instrument I/O. |
| Frequencies | 12883 kHz in DC Operating Point and Power Profile | 12882 kHz everywhere |
| Dead code | Scratch scripts, unused drivers, commented-out blocks | Moved to `AESpecificDependencies/_archive/` or deleted |
| Repo hygiene | No requirements file; personal settings committed; Java/Maven `.gitignore` | `requirements.txt`, `*.example.json`, cleaned `.gitignore`, `docs/`, AI context files |

## Detailed changes
- **New `suite_common/`.** Modules: `bench` (profiles, `bench_default`, VISA normalization, detection), `paths` (discovery, portable paths, child-process flags), `output_folders`, `inputs`, `powerpoint`, `bootstrap`.
- **Collection GUI.**
  - Added a bench profile drop-down with Save, Save As, and Delete, and address combo boxes populated from profiles and VISA detection.
  - Added equipment fields: waveform generator, Pulse Sync slave generator, AEBus COM port.
  - Added a Pulse Sync test entry. Dropped the unused power-meter field.
  - The self-test now checks the environment hand-off and script discovery.
- **Launcher and Analysis GUI.**
  - Both use the shared discovery helpers. Saved paths are portable.
  - The PowerPoint builder path always comes from code, so a stale saved path can't break it.
  - A warning is printed when `AE_Product_Profiles.json` is invalid.
- **PowerPoint builder.** The default theme name now matches `Presentation Themes/S3RTransparencyPPTXTheme.thmx`, and the sandbox `NODE_PATH` entry was removed.
- **Collection scripts** (CW, SLP, DLP, Analog Port Timing, DC Operating Point, Harmonics, Power Accuracy, Power Linearity, Power Profile, AEBus Latency, Pulse Sync):
  - Use `bench_default` for all addresses and the shared helpers.
  - Wildcard `time` imports were made explicit.
  - Power Linearity `debugMode` was removed. `WARMUP_TIME_S` can be overridden with an env var.
  - Power Profile's dead Lynx baud field was removed, and a `NameError` on Start (`np` was never imported) was fixed.
  - The Pulse Sync trigger channel is no longer chosen by checking the generator serial; it is a GUI field, default CH3, overridable by env var.
- **AE dependencies.**
  - `AECommandsDLPUnits.__repr__` no longer calls a method that doesn't exist.
  - String exceptions now raise `RuntimeError`.
  - Fixed the Keysight `SaveJPG` command string.
  - Removed the duplicate `lynx` class from `AECommands.py`.
  - Added named AEBus baud constants.
  - Added `__init__.py` to `MISC/` and `SpectrumAnalyzer/`.
- **Renames.** `Harmomics_Spurious_Analysis.py` became `Harmonics_Spurious_Analysis.py`. Notes and the user guide moved to `docs/legacy/`.

## Behavior changes operators will notice
1. **First launch after updating.** Saved selections reset to defaults; the live settings files are no longer in git. Create a bench profile on each PC.
2. **Blank addresses.** A script run with no profile or env value now stops with a clear message instead of contacting a lab-specific IP.
3. **Pulse Sync defaults.** It opens in dry-run with RF off; tick both boxes to run for real. On the Zynq bench, set the trigger channel to 4.
4. **Default output folders.** DC Operating Point, Power Linearity, and DLP now default to the user's home folder instead of personal or `C:\TekWaveforms` paths.

## Validation performed
- `py_compile` on every active `.py` file passes.
- `pyflakes` shows no new undefined names. The pre-existing ones are listed in [PROJECT_MEMORY.md](../ai/PROJECT_MEMORY.md).
- `Paramount_Data_Collection_Unified_GUI.py --self-test` passes, and all 12 collection scripts are discovered.
- **Not done:** hardware runs. Each test needs one dry run and one live run per bench type before release.

## Risks and rollback
- Changes stay on `ai-review` until a hardware run passes, and the branch can be dropped or reverted at any time.
- The highest-risk edits are the subagent changes to module-level script flow: CW and Power Linearity run code at import.
