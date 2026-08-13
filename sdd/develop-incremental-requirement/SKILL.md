---
name: develop-incremental-requirement
description: Turn an incremental requirement into a traced low-level software design, implementation, tests, and synchronized documentation. Use for features, bug fixes, interface changes, hardware adaptations, or refactors against existing design.md and spec.md baselines.
---

# Develop Incremental Requirement

Read `../common/PROCESS_CONTRACT.md` and `../common/templates/change-plan.template.md` completely. Read all affected top-level/module designs and feature specs before changing code.

## Define and baseline

1. Assign CHANGE/NEED/REQ IDs. Separate requested behavior, constraints, acceptance thresholds and non-scope.
2. Compare the request with existing design/spec/code/tests. Record contradictions instead of choosing silently.
3. Build an impact map covering interfaces, callers, persistence, concurrency, timing/resources, recovery, security, deployment, docs and target hardware.
4. Present solution options and tradeoffs. Obtain human confirmation for material scope, compatibility or architecture choices.

Hard stop: implementation cannot start until G3 and G4 have no blocking unknowns, acceptance criteria are testable, and rollback/migration is defined where state or hardware can be changed.

## Implement in traced slices

For each REQ: add/adjust a failing or characterizing test where practical; implement the smallest coherent slice; run focused checks; record code and TEST links. Never weaken an assertion merely to get green. Keep unverified target behavior explicit.

## Verify and reverse-update

Run the validation ladder from static checks through target/HIL tests that are available and authorized. Update module design for actual interfaces/state/error paths, feature spec for confirmed requirements, top-level design for cross-module impact, user/operator docs, examples, test notes and AGENTS traps. Do not rewrite approved intent to hide implementation divergence.

Process friction or a better workflow goes to a retrospective proposal; never directly self-modify this skill during delivery.

## Required output

Provide changed REQ trace rows, actual command results, target versus simulated evidence, documentation updates, rollback status, remaining risks and an Agent **建议判决**. G6 cannot PASS with stale public docs or missing critical evidence.

