from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "sdd" / "common" / "scripts" / "validate_sdd.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_sdd", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sdd_skill_pack_satisfies_hard_contract() -> None:
    validator = _load_validator()

    assert validator.validate(ROOT / "sdd") == []


def test_retrospective_requires_confirmation_before_writeback() -> None:
    text = (ROOT / "sdd" / "conduct-sdd-retrospective" / "SKILL.md").read_text(encoding="utf-8")

    barrier = text.index("## Confirmation barrier")
    confirmation = text.index("explicit confirmation", barrier)
    prohibition = text.index("do not edit any SKILL.md", barrier)
    assert confirmation < prohibition

