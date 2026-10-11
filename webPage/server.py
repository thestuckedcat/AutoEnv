from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse


WEB_DIR = Path(__file__).resolve().parent
ROOT_DIR = WEB_DIR.parent
UPLOAD_DIR = WEB_DIR / "uploads"
SETTINGS_PATH = WEB_DIR / "settings.json"
WEB_HOST = "127.0.0.1"
WEB_PORT = 8765
WEB_URL = f"http://{WEB_HOST}:{WEB_PORT}/"
_SCP_PROGRESS_RE = re.compile(
    # Require the recorder timestamp immediately before the marker.  A remote
    # command is allowed to print arbitrary text, so searching only for the
    # suffix could accidentally treat device stdout as a framework event.
    r"^\[[^\]\r\n]+\] SCP BATCH PROGRESS operation_id=(?P<operation_id>\S+) "
    r"completed=(?P<completed>\d+) total=(?P<total>\d+)$"
)

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class ProcessSession:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict[str, object]] = []
        self.process: subprocess.Popen[str] | None = None
        self.generation = 0
        self.last_run_dir: str | None = None

    def start(self, command: list[str], *, cwd: Path, event_prefix: str = "") -> None:
        self.stop()
        with self.lock:
            self.events = []
            self.generation += 1
            generation = self.generation
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1, creationflags=creationflags,
        )
        threading.Thread(target=self._read, args=(self.process, generation, event_prefix), daemon=True).start()

    def _read(self, process: subprocess.Popen[str], generation: int, prefix: str) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip("\r\n")
            if "run_dir:" in text:
                self.last_run_dir = text.split("run_dir:", 1)[1].strip()
            progress = _parse_scp_progress(text)
            if progress is not None:
                self.append(progress, generation)
            else:
                self.append({"type": "output", "text": f"{prefix}{text}"}, generation)
        code = process.wait()
        self.append({"type": "complete", "success": code == 0, "status": f"exit_{code}"}, generation)

    def append(self, event: dict[str, object], generation: int) -> None:
        with self.lock:
            if generation == self.generation:
                self.events.append(event)

    def input(self, value: str) -> None:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("process is not running")
        process.stdin.write(value + "\n")
        process.stdin.flush()

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


class ScriptProcessSession(ProcessSession):
    """Run the public CLI and stream only its concise operation output."""

    def __init__(self) -> None:
        super().__init__()
        self.last_log_path: str | None = None
        self.last_output_path: str | None = None

    def start_script(self, command: list[str], *, cwd: Path, event_file: Path) -> None:
        self.stop()
        with self.lock:
            self.events = []
            self.generation += 1
            generation = self.generation
            self.last_run_dir = self.last_log_path = self.last_output_path = None
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.process = process
        threading.Thread(
            target=self._monitor_script, args=(process, generation, event_file), daemon=True,
        ).start()

    def _monitor_script(
        self, process: subprocess.Popen[str], generation: int, event_file: Path,
    ) -> None:
        terminal: dict[str, str] = {}

        def collect() -> None:
            stdout, stderr = process.communicate()
            terminal.update(stdout=stdout, stderr=stderr)

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        offset = 0
        pending = b""
        output: Path | None = None
        while True:
            if output is None and event_file.is_file():
                try:
                    location = _json_file(event_file, {})
                    if not isinstance(location, dict):
                        raise ValueError("invalid CLI event location")
                    log = Path(str(location["log_path"])).resolve()
                    output = Path(str(location["output_path"])).resolve()
                    with self.lock:
                        if generation == self.generation:
                            self.last_log_path = str(log)
                            self.last_output_path = str(output)
                            self.last_run_dir = str(log.parent)
                    self.append({"type": "location", "log_path": str(log), "output_path": str(output)}, generation)
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    output = None
            if output is not None and output.is_file():
                with output.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
                lines = (pending + chunk).split(b"\n")
                pending = lines.pop()
                for line in lines:
                    self.append({"type": "output", "text": line.rstrip(b"\r").decode("utf-8", "replace")}, generation)
            if not reader.is_alive():
                break
            time.sleep(0.05)
        reader.join()
        # The child can finish between the previous read and its thread check.
        if output is not None and output.is_file():
            with output.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
            lines = (pending + chunk).split(b"\n")
            pending = lines.pop()
            for line in lines:
                self.append({"type": "output", "text": line.rstrip(b"\r").decode("utf-8", "replace")}, generation)
        if pending:
            self.append({"type": "output", "text": pending.decode("utf-8", "replace")}, generation)
        # A logger bootstrap failure is the only normal CLI stderr case.
        if terminal.get("stderr", "").strip():
            self.append({"type": "output", "text": terminal["stderr"].strip()}, generation)
        if generation == self.generation and self.last_log_path is None:
            candidate = terminal.get("stdout", "").strip()
            if candidate and Path(candidate).is_absolute() and Path(candidate).is_file():
                self.last_log_path = candidate
                self.last_run_dir = str(Path(candidate).parent)
        code = process.returncode
        self.append({
            "type": "complete", "success": code == 0, "exit_code": code,
            "status": f"exit_{code}", "log_path": self.last_log_path,
            "output_path": self.last_output_path,
        }, generation)
        event_file.unlink(missing_ok=True)


