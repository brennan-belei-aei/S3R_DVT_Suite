# AI-assisted development workflow

## Context files
| File | Scope |
|---|---|
| `AGENTS.md` | Repo rules, checks and known issues. GitHub Copilot and other agent tools load it automatically; people should read it too. Update it when a convention or known issue changes. |
| `docs/MIGRATION_REPORT.md` | Why the code is shaped the way it is (one section per refactor phase). |
| `docs/HANDOFF_PROMPT.md` | Paste-ready prompt for continuing work in a new agent session. |

## Changing a collection script (golden-gated)
1. **Record first.** Add or extend a scenario in `tests/tools/golden_scpi.py`. Record it from the unchanged script (`.venv\Scripts\python -W ignore -m tests.tools.golden_scpi record <name>`), then commit the fixture on its own.
2. **Change.** Keep the change as small as the goal allows. Connection code, shared helpers and measurement code are separate concerns.
3. **Prove.** The golden log (every VISA write/query and AE-bus frame, in order) stays identical, the full test suite passes, and pyflakes findings for the touched scripts are unchanged.
4. **Record.** Add a section to `docs/MIGRATION_REPORT.md` with bench checks for the owner.

## Review loop used on 2026-11-03
1. **Explore.** Parallel read-only agents survey separate areas (top-level GUIs, test folders, dependencies) and report findings with file and line references.
2. **Propose.** A human picks the items to do from a prioritized list (quick wins vs. bigger items).
3. **Delegate.** Fast agents each take a disjoint set of files with explicit instructions: what to change, what not to touch, and how to validate.
4. **Review.** The lead agent reads the diffs, runs the checks, and fixes anything the subagents missed.
5. **Record.** Update `AGENTS.md` (conventions, known issues) and write a dated review in `docs/reviews/`.

## Rules for delegating to agents
- Give each agent a non-overlapping file list. Two agents must never edit the same file.
- State the safety constraints in every prompt: no instrument I/O at import, keep `DRY_RUN`/`ENABLE_RF` defaults, no hardcoded addresses.
- Agents run no git commands except `git mv`. The lead agent commits.
- Every agent ends by running the checks for the files it touched and listing what it skipped.

## Validation gates before a commit
- `pytest tests` passes, including the golden SCPI/AE-bus logs.
- pyflakes shows no new findings in the touched files.
- The three app `--self-test`s pass, and every plugin is discovered.
- Before merging to a release branch, a hardware run on one bench (see the bench checklist at the end of `docs/MIGRATION_REPORT.md`).
