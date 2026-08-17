from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path


SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _safe_name(value: str, label: str) -> str:
    normalized = value.strip()
    if not SAFE_NAME.fullmatch(normalized):
        raise ValueError(
            f"{label} must use lowercase letters, digits, '-' or '_' and be at most 64 characters"
        )
    return normalized


def create_issue(
    knowledge_root: Path,
    *,
    component: str,
    slug: str,
    title: str,
    status: str,
    issue_date: str,
) -> Path:
    component = _safe_name(component, "component")
    slug = _safe_name(slug, "slug")
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > 120:
        raise ValueError("title must contain 1 to 120 characters")
    if status not in {"confirmed", "provisional", "rejected"}:
        raise ValueError("status must be confirmed, provisional or rejected")
    normalized_date = date.fromisoformat(issue_date).isoformat()
    root = knowledge_root.expanduser().resolve()
    component_dir = root / "components" / component
    issues_dir = component_dir / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)
    descriptor = component_dir / "component.yaml"
    if not descriptor.exists():
        descriptor.write_text(
            "\n".join(
                (
                    "schema_version: 1",
                    f"name: {component}",
                    "aliases: []",
                    "code_roots: []",
                    "log_markers:",
                    '  - "[ERROR]"',
                    "owners: []",
                    "dependencies: []",
                    'notes: ""',
                    "",
                )
            ),
            encoding="utf-8",
        )
    target = issues_dir / f"{normalized_date}-{slug}.md"
    if target.exists():
        raise FileExistsError(f"refusing to overwrite {target}")
    quoted_title = json.dumps(normalized_title, ensure_ascii=False)
    target.write_text(
        f"""---
schema_version: 1
component: {component}
date: {normalized_date}
status: {status}
title: {quoted_title}
signatures: []
error_codes: []
code_versions: []
environments: []
related_issues: []
---

# {normalized_title}

## 可复用症状与签名

- TODO：保留稳定错误文本、错误码、首个有效栈帧；删除时间戳、动态 ID 和敏感数据。

## 已确认的证据

- TODO：日志路径/行、代码路径/行、复现或运行期证据。

## 定位与根因

- 定位：TODO
- 根因：TODO
- 置信度：TODO（high / medium / low）

## 定界

- 责任组件：TODO
- 分类：TODO（internal / upstream / downstream / environment / cross-component）
- 触发方、失败方、报告/受害方：TODO

## 排除项与适用范围

- TODO：版本、配置、环境前提，以及已经排除的相似问题。

## 修复、规避与验证

- 修复或规避：TODO
- 回归检查：TODO
""",
        encoding="utf-8",
    )
    return target


def main() -> int:
    repository_root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(
        description="Create a component knowledge issue without overwriting existing conclusions."
    )
    parser.add_argument("--knowledge-root", type=Path, default=repository_root / "logKnowledge")
    parser.add_argument("--component", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--status", required=True, choices=("confirmed", "provisional", "rejected"))
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    try:
        target = create_issue(
            args.knowledge_root,
            component=args.component,
            slug=args.slug,
            title=args.title,
            status=args.status,
            issue_date=args.date,
        )
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
