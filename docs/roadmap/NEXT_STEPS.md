# Next steps / roadmap

Goal: a stable, tagged release that test engineers can run on several benches and across several product lines (TLAs) without editing code.

Status note (2026-11-17): the plugin-architecture refactor (docs/MIGRATION_REPORT.md) finished several items below; they are marked **Done**.

## Phase 0: validate the refactor
- [ ] Do a dry run, then a live run, of every test on one reference bench using a bench profile. Use the bench checklist at the end of docs/MIGRATION_REPORT.md.
- [ ] Merge `refactor/phase6-instruments` into `refactor/plugin-architecture`, then into `main`, after the bench run.
- [ ] Fix the known undefined names (`is_low_power_fall`, `exc`, `j`; see [AGENTS.md](../../AGENTS.md)). (`memLocation` went away with the retired AE module.)
- [ ] Remove the hardcoded unit-serial overrides from Power Linearity and Power Accuracy.
- [x] **Done:** `.idea` is no longer tracked.

## Phase 1: deployment foundation
1. **Tagged releases.** Benches run tags, not branches. Show the version in the launcher title and write it into every run folder.
2. **One-click bench setup.** `setup_bench.ps1` creates `.venv`, installs pinned requirements (lock file), runs `npm ci`, and copies the `config/templates/*.example.json` templates into `runtime/`.
3. **Bench check before RF.** Add a "Test Connections" button per profile: `*IDN?` to each VISA instrument and a ping to the generator and Lynx. A sequence won't start until every check passes. `InstrumentFactory.check()` / `require()` already validate addresses, adapters and capabilities.
4. **Done: run manifest.** `run_manifest.json` / `analysis_manifest.json` record product, firmware, adapter, bench profile, addresses, settings, suite commit, script hash and file hashes (Phase 5). Operator name and DUT serial are not captured yet.
5. **Logging.** Replace `print` with `logging` to a per-run log file for remote diagnosis. `suite_instruments` already logs every instrument write/query and AE frame at DEBUG.

## Phase 2: multi-TLA support
6. **Product profiles drive tests.** `config/AE_Product_Profiles.json` (schema v2) has firmware rows with `test_defaults`/`constraints`, but they are still empty. Fill them per TLA, and make collection scripts read them instead of hardcoded lists. The Paramount Plus and Apex firmware lists are empty.
7. **Locked operator mode.** Operators run with an approved profile and fixed limits, and cannot edit script paths. Engineering mode keeps everything editable.
8. **Central results.** Each lab's network share is the default output parent, set per bench profile, so analysis can compare benches.
9. **Per-bench adapters in collection.** Scripts build `InstrumentFactory` from their GUI addresses, so a bench profile's `"adapters"` section is not used yet. Merge in `InstrumentFactory.from_bench().adapters`, gated by the goldens.

## Phase 3: architecture
10. **Done: plugin manifests.** Every test folder has a `plugin.json`, and both GUIs discover them (Phases 3-4).
11. **Done: AE command library and instrument layer.** `AECommandsUniversal` is the single DUT adapter, and every collection script opens instruments through `suite_instruments` (Phases 1b and 6). Still open: vendor adapters (Tektronix, Rohde & Schwarz) that move SCPI out of the scripts.
12. **Merge near-duplicate scripts.**
    - SLP and DLP collection (about 50% overlap): one script with a DUT adapter.
    - Power Accuracy and Power Linearity analysis (about 45% overlap): one script with a column map.
13. **Shared base for collection and analysis GUIs.** Factor out progress windows, settings load/save, and process polling. The process managers are already shared (Phase 5); helpers were deduplicated in Phase 7.
14. **Unified PowerPoint export.** One `export_powerpoint_package(manifest_payload)` replacing 9 per-analysis variants (only `powerpoint_export_requested` is shared so far).
15. **Done: Analog Port Timing.** The five tests run as standalone scripts, and the Beta monolith is archived.
16. **Missing drivers.** `keysight_53220a_fast.py` (RF Tuning) and `AECommands_forFastDAQ.py` (Quasi-CW / Split-CW). Each slot is ready in `suite_instruments`.
17. **Diagram items not yet built:** a Cancel button (the process manager supports it), and PowerPoint output named `Transparency_Test_Results` with product and date stamps.

## Phase 4: quality
18. **CI on every push.** pytest (hardware-free, including the golden SCPI/AE-bus logs), pyflakes, and the self-tests. Add offline analysis unit tests that use recorded waveform CSVs as fixtures.
19. **Operator documentation.** Bring the draft user guide in `docs/legacy/` up to date for bench profiles and the new launcher.
