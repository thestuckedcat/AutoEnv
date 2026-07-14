from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .registry import list_scripts, run_script
from .runtime import RunMode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoenv",
        description="Register and run sequential remote environment scripts.",
    )
    subparsers = parser.add_subparsers(dest="command")
    for command in (RunMode.RUN.value, RunMode.RERUN.value):
        child = subparsers.add_parser(command)
        child.add_argument("script", nargs="?", help="registered script name")
    return parser


def main(argv: Sequence[str] | None = None, *, root_dir: Path | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = (root_dir or Path.cwd()).resolve()
    mode = RunMode(args.command or RunMode.RUN.value)
    try:
        script_name = getattr(args, "script", None) or _choose_script(root)
        if script_name is None:
            return 1
        result = run_script(script_name, mode=mode, root_dir=root)
    except Exception as exc:
        print(f"AutoEnv error: {exc}", file=sys.stderr)
        return 2
    if not result.success:
        print(
            f"AutoEnv script failed: {result.script_name} status={result.status} "
            f"error={result.error_message or '-'}",
            file=sys.stderr,
        )
        return 1
    print(f"AutoEnv script completed: {result.script_name} run_dir={result.run_dir}")
    return 0


def _choose_script(root_dir: Path) -> str | None:
    scripts = list_scripts(root_dir=root_dir)
    if not scripts:
        print("No registered AutoEnv scripts were found.", file=sys.stderr)
        return None
    print("Available AutoEnv scripts:")
    for index, script in enumerate(scripts, start=1):
        print(f"{index}. {script.name:<24} {script.description}")
    answer = input("Select a script: ").strip()
    try:
        selected = int(answer)
    except ValueError:
        print("Selection must be a number.", file=sys.stderr)
        return None
    if not 1 <= selected <= len(scripts):
        print("Selection is out of range.", file=sys.stderr)
        return None
    return scripts[selected - 1].name


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
