import re
from typing import Iterable, Tuple


def render_script(template: str, mappings: Iterable[Tuple[str, str, str]]) -> str:
    """按三元组 (var_name, spec_name, real_name) 渲染脚本模板。"""
    rendered = template
    for var_name, _spec_name, real_name in mappings:
        rendered = rendered.replace(f"${{{var_name}}}", real_name)

    unresolved = re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", rendered)
    if unresolved:
        raise ValueError(f"脚本存在未替换变量: {sorted(set(unresolved))}")
    return rendered
