---
name: conduct-sdd-retrospective
description: Turn SDD development conversations, artifacts, review findings, and verification evidence into controlled process-improvement proposals. Use after delivery, rework, failure, drift, or when improving SDD skills and templates; user confirmation is mandatory before any process write-back.
---

# Conduct SDD Retrospective

Read `../common/PROCESS_CONTRACT.md` and `../common/templates/retrospective-proposal.template.md` completely.

## Analyze

1. Establish the planned gates, actual timeline, artifacts, decisions, rework and final evidence. Quote or link evidence; do not rely on vague memory.
2. Separate outcome problems from process causes. Use concrete causal chains and consider alternative causes.
3. Identify rules that prevented drift, rules that failed, missing checkpoints, excessive overhead and project-specific exceptions.
4. Propose the smallest general rule/template/validator change that would detect or prevent recurrence. Include exact target, wording/logic, expected benefit, regression risk and a forward test.
5. Explicitly reject lessons that are only AutoEnv- or one-hardware-specific unless generalized with evidence.

## Confirmation barrier

Write only a proposal with status `awaiting_user_confirmation`. Ask the user to accept, modify or reject proposal IDs. Before explicit confirmation, **do not edit any SKILL.md, template, PROCESS_CONTRACT.md, validator or confirmed AGENTS.md lesson**. After confirmation, apply only accepted IDs, run skill and SDD validators, and record the write-back diff in the proposal.

## Required output

Provide evidence-linked successes/failures, candidate changes, rejected special cases, forward tests and an Agent **建议判决** on whether each proposal is ready for user confirmation. Absence of confirmation means `BLOCKED` for write-back, not permission to assume approval.

