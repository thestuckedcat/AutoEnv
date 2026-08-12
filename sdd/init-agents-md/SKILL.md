---
name: init-agents-md
description: Create or repair a concise evidence-backed AGENTS.md for a software repository. Use when onboarding agents, documenting command quick references and directory structure, recording verified traps and lessons, or aligning repository instructions with an SDD workflow.
---

# Initialize AGENTS.md

Read `../common/PROCESS_CONTRACT.md` and `../common/templates/agents.template.md` completely.

## Workflow

1. Read existing agent instructions at every applicable directory level. Preserve stricter or user-authored rules.
2. Discover commands from build files, CI and successful local execution. Never guess a command or mark it safe without evidence.
3. Map only important directories/modules and ownership boundaries; link detailed design instead of duplicating it.
4. Search version history, issue notes, tests and confirmed retrospectives for traps. Separate verified lessons from current hypotheses.
5. Draft commands with prerequisites and side effects; mark real-device, flash, deploy, network and destructive operations as explicit-authorization actions.
6. Add routing to applicable project skills, validation ladder, generated/ignored files and delivery checklist.
7. Search for contradictions and run representative non-destructive commands before replacing an existing file.

## Hard constraints

Do not include credentials, host-specific secrets, transient task notes, unverifiable advice or enormous architecture prose. Retrospective lessons enter `AGENTS.md` only after user confirmation. A command that has not been run is labeled unverified.

## Required output

Report created/preserved sections, evidence for commands, unresolved conflicts, unsafe commands intentionally excluded, and an Agent **建议判决**. Human confirmation is required before weakening an existing safety rule.