def _parse_scp_progress(text: str) -> dict[str, object] | None:
    """Convert recorder progress lines into Web events instead of log text."""

    match = _SCP_PROGRESS_RE.search(text)
    if match is None:
        return None
    completed = int(match.group("completed"))
    total = int(match.group("total"))
    if total <= 0 or completed < 0 or completed > total:
        return None
    return {
        "type": "progress",
        "operation_id": match.group("operation_id"),
        "completed": completed,
        "total": total,
    }


class TerminalSession:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict[str, object]] = []
        self.process: object | None = None
        self.generation = 0

    def start(self, command: list[str], *, cwd: Path, rows: int = 30, cols: int = 100) -> int:
        self.stop()
        try:
            from winpty import PtyProcess
        except ImportError as exc:
            raise RuntimeError(
                "Agent CLI requires pywinpty on Windows; reinstall AutoEnv dependencies"
            ) from exc
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        process = PtyProcess.spawn(
            command,
            cwd=str(cwd),
            env=env,
            dimensions=(max(2, rows), max(10, cols)),
        )
        with self.lock:
            self.events = []
            self.generation += 1
            generation = self.generation
            self.process = process
        threading.Thread(
            target=self._read, args=(process, generation), daemon=True
        ).start()
        return generation

    def _read(self, process: object, generation: int) -> None:
        try:
            while True:
                try:
                    value = process.read(4096)  # type: ignore[attr-defined]
                except EOFError:
                    break
                if value:
                    self.append({"type": "terminal", "data": value}, generation)
        finally:
            try:
                code = process.wait()  # type: ignore[attr-defined]
            except Exception:
                code = getattr(process, "exitstatus", None)
            self.append(
                {"type": "complete", "success": code == 0, "status": f"exit_{code}"},
                generation,
            )

    def append(self, event: dict[str, object], generation: int) -> None:
        with self.lock:
            if generation == self.generation:
                self.events.append(event)

    def input(self, value: str) -> None:
        process = self.process
        if process is None or not process.isalive():  # type: ignore[attr-defined]
            raise RuntimeError("terminal is not running")
        process.write(value)  # type: ignore[attr-defined]

    def resize(self, rows: int, cols: int) -> None:
        process = self.process
        if process is not None and process.isalive():  # type: ignore[attr-defined]
            process.setwinsize(max(2, rows), max(10, cols))  # type: ignore[attr-defined]

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.isalive():  # type: ignore[attr-defined]
            process.terminate(force=True)  # type: ignore[attr-defined]


def _start_agent_shell(
    session: TerminalSession,
    command: str,
    *,
    cwd: Path,
    rows: int,
    cols: int,
) -> int:
    shell = os.environ.get("COMSPEC", "cmd.exe")
    generation = session.start([shell, "/d"], cwd=cwd, rows=rows, cols=cols)
    if command:
        session.input(f"{command}\r")
    return generation


AUTOENV_SESSION = ScriptProcessSession()
TOOL_SESSION = ProcessSession()
AGENT_SESSION = TerminalSession()


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def _json_file(path: Path, default: object) -> object:
    if not path.is_file():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    with pending.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(pending, path)


