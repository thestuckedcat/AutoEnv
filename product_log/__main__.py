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
    parser.add_argument("--config", type=Path, help="Explicit source/parser/rule configuration JSON")
    parser.add_argument("--directory", default="", help="Unique partial directory path, e.g. diag/slot62")
    parser.add_argument("--collection-mode", choices=("auto", "product", "generic"), default="auto",
                        help="Auto detects diag/varlog; explicit --directory preserves generic selection")
    parser.add_argument("--diag-directory", default="diag", help="Initial diag source directory override when --config is omitted")
    parser.add_argument("--var-directory", default="varlog", help="Initial kernel source directory override when --config is omitted")
    parser.add_argument("--archive-pattern", action="append", help="Legacy request field; all supported archives are now prepared before selection")
    parser.add_argument("--file-pattern", action="append", help="Initial diag/generic filename pattern override when --config is omitted")
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
        analysis_config = None
        if args.config is not None:
            analysis_config = json.loads(args.config.read_text(encoding="utf-8-sig"))
            if not isinstance(analysis_config, dict):
                raise ValueError("Analysis configuration must be an object")
        manifest = run_analysis(args.input, output, directory_pattern=args.directory,
                                analysis_config=analysis_config,
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
