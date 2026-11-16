# Microsoft 365 Copilot prompt: migration-tracking slides

**How to use:** open PowerPoint, choose Copilot, then "Create a presentation". Paste everything inside the box below. For best results, also attach or reference these files from the repo:
- `docs/reviews/2026-11-03_AI_Review_and_Quick_Wins.md`
- `docs/roadmap/NEXT_STEPS.md`

To apply the team theme, start from a deck that uses `Presentation Themes/S3RTransparencyPPTXTheme.thmx`.

---

```text
Create a 12-slide engineering presentation titled "S3R Transparency Testing Suite – Migration Trajectory" for an internal test-engineering group meeting. Audience: test engineers and engineering leads. Tone: concise, technical, status-report style. Use one idea per slide, short bullets (max 5 per slide, max ~12 words each), and add speaker notes (3–5 sentences) to every slide.

Use exactly this content and structure:

1. Title slide – "S3R Transparency Testing Suite: Migration Trajectory". Subtitle: "From per-bench scripts to a deployable multi-bench, multi-TLA suite". Date: November 2026.

2. Why we are changing – Problems in the starting state:
   - Instrument IPs, serial hostnames, and COM ports hardcoded in ~10 scripts
   - Settings files pinned to one engineer's OneDrive paths
   - Same helpers copy-pasted 3–10 times across scripts
   - Script names in the launchers did not match files on disk
   - One script defaulted to live RF with dry-run disabled

3. Where we are today (architecture) – Draw a simple diagram: Launcher → Unified Collection GUI and Unified Analysis GUI → per-test child scripts → PowerPoint Builder. Add a side box "suite_common shared package" connected to all scripts and a box "Bench_Profiles.json" feeding the Collection GUI.

4. Completed – 2026-11-03 AI-assisted review (use a checklist visual):
   - Bench profiles selectable in the GUI with instrument auto-detect
   - All addresses come from env vars or the bench profile; none hardcoded
   - Shared suite_common package replaces duplicated helpers
   - Safe defaults: dry-run and RF off; missing addresses stop before any I/O
   - 12882 kHz standardized; script discovery fixed; legacy code archived
   - requirements.txt, example settings, docs, AI context files added

5. Impact in numbers – Show as large-number callouts:
   - ~980 lines removed, ~420 added across 19 files
   - 0 hardcoded instrument addresses in active scripts (from ~20)
   - 12 of 12 collection scripts auto-discovered
   - 9 unused legacy files archived

6. How the work was done (AI-assisted workflow) – Process diagram with 5 steps: Explore (parallel read-only AI agents) → Propose (human selects scope) → Delegate (fast AI agents on disjoint files) → Review (lead agent + lint + self-test) → Record (project memory + dated review doc). Note: "No hardware in the loop yet – validation pending."

7. Migration trajectory (timeline / roadmap visual with 5 phases left-to-right):
   - Phase 0 – Validate ai-review branch on a reference bench (now)
   - Phase 1 – Deployment foundation: tagged releases, one-click setup, pre-RF bench check, run manifest, logging
   - Phase 2 – Multi-TLA: product profiles drive setpoints/limits, locked operator mode, central results share
   - Phase 3 – Architecture revamp: plugin manifests, merged SLP/DLP and Accuracy/Linearity scripts, AE command base classes
   - Phase 4 – Quality: CI checks, analysis unit tests with recorded waveforms, updated operator guide

8. Migration tracker – Create a table with columns: Workstream | Status | Owner | Target. Rows:
   - Bench profiles & address parameterization | Done | AI review | Nov 2026
   - Shared suite_common package | Done | AI review | Nov 2026
   - Hardware validation of ai-review branch | Not started | TBD | TBD
   - Fix known undefined-name bugs (3) | Not started | TBD | TBD
   - Tagged release + one-click bench setup | Planned | TBD | TBD
   - Pre-RF bench connection check | Planned | TBD | TBD
   - Product (TLA) profiles drive test parameters | Planned | TBD | TBD
   - Plugin manifests for tests | Planned | TBD | TBD
   - CI + analysis unit tests | Planned | TBD | TBD
   Color the Status cells: Done = green, Not started = gray, Planned = blue.

9. Operator-visible changes – What test engineers will notice:
   - Create a bench profile once per PC (addresses saved, selectable)
   - Saved GUI selections reset once after update
   - Pulse Sync opens in dry-run with RF off; tick both to run live
   - Default output folder is now the user's home or the chosen parent folder

10. Risks and mitigations – Two-column layout:
   - Untested on hardware → dry + live run per test before merge
   - Module-level script flow edited by AI agents → targeted review of CW and Power Linearity
   - Existing latent bugs found → tracked in project memory, fix in Phase 0
   - Bench wiring differences (e.g. trigger channel) → store per-bench in profiles

11. Decisions needed from the group – Bulleted asks:
   - Pick reference bench and owner for Phase 0 validation
   - Agree release cadence and tagging policy
   - Agree first TLAs to encode in product profiles
   - Approve plugin-manifest approach for the revamp
   - Assign owners for the tracker rows marked TBD

12. Next steps & summary – 3 key takeaways and the immediate next 2 weeks: validate on hardware, fix known bugs, merge ai-review, schedule Phase 1 kickoff.

Design guidance: clean corporate style, dark-blue headings, plenty of white space, icons for checklist items, use a horizontal timeline graphic on slide 7 and a status-colored table on slide 8. Do not invent metrics beyond those given.
```