def _load_settings() -> dict[str, object]:
    defaults: dict[str, object] = {
        "upload_dir": str(UPLOAD_DIR),
        "agent_command": "codeagent",
        "agent_cwd": str(ROOT_DIR),
    }
    stored = _json_file(SETTINGS_PATH, {})
    if isinstance(stored, dict):
        defaults.update(stored)
    return defaults


def _resolve_agent_cwd(value: object) -> Path:
    raw = os.path.expandvars(str(value or ROOT_DIR).strip())
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"agent startup directory does not exist: {path}")
    return path


def _describe_scripts() -> list[dict[str, object]]:
    from autoenv.script_files import describe_script, list_script_files

    scripts = []
    for item in list_script_files(ROOT_DIR):
        if "error" not in item:
            try:
                item = describe_script(ROOT_DIR, str(item["script"]))
            except (ValueError, TypeError, FileNotFoundError) as exc:
                item = {**item, "topologies": [], "error": str(exc)}
        scripts.append(item)
    return scripts


def _script_command(data: dict[str, object], event_file: Path) -> list[str]:
    """Translate a Web form into CLI arguments, never into a shell command."""

    script = data.get("script")
    if not isinstance(script, str) or not script.strip():
        raise ValueError("script is required")
    from autoenv.script_files import describe_script

    describe_script(ROOT_DIR, script)
    command = [sys.executable, "-X", "utf8", "-m", "autoenv.cli", "run", script, "--non-interactive"]
    topology = data.get("selected_topology", "")
    if not isinstance(topology, str):
        raise ValueError("selected_topology must be a string")
    if topology:
        command.extend(["--topology", topology])
    for section, flag in (("bindings", "--bind"), ("packages", "--package"), ("arguments", "--arg")):
        values = data.get(section, {})
        if not isinstance(values, dict):
            raise ValueError(f"{section} must be an object")
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{section} names must be nonempty strings")
            if isinstance(value, (list, dict)):
                # Complex arguments use the same canonical request accepted by agents.
                request = {"schema_version": 2, "mode": "run", "non_interactive": True, "history_policy": "ignore", **data}
                if not topology:
                    request["selected_topology"] = None
                request_path = event_file.with_name(event_file.stem + "-request.json")
                _write_json(request_path, request)
                return command[:6] + [script, "--non-interactive", "--request", str(request_path), "--event-file", str(event_file)]
            if value is None:
                raise ValueError(f"{section}.{key} cannot be null")
            text = str(value).lower() if isinstance(value, bool) else str(value)
            command.extend([flag, f"{key}={text}"])
    command.extend(["--event-file", str(event_file)])
    return command


