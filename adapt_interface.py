"""Thin command adapter; Web and Agent use the public autoenv CLI directly."""
from __future__ import annotations

import sys
from pathlib import Path

from autoenv.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] not in {"run", "rerun", "list", "describe", "-h", "--help"}:
        values.insert(0, "run")
    return cli_main(values, root_dir=Path(__file__).resolve().parent)


if __name__ == "__main__":
    raise SystemExit(main())
