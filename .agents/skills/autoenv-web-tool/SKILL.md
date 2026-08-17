---
name: autoenv-web-tool
description: Create, modify, and validate local or RunContext workflow utilities registered as dynamic sub-tabs under AutoEnv webPage Tools. Use when asked to add a parser, converter, diagnostic helper, log collection workflow, or other utility to the AutoEnv Web Tools page.
---

# Add an AutoEnv Web Tool

1. Read `autoenv/web_tools.py`, `webPage/tools/system_info.py`, `webPage/QUICK_START.md`, and relevant tests.
2. Clarify the tool's inputs, outputs, validation, failure shape, and whether it is a local JSON utility or a resource-bound workflow. Never invent company business rules.
3. Choose one starting point. For a local Tool, copy `webPage/tools/_template.py` to a descriptive non-underscore `.py` filename. For generated local or workflow Tools, run `scripts/scaffold_tool.py NAME --title TITLE --description DESCRIPTION` from the repository root; workflows add `--kind workflow` and a supported `--renderer`. Never edit `_template.py` into a live Tool and never overwrite an existing file.
4. Replace every template placeholder or edit the generated `webPage/tools/NAME.py` for tool-specific behavior. Use field types `text`, `number`, `textarea`, `select`, or `checkbox`; keep options JSON-serializable. Core UI edits are only justified for a new reusable renderer, not for ordinary discovery.
5. Keep one `@register_web_tool` per file. A `kind="local"` body accepts one values dictionary and returns a JSON-serializable value. A `kind="workflow"` body accepts one `RunContext`, declares resources through literal `ctx.register_*()` calls, uses AutoEnv SDK operations, and returns `None` or an AutoEnv result.
6. Local Tools must not access the network, devices, subprocesses, or arbitrary paths. Workflow Tools may access declared environment resources only through `RunContext` and AutoEnv SDK APIs. Keep arbitrary shell execution, dynamic `eval`/`exec`, user-provided imports, and secrets in results out of both kinds.
7. Add focused offline tests. Run `scripts/validate_tool.py webPage/tools/NAME.py`, then the focused test and full `pytest` suite.
8. Confirm the Tool remains absent from `list_scripts()` and the Environment Startup dropdown. Confirm `_template.py` itself remains absent from `describe_tools()`. Do not edit `webPage/index.html`, `webPage/app.js`, or CSS merely to make the tab appear; discovery is automatic.

Read `references/contract.md` when adding non-text fields or structured output.
