from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / ".agents" / "skills" / "log-error-triage"
EXTRACT_SCRIPT = SKILL_ROOT / "scripts" / "extract_error_context.py"
KNOWLEDGE_SCRIPT = SKILL_ROOT / "scripts" / "new_knowledge_issue.py"


def _run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", *(str(value) for value in args)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_extract_error_context_scans_plain_and_gzip_logs_with_context(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "auth.log").write_text(
        "auth begin\n2026-08-17 08:21:00 [ERROR] token rejected\nauth end\n",
        encoding="utf-8",
    )
    with gzip.open(logs / "database.log.gz", "wt", encoding="utf-8") as handle:
        handle.write("db begin\n2026-08-17 08:21:02 [ERROR] query failed\ndb end\n")
    hidden = logs / ".cache"
    hidden.mkdir()
    (hidden / "ignored.log").write_text("[ERROR] ignore me\n", encoding="utf-8")

    completed = _run(EXTRACT_SCRIPT, logs, "--context", 1)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["files_scanned"] == 2
    assert result["match_count"] == 2
    assert [match["text"] for match in result["matches"]] == [
        "2026-08-17 08:21:00 [ERROR] token rejected",
        "2026-08-17 08:21:02 [ERROR] query failed",
    ]
    assert result["matches"][0]["line"] == 2
    assert result["matches"][0]["before"] == [{"line": 1, "text": "auth begin"}]
    assert result["matches"][0]["after"] == [{"line": 3, "text": "auth end"}]


def test_extract_error_context_is_case_sensitive_unless_requested(tmp_path: Path) -> None:
    log = tmp_path / "service.log"
    log.write_text("[error] lower case\n", encoding="utf-8")

    exact = _run(EXTRACT_SCRIPT, log)
    insensitive = _run(EXTRACT_SCRIPT, log, "--ignore-case")

    assert exact.returncode == 0
    assert json.loads(exact.stdout)["match_count"] == 0
    assert insensitive.returncode == 0
    assert json.loads(insensitive.stdout)["match_count"] == 1


def test_new_knowledge_issue_creates_scaffold_and_refuses_overwrite(tmp_path: Path) -> None:
    knowledge_root = tmp_path / "knowledge"
    args = (
        KNOWLEDGE_SCRIPT,
        "--knowledge-root",
        knowledge_root,
        "--component",
        "auth-service",
        "--slug",
        "invalid-token",
        "--title",
        "Token 校验失败",
        "--status",
        "confirmed",
        "--date",
        "2026-08-17",
    )

    created = _run(*args)

    assert created.returncode == 0, created.stderr
    component = knowledge_root / "components" / "auth-service"
    descriptor = (component / "component.yaml").read_text(encoding="utf-8")
    issue_path = component / "issues" / "2026-08-17-invalid-token.md"
    issue = issue_path.read_text(encoding="utf-8")
    assert "name: auth-service" in descriptor
    assert "status: confirmed" in issue
    assert 'title: "Token 校验失败"' in issue
    assert "## 定界" in issue

    duplicate = _run(*args)

    assert duplicate.returncode != 0
    assert "refusing to overwrite" in duplicate.stderr


def test_new_knowledge_issue_rejects_unsafe_component_names(tmp_path: Path) -> None:
    completed = _run(
        KNOWLEDGE_SCRIPT,
        "--knowledge-root",
        tmp_path,
        "--component",
        "../outside",
        "--slug",
        "bad",
        "--title",
        "bad",
        "--status",
        "provisional",
    )

    assert completed.returncode != 0
    assert "component must use lowercase" in completed.stderr
    assert not (tmp_path.parent / "outside").exists()


def test_skill_and_human_knowledge_contract_are_present() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    knowledge = (REPOSITORY_ROOT / "logKnowledge" / "README.md").read_text(encoding="utf-8")

    assert "[ERROR]" in skill
    assert "Only write knowledge after the user explicitly" in skill
    assert "触发方" in skill and "报告/受害方" in skill
    assert "只有用户或评审者明确确认后" in knowledge
    assert (REPOSITORY_ROOT / "logKnowledge/components/_template/component.yaml").is_file()
