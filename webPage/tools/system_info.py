from __future__ import annotations

from autoenv.web_tools import register_web_tool


@register_web_tool(
    name="tool-contract-preview",
    title="工具契约预览",
    description="验证动态表单、结构化输入和输出渲染；后续错误码解析器可沿用同一契约。",
    fields=[
        {"name": "value", "label": "示例输入", "type": "text", "required": True,
         "placeholder": "例如：0x12345678"},
        {"name": "notes", "label": "备注", "type": "textarea", "required": False},
    ],
)
def preview(values: dict[str, object]) -> object:
    return {
        "status": "contract_ready",
        "input": values.get("value", ""),
        "message": "错误码位段规则尚未提供；请新增独立工具实现，不修改 Web 核心。",
    }
