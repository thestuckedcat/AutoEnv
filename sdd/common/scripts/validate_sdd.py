from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_SKILLS = {
    "sdd-process",
    "analyze-existing-system",
    "init-agents-md",
    "develop-incremental-requirement",
    "review-architecture",
    "run-sdd-checkpoint",
    "self-review-and-verdict",
    "conduct-sdd-retrospective",
}
REQUIRED_CONTRACT_TERMS = {
    "FACT",
    "USER_PRIOR",
    "INFERENCE",
    "UNKNOWN",
    "CONDITIONAL_PASS",
    "目标机",
    "用户确认",
}


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    contract = root / "common" / "PROCESS_CONTRACT.md"
    if not contract.is_file():
        return [f"missing process contract: {contract}"]
    contract_text = contract.read_text(encoding="utf-8")
    for term in REQUIRED_CONTRACT_TERMS:
        if term not in contract_text:
            errors.append(f"process contract is missing hard term: {term}")

    for name in sorted(REQUIRED_SKILLS):
        path = root / name / "SKILL.md"
        if not path.is_file():
            errors.append(f"missing skill: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: (.+)\n---\n", text)
        if not match:
            errors.append(f"invalid frontmatter: {path}")
        elif match.group(1) != name:
            errors.append(f"skill name mismatch: {path}")
        if "../common/PROCESS_CONTRACT.md" not in text:
            errors.append(f"skill does not load common contract: {path}")
        if "建议判决" not in text:
            errors.append(f"skill lacks explicit verdict output: {path}")
        if "TODO" in text:
            errors.append(f"unfinished TODO in skill: {path}")

    templates = root / "common" / "templates"
    if len(list(templates.glob("*.template.md"))) < 10:
        errors.append("expected at least ten reusable document templates")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the reusable SDD skill pack")
    parser.add_argument("--skills-root", type=Path, default=Path("sdd"))
    args = parser.parse_args()
    errors = validate(args.skills_root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"SDD skill pack valid: {args.skills_root.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
