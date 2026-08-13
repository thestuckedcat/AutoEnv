from __future__ import annotations

import argparse
import re
from pathlib import Path

TEMPLATE = '''from __future__ import annotations

from autoenv.web_tools import register_web_tool

@register_web_tool(
    name={name!r}, title={title!r}, description={description!r},
    fields=[{{"name": "value", "label": "输入", "type": "text", "required": True}}],
)
def run(values: dict[str, object]) -> object:
    value = str(values.get("value", "")).strip()
    if not value:
        raise ValueError("value must not be empty")
    return {{"value": value}}
'''

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("name"); parser.add_argument("--title", required=True); parser.add_argument("--description", default=""); args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.name):
        raise SystemExit("name must use lowercase letters, digits, '-' or '_'")
    target = Path.cwd().resolve() / "webPage" / "tools" / f"{args.name.replace('-', '_')}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists(): raise SystemExit(f"refusing to overwrite {target}")
    target.write_text(TEMPLATE.format(name=args.name, title=args.title, description=args.description), encoding="utf-8")
    print(target); return 0

if __name__ == "__main__": raise SystemExit(main())
