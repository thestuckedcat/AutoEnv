---
name: autoenv-web-tool
description: Create, modify, and validate Python utilities registered as dynamic sub-tabs under AutoEnv webPage Tools. Use when asked to add an error-code parser, converter, diagnostic helper, local developer utility, or any other small Python function to the AutoEnv Web Tools page without changing the core frontend navigation.
---

# Add an AutoEnv Web Tool

1. Read `autoenv/web_tools.py`, `webPage/tools/system_info.py`, `webPage/QUICK_START.md`, and relevant tests.
2. Clarify the tool's inputs, outputs, validation, failure shape, and whether it can safely run locally. Never invent company business rules.
3. Run `scripts/scaffold_tool.py NAME --title TITLE --description DESCRIPTION` from the repository root. Do not overwrite an existing file.
4. Edit only the generated `webPage/tools/NAME.py` for tool-specific layout and logic. Use field types `text`, `number`, `textarea`, `select`, or `checkbox`; keep options JSON-serializable.
5. Keep one `@register_web_tool` per file. Make the body accept one values dictionary and return a JSON-serializable value.
6. Keep network access, device mutations, arbitrary shell execution, dynamic `eval`/`exec`, and user-provided imports out of Tools. Those flows belong in registered environment scripts or the Agent CLI.
7. Add focused offline tests. Run `scripts/validate_tool.py webPage/tools/NAME.py`, then the focused test and full `pytest` suite.
8. Do not edit `webPage/index.html`, `webPage/app.js`, or `webPage/styles.css` merely to make the tab appear; discovery is automatic.

Read `references/contract.md` when adding non-text fields or structured output.
