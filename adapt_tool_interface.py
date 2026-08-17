from __future__ import annotations

import argparse
import json
from pathlib import Path

from autoenv.interface import bind_environments, merge_parameters
from autoenv.results import result_to_dict
from autoenv.web_tools import get_web_tool, run_workflow_tool


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request_path = Path(args.request).resolve()
    root = Path(__file__).resolve().parent
    value = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("tool request must be an object")
    name = str(value.get("tool", "")).strip()
    definition = get_web_tool(root, name)
    bindings = value.get("environments", {})
    parameters = value.get("parameters", {})
    if not isinstance(bindings, dict) or not isinstance(parameters, dict):
        raise ValueError("tool environments and parameters must be objects")
    bound = bind_environments(root, bindings, definition.resources)
    merged = merge_parameters(bound, parameters)
    result = run_workflow_tool(root, name, parameters=merged)
    print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
