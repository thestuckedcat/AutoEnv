---
name: review-architecture
description: Perform an independent architecture review of low-level software designs or changes. Use for module boundaries, cross-layer dependencies, interfaces, concurrency, timing, resources, recovery, security, hardware compatibility, or before approving a design baseline.
---

# Review Architecture

Read `../common/PROCESS_CONTRACT.md` and `../common/templates/review.template.md` completely. Review the stated baseline, not an unstated ideal system.

## Review method

1. Establish scope, stakeholders/concerns, quality attributes, assumptions and evidence.
2. Check whether views cover context, decomposition, runtime/control flow, data/state, deployment/hardware and failure/recovery.
3. Challenge module cohesion/coupling, dependency direction, interface ownership, versioning and forbidden cycles.
4. Walk at least these scenarios: nominal startup/use, invalid input, timeout/partial failure, concurrent event or interrupt, resource exhaustion, reset/power loss, upgrade/rollback and diagnosis.
5. Apply every low-level concern in the common contract; record justified N/A decisions.
6. Trace significant choices to ADR/RISK/REQ and tests. Compare at least one credible alternative for irreversible or high-cost decisions.

## Blocking rules

Missing ownership at an interface, unbounded critical resource/timing behavior, undefined recovery from partial state, unsolved high-severity security/safety risk, or a design contradicted by code/spec is blocking. Style preferences are not blocking.

## Required output

Use evidence-linked findings with severity, affected requirement, required action and retest. Include scenario coverage, unresolved assumptions and an Agent **建议判决**. Only a human can accept architecture risk or approve the baseline.

