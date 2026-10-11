"""CLI entry point. The Web server continues to use startWeb.py exclusively."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .workflow import run_analysis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and index local user/kernel driver and firmware product logs")
    parser.add_argument("input", help="Local directory or supported archive")
    parser.add_argument("--output", type=Path, help="New isolated batch directory")
    parser.add_argument("--directory", default="", help="Unique partial directory path, e.g. diag/slot62")
    parser.add_argument("--collection-mode", choices=("auto", "product", "generic"), default="auto",
                        help="Auto detects diag/var; explicit --directory preserves generic selection")
    parser.add_argument("--diag-directory", default="diag", help="Unique partial diag directory path")
    parser.add_argument("--var-directory", default="var", help="Unique partial var directory path")
    parser.add_argument("--archive-pattern", action="append", help="Basename keyword/glob; repeat for multiple patterns")
    parser.add_argument("--file-pattern", action="append", help="Generic file or product diag glob; product var always selects dmesg rotations")
    parser.add_argument("--copy-pattern", action="append", help="Copy matching sources into the task workspace")
    parser.add_argument("--rename", action="append", default=[], metavar="OLD=NEW", help="Rename a selected task-local copy")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-recursive", action="store_true")
    args = parser.parse_args(argv)
    renames = {}
    for value in args.rename:
        old, separator, new = value.partition("=")
        if not separator or not old or not new:
            parser.error("--rename must be OLD=NEW")
        if old in renames:
            parser.error("duplicate --rename source")
        renames[old] = new
    output = args.output or (Path.cwd() / "runtime_path" / ("product-log-" + uuid.uuid4().hex))
    try:
        manifest = run_analysis(args.input, output, directory_pattern=args.directory,
                                collection_mode=args.collection_mode,
                                diag_directory_pattern=args.diag_directory,
                                var_directory_pattern=args.var_directory,
                                archive_patterns=args.archive_pattern, file_patterns=args.file_pattern,
                                copy_patterns=args.copy_pattern, rename_map=renames,
                                recursive=not args.no_recursive, workers=args.workers,
                                progress=lambda event: print(json.dumps(event, ensure_ascii=False), file=sys.stderr))
    except Exception as error:
        # Archive libraries have different exception families. All failed local
        # jobs use the same machine-readable CLI exit code and a concise cause.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
