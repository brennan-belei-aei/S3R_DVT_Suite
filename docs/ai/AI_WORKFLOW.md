# AI-assisted development workflow

## Context files AI agents read automatically
| File | Scope |
|---|---|
| `.github/copilot-instructions.md` | Repo-wide rules, always loaded by VS Code Copilot |
| `.github/instructions/collection-scripts.instructions.md` | Applied when editing `*Collection*.py` |
| `.github/instructions/analysis-scripts.instructions.md` | Applied when editing `*Analysis*.py` |
| `.github/prompts/*.prompt.md` | Reusable task prompts. Run them from chat with `/<name>`. |
| `AGENTS.md` | Entry point for other agent tools (pointer to the files above) |
| `docs/ai/PROJECT_MEMORY.md` | Conventions, decisions, and known issues. Agents must update it when these change. |

## Review loop used on 2026-11-03
1. **Explore.** Parallel read-only agents survey separate areas (top-level GUIs, test folders, dependencies) and report findings with file and line references.
2. **Propose.** A human picks the items to do from a prioritized list (quick wins vs. bigger items).
3. **Delegate.** Fast agents (Haiku-class) each take a disjoint set of files with explicit instructions: what to change, what not to touch, and how to validate.
4. **Review.** The lead agent reads the diffs, runs pyflakes, `py_compile`, and the self-test, and fixes anything the subagents missed. In this run, review caught one missed import and one missed personal path.
5. **Record.** Update `PROJECT_MEMORY.md` and write a dated review in `docs/reviews/`.

## Rules for delegating to agents
- Give each agent a non-overlapping file list. Two agents must never edit the same file.
- State the safety constraints in every prompt: no instrument I/O at import, keep `DRY_RUN`/`ENABLE_RF` defaults, no hardcoded addresses.
- Agents run no git commands except `git mv` for archive moves. The lead agent commits.
- Every agent ends by compiling the files it touched and listing what it skipped.

## Validation gates before a commit
- `python -m pyflakes` shows no new undefined names.
- `python suite_apps/collection_gui.py --self-test` passes, and every script is discovered.
- A hardware dry run on one bench before merging to a release branch.
