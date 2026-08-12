---
name: analyze-existing-system
description: Analyze an existing low-level software repository and build trustworthy top-level design, detailed module designs, and feature specifications from code plus human domain knowledge. Use for legacy takeover, missing or stale design/spec documents, architecture discovery, or module-by-module reverse engineering.
---

# Analyze Existing System

Read `../common/PROCESS_CONTRACT.md` completely. Use `project-design.template.md`, `module-design.template.md`, and `feature-spec.template.md` from `../common/templates/`.

## Phase A: inventory before interpretation

Record baseline commit, build/test commands, languages, generated/vendor code, entry points, dependency boundaries, hardware targets and current docs. Search symbols and call sites. Create `docs/sdd/manifest.md` from `../common/templates/manifest.template.md` with every candidate module and explicit code ranges. Label every statement `FACT`, `USER_PRIOR`, `INFERENCE`, or `UNKNOWN`.

Hard stop: do not write detailed behavior until G1 confirms the inventory covers all in-scope code. File names alone are not module boundaries.

## Phase B: top-level design draft

Create only an outline-level `docs/sdd/design.md`: project functions, system context, layers, number of submodules, each module's responsibility/code range/interface, cross-cutting constraints and unresolved boundaries. Do not invent internals. Run G2 as `in_review`, not approved.

## Phase C: module-by-module deep design

For one module at a time:

1. Ask the user for that module's prior knowledge, known quirks, hardware assumptions and expected behavior. Record it as `USER_PRIOR`, not fact.
2. Trace entry points, state, control/data flow, error paths, dependencies, concurrency, timing, resources, recovery, compatibility and observability to code evidence.
3. Write `docs/sdd/modules/<module>/design.md` with file/symbol mapping.
4. Present unknowns and contradictions for human correction. Record corrections and re-check code.
5. If the module cannot be reviewed coherently, split it recursively in the manifest before continuing.

Hard stop: a module cannot become `approved` without explicit human inspection/correction. Apply confirmed corrections to the draft method only through the retrospective workflow; do not mutate skills mid-analysis.

## Phase D: feature specifications

Generate specs one function/feature at a time as `docs/sdd/specs/<feature>/spec.md`. Derive atomic requirements jointly from approved user intent, module design and code. Give each requirement verification method and acceptance threshold. While writing, reverse-update module design when the spec exposes missing states, interfaces or contradictions; log each update.

Hard stop: do not treat accidental legacy behavior as required without human confirmation. G3 fails if critical NEED→REQ→design→code→TEST→EVID links are missing.

## Phase E: close the hierarchy

Revisit the top-level `design.md` using all module designs and specs. Reconcile module count, interfaces, layering, lifecycle and cross-cutting constraints. Record superseded assumptions and run architecture review plus G2/G3 again.

## Required output

Provide artifact index, module completion table, evidence/unknown counts, human confirmations still required, traceability coverage, contradictions found, and an Agent **建议判决**. Never claim the recovered documents represent intended behavior where only code evidence exists.
