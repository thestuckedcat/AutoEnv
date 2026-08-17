# AutoEnv Agent Instructions

When a task involves creating, modifying, or reviewing an AutoEnv environment registration script under `scripts/`, or changing `config.json` to support such a script, first load and follow:

`.agents/skills/autoenv-script-generator/SKILL.md`

Treat the current branch implementation and tests as authoritative when they differ from examples or prose. Do not run a registered environment script against SSH, Telnet, or HDFS targets unless the user explicitly requests a live run.

When adding a local utility under `webPage/tools/`, first load `.agents/skills/autoenv-web-tool/SKILL.md`. When adapting an uploaded Python file or Python project ZIP into a Web Tool, first load `.agents/skills/import-python-web-tool/SKILL.md`. Read `docs/WEB_ARCHITECTURE_AND_HANDOFF.md` before changing the Web bridge, structured launch contract, Agent CLI, or upload behavior.

When the user provides logs or asks to investigate `[ERROR]` entries against local source code, determine component ownership, or consult/record historical log diagnoses, first load `.agents/skills/log-error-triage/SKILL.md`. Treat `logKnowledge/` as human-maintained evidence: analyze read-only by default and write a conclusion only after explicit human confirmation.

AutoEnv Web has exactly one supported startup command and endpoint: `python -X utf8 startWeb.py` at `http://127.0.0.1:8765/`. Do not add another launcher, a `webPage.server` module entry point, host/port CLI flags, environment/configurable ports, automatic port selection, compatibility startup paths, or legacy fallback servers. A bind conflict must fail explicitly. Revert behavior through Git commit history instead of retaining old paths or runtime fallbacks.

When establishing or changing a specification-driven development process, read `sdd/README.md` and load the applicable skill under `sdd/`. Use `sdd/sdd-process/SKILL.md` for the full lifecycle. All SDD skills must obey `sdd/common/PROCESS_CONTRACT.md`; Agent verdicts do not replace human baseline approval, risk waivers, or retrospective confirmation.

For every newly added user-facing feature or reusable framework capability, add a companion Markdown record under `newFeature/` before considering the work complete. The record must be included in the same commit as the feature and use `YYYY-MM-DD-short-feature-name.md`. It must summarize the intent, user-visible behavior, architecture/data flow, public interfaces, important semantics and decisions, principal files, tests run, and live-validation gaps. When extending a feature that is still uncommitted, update its existing record instead of creating a fragmented second record. Do not rewrite historical feature records for later unrelated changes; create a new record that links back when needed. Pure bug fixes, refactors, and documentation-only edits do not require a new record unless they materially change behavior.

After every modification, perform a related-file review before considering the task complete:

1. Re-read every changed file and the directly related implementation, public exports, tests, examples, documentation, project skills, and configuration.
2. Search the repository for every changed public name, behavior, status, parameter, and example so stale references and missing call sites are found.
3. Reconcile implementation, UT, `tests/README.md`, README, quick start, detailed design, registration guide, `scripts/example.py`, and `.agents/skills/autoenv-script-generator/SKILL.md`; update each affected artifact or explicitly verify why no update is needed.
4. Run focused tests for the changed behavior, then the shared environment-script contract, compilation, registration discovery, skill validation, and the full offline UT suite when available.
5. Report any check that was skipped, any remaining mismatch, and anything not verified against real HDFS, SSH, or Telnet targets.
