---
name: run-sdd-checkpoint
description: Apply a deterministic SDD review gate to artifacts and evidence. Use at G0-G7, before implementation, before merging or release, or whenever a standalone PASS/CONDITIONAL_PASS/FAIL/BLOCKED decision is needed.
---

# Run SDD Checkpoint

Read `../common/PROCESS_CONTRACT.md` and `../common/templates/review.template.md` completely.

## Procedure

1. Name exactly one Gate or an explicit set of Gates. List required entry/exit criteria verbatim from the contract plus project additions.
2. Inspect current files and rerun cheap deterministic checks; do not rely on conversational claims.
3. For each criterion record `met/not_met/unknown/not_applicable`, evidence location, owner and remediation.
4. Count critical trace links and compute coverage. A missing critical link is a failure, not “mostly complete”.
5. Separate blocking findings, conditional items and observations. Re-run the gate after fixes; never edit the criteria to fit the result.

## Verdict algorithm

- Any violated criterion or critical trace gap → `FAIL`.
- Necessary evidence cannot be obtained → `BLOCKED`.
- No blockers, but bounded noncritical items have owner, deadline/condition and mitigation → `CONDITIONAL_PASS`.
- Every criterion is met with evidence → `PASS`.

Security/safety, destructive operation, data loss, public compatibility and uncontrolled hardware risks cannot be waived by the Agent. Skipped tests cannot be counted as passed.

## Required output

Produce a criterion table, evidence links, traceability percentage, blocking issues, conditional actions, unverified target behavior and an Agent **建议判决**. Record human approval/waiver separately; never impersonate it.

