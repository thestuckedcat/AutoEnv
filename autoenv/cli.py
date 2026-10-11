from __future__ import annotations

import argparse
import contextlib
import json
import os
import runpy
import sys
import traceback
from pathlib import Path
from typing import Sequence

from .errors import AutoEnvOperationError
from .script_files import describe_script, inspect_script, list_script_files, resolve_script
from .script_session import ExecutionSession, prompt_stream


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="autoenv", description="Run ordinary Python workflows with remote objects and file handlers.")
    children = parser.add_subparsers(dest="command")
    for mode in ("run", "rerun"):
        child = children.add_parser(mode)
        child.add_argument("script", nargs="?", help="Python file path or scripts/ stem")
        child.add_argument("--non-interactive", action="store_true")
        child.add_argument("--topology")
        child.add_argument("--bind", action="append", default=[], metavar="ROLE=CONNECTION")
        child.add_argument("--package", action="append", default=[], metavar="CONFIG=HDFS_PATH")
        child.add_argument("--arg", action="append", default=[], metavar="NAME=VALUE")
        child.add_argument("--request", type=Path)
        child.add_argument("--use-history", action="store_true")
        child.add_argument("--event-file", type=Path, help=argparse.SUPPRESS)
    listing = children.add_parser("list")
    listing.add_argument("--json", action="store_true")
    description = children.add_parser("describe")
    description.add_argument("script")
    description.add_argument("--json", action="store_true")
    return parser


