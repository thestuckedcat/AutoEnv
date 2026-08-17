"""Local Web Tool template.

Copy this file to ``webPage/tools/<tool_name>.py`` and replace every
``replace-*`` value. Files beginning with ``_`` are deliberately skipped by
automatic discovery, so this template never appears in the Tools page.

For a resource-bound workflow Tool, use the repository scaffold instead:

    python -X utf8 .agents/skills/autoenv-web-tool/scripts/scaffold_tool.py \
      sample-workflow --title "Sample workflow" --kind workflow
"""

from __future__ import annotations

from autoenv.web_tools import register_web_tool


@register_web_tool(
    name="replace-with-tool-name",
    title="替换为工具标题",
    description="替换为简短、面向用户的功能说明。",
    fields=[
        {
            "name": "value",
            "label": "输入",
            "type": "text",
            "required": True,
            "placeholder": "请输入待处理内容",
        },
        {
            "name": "mode",
            "label": "模式",
            "type": "select",
            "required": True,
            "options": ["summary", "detail"],
            "default": "summary",
        },
        {
            "name": "include_metadata",
            "label": "包含元数据",
            "type": "checkbox",
            "required": False,
            "default": False,
        },
    ],
    kind="local",
    renderer="json",
)
def run(values: dict[str, object]) -> object:
    """Validate page values and return JSON-serializable output."""
    value = str(values.get("value", "")).strip()
    if not value:
        raise ValueError("value must not be empty")
    mode = str(values.get("mode", "summary")).strip()
    if mode not in {"summary", "detail"}:
        raise ValueError("mode must be summary or detail")
    return {
        "value": value,
        "mode": mode,
        "include_metadata": bool(values.get("include_metadata", False)),
    }