def _display_command(command: list[str]) -> str:
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in command)
    import shlex

    return shlex.join(command)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def download_text(self, filename: str, value: str) -> None:
        payload = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header(
            "Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}"
        )
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64 * 1024 * 1024:
            raise ValueError("request is too large")
        value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            from product_log.web import handle_get
            if handle_get(self, parsed, ROOT_DIR):
                return
            if parsed.path == "/api/health":
                self.json({"ok": True, "root": str(ROOT_DIR)})
            elif parsed.path == "/api/environments":
                from autoenv.remote_catalog import read_catalog
                self.json({"topologies": read_catalog(ROOT_DIR)})
            elif parsed.path == "/api/resource-labels":
                from autoenv.resources import describe_resource_labels
                self.json({"resource_labels": describe_resource_labels()})
            elif parsed.path == "/api/scripts":
                self.json({"scripts": _describe_scripts()})
            elif parsed.path == "/api/tools":
                from autoenv.web_tools import describe_tools
                self.json({"tools": describe_tools(ROOT_DIR)})
            elif parsed.path == "/api/tools/workflow/events":
                cursor = int(parse_qs(parsed.query).get("cursor", ["0"])[0])
                with TOOL_SESSION.lock:
                    self.json({
                        "events": TOOL_SESSION.events[cursor:],
                        "next": len(TOOL_SESSION.events),
                        "generation": TOOL_SESSION.generation,
                    })
            elif parsed.path == "/api/log-batches":
                from autoenv.log_query import list_log_batches
                self.json({"batches": list_log_batches(ROOT_DIR)})
            elif parsed.path == "/api/log-batches/targets":
                from autoenv.log_query import list_log_targets
                query = parse_qs(parsed.query)
                self.json({"targets": list_log_targets(ROOT_DIR, query.get("batch", [""])[0])})
            elif parsed.path == "/api/log-batches/query":
                from autoenv.log_query import query_log_records
                query = parse_qs(parsed.query)
                offset_value = query.get("offset", [None])[0]
                self.json(query_log_records(
                    ROOT_DIR,
                    query.get("batch", [""])[0],
                    query.get("target", [""])[0],
                    page=int(query.get("page", ["1"])[0]),
                    limit=int(query.get("limit", ["200"])[0]),
                    query_date=query.get("date", [""])[0],
                    query_time=query.get("time", [""])[0],
                    window_minutes=int(query.get("window", ["60"])[0]),
                    keyword=query.get("keyword", [""])[0],
                    context_lines=int(query.get("context", ["0"])[0]),
                    slot_id=query.get("slot_id", [""])[0],
                    socket_id=query.get("socket_id", [""])[0],
                    offset=int(offset_value) if offset_value is not None else None,
                ))
            elif parsed.path == "/api/log-batches/correlate":
                from autoenv.log_query import correlate_log_records
                query = parse_qs(parsed.query)
                self.json(correlate_log_records(
                    ROOT_DIR,
                    query.get("batch", [""])[0],
                    query.get("target", [""])[0],
                    int(query.get("sequence", ["0"])[0]),
                    int(query.get("window_seconds", ["300"])[0]),
                ))
            elif parsed.path == "/api/log-batches/export":
                from autoenv.log_query import export_log_records
                query = parse_qs(parsed.query)
                filename, content = export_log_records(
                    ROOT_DIR,
                    query.get("batch", [""])[0],
                    query.get("target", [""])[0],
                    mode=query.get("mode", [""])[0],
                    slot_id=query.get("slot_id", [""])[0],
                    socket_id=query.get("socket_id", [""])[0],
                )
                self.download_text(filename, content)
            elif parsed.path in {"/api/run/events", "/api/agent/events"}:
                session = AUTOENV_SESSION if "/run/" in parsed.path else AGENT_SESSION
                cursor = int(parse_qs(parsed.query).get("cursor", ["0"])[0])
                with session.lock:
                    self.json({
                        "events": session.events[cursor:],
                        "next": len(session.events),
                        "generation": session.generation,
                    })
            elif parsed.path == "/api/settings":
                self.json(_load_settings())
            else:
                if parsed.path == "/":
                    self.path = "/index.html"
                super().do_GET()
        except Exception as exc:
            self.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        try:
            from product_log.web import handle_post
            if handle_post(self, ROOT_DIR):
                return
            data = self.body()
            if self.path == "/api/environments":
                from autoenv.remote_catalog import save_connection
                topology = data.get("topology")
                connection = data.get("connection")
                if not isinstance(topology, str) or not isinstance(connection, dict):
                    raise ValueError("topology and connection are required")
                save_connection(ROOT_DIR, topology, connection)
                self.json({"ok": True})
            elif self.path == "/api/run/start":
                event_file = WEB_DIR / ".runtime" / f"session-{uuid.uuid4().hex}.json"
                event_file.parent.mkdir(parents=True, exist_ok=True)
                command = _script_command(data, event_file)
                AUTOENV_SESSION.start_script(command, cwd=ROOT_DIR, event_file=event_file)
                self.json({"ok": True, "generation": AUTOENV_SESSION.generation, "command": _display_command(command)})
            elif self.path == "/api/run/command":
                event_file = WEB_DIR / ".runtime" / "session-preview.json"
                self.json({"command": _display_command(_script_command(data, event_file))})
            elif self.path == "/api/run/stop":
                AUTOENV_SESSION.stop(); self.json({"ok": True})
            elif self.path == "/api/open":
                target = str(data.get("target", ""))
                if target == "run":
                    path = Path(AUTOENV_SESSION.last_run_dir or ROOT_DIR / "logs")
                elif target == "log":
                    if AUTOENV_SESSION.last_log_path is None:
                        raise ValueError("no script log is available yet")
                    path = Path(AUTOENV_SESSION.last_log_path)
                else:
                    raise ValueError("target must be run or log")
                if not path.exists():
                    raise FileNotFoundError(path)
                os.startfile(str(path))  # type: ignore[attr-defined]
                self.json({"ok": True})
            elif self.path == "/api/tools/run":
                from autoenv.web_tools import run_web_tool
                name = str(data.get("name", "")); values = data.get("values", {})
                if not isinstance(values, dict): raise ValueError("values must be an object")
                self.json({"result": run_web_tool(ROOT_DIR, name, values)})
            elif self.path == "/api/tools/workflow/start":
                request_path = WEB_DIR / ".runtime" / f"tool-request-{uuid.uuid4().hex}.json"
                _write_json(request_path, data)
                TOOL_SESSION.start(
                    [sys.executable, "-X", "utf8", str(ROOT_DIR / "adapt_tool_interface.py"), "--request", str(request_path)],
                    cwd=ROOT_DIR,
                )
                self.json({"ok": True, "generation": TOOL_SESSION.generation})
            elif self.path == "/api/tools/workflow/stop":
                TOOL_SESSION.stop(); self.json({"ok": True})
            elif self.path == "/api/settings":
                upload_dir = Path(str(data.get("upload_dir", UPLOAD_DIR))).expanduser().resolve()
                command = str(data.get("agent_command") or "").strip()
                agent_cwd = _resolve_agent_cwd(data.get("agent_cwd", ROOT_DIR))
                _write_json(SETTINGS_PATH, {
                    "upload_dir": str(upload_dir),
                    "agent_command": command,
                    "agent_cwd": str(agent_cwd),
                })
                self.json({"ok": True})
            elif self.path == "/api/upload":
                settings = _load_settings()
                folder = Path(str(settings.get("upload_dir", UPLOAD_DIR))).resolve()
                folder.mkdir(parents=True, exist_ok=True)
                name = Path(str(data.get("name", "upload.bin"))).name
                raw = str(data.get("data", "")); encoded = raw.split(",", 1)[-1]
                payload = base64.b64decode(encoded, validate=True)
                if len(payload) > 48 * 1024 * 1024: raise ValueError("file exceeds 48 MiB limit")
                target = folder / f"{uuid.uuid4().hex[:8]}-{name}"
                target.write_bytes(payload)
                self.json({"path": str(target)})
            elif self.path == "/api/log-rules/preview":
                from autoenv.log_collection_rules import preview_log_sample
                name = Path(str(data.get("name", "sample.log"))).name
                raw = str(data.get("data", ""))
                payload = base64.b64decode(raw.split(",", 1)[-1], validate=True)
                self.json(preview_log_sample(payload, name))
            elif self.path == "/api/agent/start":
                settings = _load_settings()
                requested_command = data.get("command", settings.get("agent_command", "codeagent"))
                command = str(requested_command or "").strip()
                rows = int(data.get("rows", 30)); cols = int(data.get("cols", 100))
                cwd = _resolve_agent_cwd(data.get("cwd") or settings.get("agent_cwd"))
                generation = _start_agent_shell(
                    AGENT_SESSION, command, cwd=cwd, rows=rows, cols=cols
                )
                self.json({"ok": True, "generation": generation})
            elif self.path == "/api/agent/input":
                AGENT_SESSION.input(str(data.get("value", ""))); self.json({"ok": True})
            elif self.path == "/api/agent/resize":
                AGENT_SESSION.resize(int(data.get("rows", 30)), int(data.get("cols", 100)))
                self.json({"ok": True})
            elif self.path == "/api/agent/stop":
                AGENT_SESSION.stop(); self.json({"ok": True})
            else:
                self.json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main() -> int:
    try:
        server = ExclusiveThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler)
    except OSError as exc:
        print(
            f"AutoEnv Web cannot listen on its fixed endpoint {WEB_URL}; "
            f"stop the process already using it and retry ({exc})",
            file=sys.stderr,
        )
        return 2
    print(f"AutoEnv Web: {WEB_URL}")
    threading.Timer(0.5, lambda: webbrowser.open(WEB_URL)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        AUTOENV_SESSION.stop(); TOOL_SESSION.stop(); AGENT_SESSION.stop(); server.server_close()
    return 0
