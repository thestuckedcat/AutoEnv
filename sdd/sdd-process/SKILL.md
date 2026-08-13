---
name: sdd-process
description: Orchestrate an evidence-driven specification-driven development lifecycle for low-level software. Use for a full legacy baseline, new feature, risky refactor, or release that needs design, specifications, implementation, verification, review gates, self-verdict, and retrospective.
---

# SDD Process

Read `../common/PROCESS_CONTRACT.md` completely before acting. Use the templates in `../common/templates/`; never substitute project folklore for evidence.

## Route the work

1. Read repository instructions and detect existing `docs/sdd` artifacts.
2. For legacy code without trustworthy baselines, invoke the workflow in `../analyze-existing-system/SKILL.md`.
3. If repository operating instructions are absent or stale, use `../init-agents-md/SKILL.md`.
4. For a new or changed requirement, use `../develop-incremental-requirement/SKILL.md`.
5. Run `../review-architecture/SKILL.md` for cross-module, interface, lifecycle, resource, security, or hardware-impacting changes.
6. At every G0-G7 boundary, apply `../run-sdd-checkpoint/SKILL.md`. Do not silently skip a gate.
7. Before delivery, apply `../self-review-and-verdict/SKILL.md` using fresh evidence.
8. After delivery or a failed attempt, use `../conduct-sdd-retrospective/SKILL.md`. Wait for user confirmation before applying process changes.

## Control loop

At each stage: collect evidence → update only draft artifacts → run the gate → close blocking findings → request human confirmation where the contract requires it. If a prior design/spec is contradicted by code, record an issue; do not quietly redefine the baseline.

## Required output

Maintain the recommended layout from `../README.md`. End with scope, artifact links, gate table G0-G7, traceability coverage, commands and actual results, unverified target behavior, risks, decisions awaiting a human, and an Agent **建议判决** of `PASS`, `CONDITIONAL_PASS`, `FAIL`, or `BLOCKED`.

