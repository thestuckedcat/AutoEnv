# AutoEnv Agent Instructions

When a task involves creating, modifying, or reviewing an AutoEnv environment registration script under `scripts/`, or changing `config.json` to support such a script, first load and follow:

`.agents/skills/autoenv-script-generator/SKILL.md`

Treat the current branch implementation and tests as authoritative when they differ from examples or prose. Do not run a registered environment script against SSH, Telnet, or HDFS targets unless the user explicitly requests a live run.

After every modification, perform a related-file review before considering the task complete:

1. Re-read every changed file and the directly related implementation, public exports, tests, examples, documentation, project skills, and configuration.
2. Search the repository for every changed public name, behavior, status, parameter, and example so stale references and missing call sites are found.
3. Reconcile implementation, UT, `tests/README.md`, README, quick start, detailed design, registration guide, `scripts/example.py`, and `.agents/skills/autoenv-script-generator/SKILL.md`; update each affected artifact or explicitly verify why no update is needed.
4. Run focused tests for the changed behavior, then the shared environment-script contract, compilation, registration discovery, skill validation, and the full offline UT suite when available.
5. Report any check that was skipped, any remaining mismatch, and anything not verified against real HDFS, SSH, or Telnet targets.
