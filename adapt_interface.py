from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autoenv.interface import LaunchRequest, launch, result_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start AutoEnv without interactive input")
    parser.add_argument("--request", type=Path, help="JSON launch request")
    parser.add_argument("--script")
    parser.add_argument("--mode", choices=("run", "rerun"), default="run")
    parser.add_argument("--environment")
    parser.add_argument("--parameters", help="JSON object with connection/package/script arguments")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parent
    if args.request:
        with args.request.open("r", encoding="utf-8") as handle:
            request = LaunchRequest.from_dict(json.load(handle))
    else:
        parameters = json.loads(args.parameters or "{}")
        request = LaunchRequest.from_dict({
            "script": args.script, "mode": args.mode,
            "environment": args.environment, "parameters": parameters,
        })
    result = launch(request, root_dir=root)
    print(result_json(result))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
