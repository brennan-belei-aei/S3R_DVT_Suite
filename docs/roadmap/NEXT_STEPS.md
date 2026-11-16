# Next steps / roadmap

Goal: a stable, tagged release that test engineers can run on several benches and across several product lines (TLAs) without editing code.

## Phase 0: validate this branch (next 1–2 weeks)
- [ ] Do a dry run, then a live run, of every test on one reference bench using a bench profile.
- [ ] Fix the known undefined names (`is_low_power_fall`, `memLocation`, `j`). See [PROJECT_MEMORY](../ai/PROJECT_MEMORY.md).
- [ ] Remove the hardcoded unit-serial overrides from Power Linearity and Power Accuracy.
- [ ] Run `git rm -r --cached .idea`.
- [ ] Merge `ai-review` into `plugin-architecture`.

## Phase 1: deployment foundation
1. **Tagged releases.** Benches run tags, not branches. Show the version in the launcher title and write it into every run folder.
2. **One-click bench setup.** `setup_bench.ps1` creates `.venv`, installs pinned requirements (lock file), runs `npm ci`, and copies the `*.example.json` templates.
3. **Bench check before RF.** Add a "Test Connections" button per profile: `*IDN?` to each VISA instrument and a ping to the generator and Lynx. A sequence won't start until every check passes.
4. **Run manifest.** Each output folder gets `run_manifest.json` with: operator, bench profile, product/TLA, DUT serial, code version and git hash, and all parameters.
5. **Logging.** Replace `print` with `logging` to a per-run log file for remote diagnosis.

## Phase 2: multi-TLA support
6. **Product profiles drive tests.** Fill `AE_Product_Profiles.json` per TLA with frequencies, setpoints, rated power, and limits. Collection scripts read `PARAMOUNT_PRODUCT_RECOMMENDATIONS_JSON` instead of hardcoded lists.
7. **Locked operator mode.** Operators run with an approved profile and fixed limits, and cannot edit script paths. Engineering mode keeps everything editable.
8. **Central results.** Each lab's network share is the default output parent, set per bench profile, so analysis can compare benches.

## Phase 3: architecture revamp (planning-meeting topics)
9. **Plugin manifests.** Each test folder gets a `plugin.json` (name, scripts, equipment, instructions, setup image) that both GUIs auto-discover, replacing the hand-edited `TESTS` and `BUILTIN_ANALYSES` lists.
10. **Merge near-duplicate scripts.**
    - SLP and DLP collection (about 50% overlap): one script with a DUT adapter.
    - Power Accuracy and Power Linearity analysis (about 45% overlap): one script with a column map.
11. **Shared base for collection and analysis GUIs.** Factor out progress windows, settings load/save, and process polling.
12. **AE command library.** Write a base class plus one subclass per product (PMNT_HF_1XX vs 7434753 overlap about 90%), and give the spectrum analyzer drivers a common interface.
13. **Unified PowerPoint export.** One `export_powerpoint_package(manifest_payload)` replacing 8 copies.
14. **Analog Port Timing.** Done: the five tests run as standalone scripts, and the Beta monolith (`CollectionBeta.py`, `AnalysisBeta.py`) is archived in `archive/analog_port_timing/`.

## Phase 4: quality
15. **CI on every push.** pyflakes, `py_compile`, the self-test, and offline analysis unit tests that use recorded waveform CSVs as fixtures.
16. **Operator documentation.** Bring the draft user guide in `docs/legacy/` up to date for bench profiles.