def _pairs(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not separator or not key or key in result:
            raise ValueError(f"invalid or duplicate NAME=VALUE: {value}")
        result[key] = content
    return result


def _request(args) -> dict:
    if args.request:
        if args.topology or args.bind or args.package or args.arg or args.use_history:
            raise ValueError("--request is exclusive with topology/bind/package/arg/history flags")
        value = json.loads(args.request.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise TypeError("request must be an object")
        unknown = set(value) - {"schema_version", "script", "mode", "non_interactive", "history_policy", "selected_topology", "bindings", "packages", "arguments"}
        if unknown:
            raise ValueError(f"unknown request fields: {sorted(unknown)}")
        if value.get("schema_version", 2) != 2:
            raise ValueError("request schema_version must be 2")
        if args.script and value.get("script") != args.script:
            raise ValueError("request script conflicts with command line")
        if value.get("mode", args.command) != args.command:
            raise ValueError("request mode conflicts with command line")
        value.setdefault("mode", args.command)
        value.setdefault("non_interactive", True)
        value.setdefault("history_policy", "require" if args.command == "rerun" else "ignore")
        if args.non_interactive and value["non_interactive"] is not True:
            raise ValueError("request non_interactive conflicts with command line")
        return value
    return {
        "script": args.script, "mode": args.command or "run",
        "non_interactive": args.non_interactive,
        "history_policy": "require" if args.command == "rerun" else ("use" if args.use_history or not args.non_interactive else "ignore"),
        "selected_topology": args.topology,
        "bindings": _pairs(args.bind), "packages": _pairs(args.package), "arguments": _pairs(args.arg),
    }


@contextlib.contextmanager
def _capture(path: Path, session: ExecutionSession):
    """Capture Python, descriptor writes and inherited child output once."""
    saved = []
    prompt = None
    try:
        prompt = os.fdopen(os.dup(sys.__stderr__.fileno()), "w", encoding="utf-8", buffering=1)
        session.prompt_stream = prompt
    except (AttributeError, OSError, ValueError):
        session.prompt_stream = sys.__stderr__
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        try:
            for number, original in ((1, sys.stdout), (2, sys.stderr)):
                original.flush()
                try:
                    if original.fileno() != number:
                        continue
                    saved.append((number, os.dup(number)))
                    os.dup2(stream.fileno(), number)
                except (AttributeError, OSError, ValueError):
                    pass
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                yield
        finally:
            stream.flush()
            for number, duplicate in saved:
                os.dup2(duplicate, number)
                os.close(duplicate)
            if prompt:
                prompt.close()


def _run(session: ExecutionSession, descriptor: dict) -> object:
    script = Path(descriptor["path"])
    previous = list(sys.path)
    sys.path[:0] = [str(session.root_dir), str(script.parent)]
    try:
        if descriptor["entry"] == "main":
            namespace = runpy.run_path(str(script), run_name="__autoenv_workflow__")
            return namespace["main"]()
        runpy.run_path(str(script), run_name="__main__")
        return None
    finally:
        sys.path[:] = previous


def main(argv: Sequence[str] | None = None, *, root_dir: Path | None = None) -> int:
    values = list(argv) if argv is not None else sys.argv[1:]
    root = (root_dir or Path.cwd()).resolve()
    parser = build_parser()
    if values and (values[0] in {"list", "describe"} or "--help" in values or "-h" in values):
        try:
            args = parser.parse_args(values)
            data = list_script_files(root) if args.command == "list" else describe_script(root, args.script)
            if args.json or args.command == "describe":
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                for item in data:
                    print(f"{item['script']}: {item.get('description') or item.get('error', '')}")
            return 0
        except Exception as exc:
            print(f"AutoEnv: {exc}", file=sys.stderr)
            return 2
    event_file = None
    for index, value in enumerate(values):
        if value == "--event-file" and index + 1 < len(values):
            event_file = values[index + 1]
        elif value.startswith("--event-file="):
            event_file = value.partition("=")[2]
    try:
        session = ExecutionSession(root, event_file=event_file)
    except Exception as exc:
        print(f"Cannot create AutoEnv log: {exc}", file=sys.stderr)
        return 2
    exit_code, prepared = 2, False
    session.hosted = True
    try:
        with _capture(session.log_path, session), session.activate():
            try:
                if session.bootstrap_error:
                    raise session.bootstrap_error
                args = parser.parse_args(values or ["run"])
                if args.command is None:
                    args = parser.parse_args(["run", *values])
                request = _request(args)
                script = request.get("script")
                if script is None and not request.get("non_interactive"):
                    scripts = [item for item in list_script_files(root) if "error" not in item]
                    for i, item in enumerate(scripts, 1):
                        print(f"{i}. {item['script']}", file=prompt_stream())
                    print("脚本: ", end="", file=prompt_stream(), flush=True)
                    selected = input().strip()
                    script = scripts[int(selected) - 1]["script"] if selected.isdecimal() and 1 <= int(selected) <= len(scripts) else selected
                    request["script"] = script
                descriptor = inspect_script(resolve_script(root, script))
                session.prepare(descriptor, request)
                prepared = True
                returned = _run(session, descriptor)
                if not session._initialized:
                    raise ValueError("script did not call init_script()")
                if returned is None or isinstance(returned, str):
                    session.script_output = returned
                    if isinstance(returned, str):
                        session.recorder.log("SCRIPT OUTPUT " + returned, console=False)
                    success, status = True, "success"
                elif hasattr(returned, "success") and hasattr(returned, "status"):
                    session.select_final(returned)
                    success = bool(returned.success)
                    status = getattr(returned.status, "value", str(returned.status))
                else:
                    raise TypeError("main() must return None, str, or an AutoEnv result")
                unavailable = next((remote for remote in session.remotes if not remote.available), None)
                if success and unavailable:
                    success, status = False, "self_check_failed"
                    session.select_final(unavailable.self_check_result)
                exit_code = 0 if success else 1
                session.finish(success=success, status=status)
            except BaseException as exc:
                if isinstance(exc, SystemExit) and exc.code in (None, 0) and session._initialized:
                    unavailable = next((remote for remote in session.remotes if not remote.available), None)
                    if unavailable:
                        session.select_final(unavailable.self_check_result)
                    exit_code = 1 if unavailable else 0
                    session.finish(success=not unavailable, status="self_check_failed" if unavailable else "success")
                else:
                    exit_code = 1 if prepared else 2
                    status = "program_error" if prepared else "preparation_failed"
                    if isinstance(exc, AutoEnvOperationError):
                        status = getattr(exc.status, "value", str(exc.status))
                        session.select_final(exc.result)
                    session.recorder.log("EXCEPTION\n" + "".join(traceback.format_exception(exc)), console=False)
                    session.finish(success=False, status=status, error=exc)
    finally:
        session.close()
    print(str(session.log_path), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
