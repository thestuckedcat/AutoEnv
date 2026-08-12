---
name: self-review-and-verdict
description: Conduct a skeptical final self-review of a low-level software change and issue an evidence-based delivery recommendation. Use before commit, merge, release, handoff, or after implementation claims need independent rechecking.
---

# Self Review and Verdict

Read `../common/PROCESS_CONTRACT.md` and `../common/templates/review.template.md` completely. Treat prior Agent statements as untrusted until reproduced.

## Review passes

1. Scope pass: compare request, changed files and commit diff; find accidental or missing scope.
2. Correctness pass: inspect happy path, boundary/error/recovery paths, state cleanup and result propagation.
3. Low-level pass: concurrency/interrupt, timeouts, resources, ABI/protocol, endian/alignment, reset/power, hardware assumptions and observability.
4. Security pass: trust boundaries, credentials, input/path/archive handling, command execution, dependency and deployment exposure.
5. Trace pass: sample every critical REQ and verify design→code→test→evidence; search stale names and contradictory docs.
6. Verification pass: run focused then broad checks. Distinguish mocked, host, integration, target and HIL results.
7. Maintainability pass: ensure handoff docs, commands, known traps and remaining work items are precise.

Try to falsify success with at least three plausible failure scenarios. Do not fix unrelated findings under the guise of review; classify them.

## Required output

Lead with findings ordered by severity and exact evidence. Then list executed checks, skipped checks, trace coverage, documentation consistency, remaining risk and an Agent **建议判决**. `PASS` is forbidden when a requested deliverable, blocking test, required human decision or critical target validation is missing.
