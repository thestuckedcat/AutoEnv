import re
from typing import Dict


def render_script(template: str, variables: Dict[str, str]) -> str:
    rendered = template
    for k, v in variables.items():
        rendered = rendered.replace(f"${{{k}}}", v)

    unresolved = re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", rendered)
    if unresolved:
        raise ValueError(f"脚本存在未替换变量: {sorted(set(unresolved))}")
    return rendered
