# AutoEnv Agent Instructions

When a task involves creating, modifying, or reviewing an AutoEnv environment registration script under `scripts/`, or changing `config.json` to support such a script, first load and follow:

`.agents/skills/autoenv-script-generator/SKILL.md`

Treat the current branch implementation and tests as authoritative when they differ from examples or prose. Do not run a registered environment script against SSH, Telnet, or HDFS targets unless the user explicitly requests a live run.
