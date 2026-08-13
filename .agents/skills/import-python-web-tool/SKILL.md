---
name: import-python-web-tool
description: Safely inspect an uploaded Python script or Python project ZIP, identify one small local utility, and adapt it into an AutoEnv Web Tools sub-tab. Use when the Agent CLI receives a `.py` file or `.zip` and the user asks to parse, import, register, expose, or turn it into a Web Tool.
---

# Import a Python utility

1. Treat the uploaded path as untrusted. Do not import or run it during inspection.
2. For ZIP input, run `scripts/safe_extract.py INPUT OUTPUT`. Reject password-protected archives, links, absolute paths, `..`, compiled binaries, executables, environment files, credentials, and oversized expansion.
3. Read the Python source statically. Identify entry functions, inputs, outputs, dependencies, file/network access, subprocess calls, global mutations, and import-time side effects.
4. Stop and request direction if the project contains multiple plausible tools or if adapting it requires network access, arbitrary shell commands, secrets, device mutations, or a long-running server.
5. Use `$autoenv-web-tool` to create a thin adapter. Copy only necessary safe logic; exclude virtual environments, caches, secret-bearing tests, build output, and vendored packages.
6. Preserve license notices. Document any third-party dependency in `pyproject.toml`; do not install it without approval.
7. Add offline tests for valid input, invalid input, and serialization. Run the tool validator and full offline suite.
8. Report what was imported, what was intentionally excluded, and any behavior not verified against real data.

Read `references/review-checklist.md` before accepting a ZIP project.
