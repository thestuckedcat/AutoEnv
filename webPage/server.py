from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


WEB_DIR = Path(__file__).resolve().parent
ROOT_DIR = WEB_DIR.parent
ENV_DIR = ROOT_DIR / "environments"
UPLOAD_DIR = WEB_DIR / "uploads"
SETTINGS_PATH = WEB_DIR / "settings.json"

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


AUTOENV_SESSION = ProcessSession()
AGENT_SESSION = ProcessSession()


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


def _safe_name(value: object, label: str) -> str:
    name = str(value).strip()
    if not name or Path(name).name != name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
        raise ValueError(f"{label} may contain letters, digits, '_' and '-' only")
    return name


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
            if parsed.path == "/api/health":
                self.json({"ok": True, "root": str(ROOT_DIR)})
            elif parsed.path == "/api/environments":
                ENV_DIR.mkdir(parents=True, exist_ok=True)
                self.json({"environments": [_json_file(path, {}) for path in sorted(ENV_DIR.glob("*.json"))]})
            elif parsed.path == "/api/scripts":
                from autoenv.registry import list_scripts
                self.json({"scripts": [{"name": item.name, "description": item.description, "packages": list(item.packages), "parameters": list(item.parameters)} for item in list_scripts(root_dir=ROOT_DIR)]})
            elif parsed.path == "/api/tools":
                from autoenv.web_tools import describe_tools
                self.json({"tools": describe_tools(ROOT_DIR)})
            elif parsed.path in {"/api/run/events", "/api/agent/events"}:
                session = AUTOENV_SESSION if "/run/" in parsed.path else AGENT_SESSION
                cursor = int(parse_qs(parsed.query).get("cursor", ["0"])[0])
                with session.lock:
                    self.json({"events": session.events[cursor:], "next": len(session.events)})
            elif parsed.path == "/api/settings":
                self.json(_json_file(SETTINGS_PATH, {"upload_dir": str(UPLOAD_DIR), "agent_command": "codeagent"}))
            else:
                if parsed.path == "/":
                    self.path = "/index.html"
                super().do_GET()
        except Exception as exc:
            self.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        try:
            data = self.body()
            if self.path == "/api/environments":
                name = _safe_name(data.get("name"), "environment name")
                data["name"] = name
                _write_json(ENV_DIR / f"{name}.json", data)
                self.json({"ok": True})
            elif self.path == "/api/run/start":
                request_path = WEB_DIR / ".runtime" / f"request-{uuid.uuid4().hex}.json"
                _write_json(request_path, data)
                AUTOENV_SESSION.start([sys.executable, "-X", "utf8", str(ROOT_DIR / "adapt_interface.py"), "--request", str(request_path)], cwd=ROOT_DIR)
                self.json({"ok": True})
            elif self.path == "/api/run/stop":
                AUTOENV_SESSION.stop(); self.json({"ok": True})
            elif self.path == "/api/open":
                target = str(data.get("target", ""))
                if target == "run":
                    path = Path(AUTOENV_SESSION.last_run_dir or ROOT_DIR / "logs")
                elif target == "log":
                    path = Path(AUTOENV_SESSION.last_run_dir or ROOT_DIR / "logs") / "run.log"
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
            elif self.path == "/api/settings":
                upload_dir = Path(str(data.get("upload_dir", UPLOAD_DIR))).expanduser().resolve()
                command = str(data.get("agent_command", "codeagent")).strip()
                if not command: raise ValueError("agent command must not be empty")
                _write_json(SETTINGS_PATH, {"upload_dir": str(upload_dir), "agent_command": command})
                self.json({"ok": True})
            elif self.path == "/api/upload":
                settings = _json_file(SETTINGS_PATH, {"upload_dir": str(UPLOAD_DIR)})
                assert isinstance(settings, dict)
                folder = Path(str(settings.get("upload_dir", UPLOAD_DIR))).resolve()
                folder.mkdir(parents=True, exist_ok=True)
                name = Path(str(data.get("name", "upload.bin"))).name
                raw = str(data.get("data", "")); encoded = raw.split(",", 1)[-1]
                payload = base64.b64decode(encoded, validate=True)
                if len(payload) > 48 * 1024 * 1024: raise ValueError("file exceeds 48 MiB limit")
                target = folder / f"{uuid.uuid4().hex[:8]}-{name}"
                target.write_bytes(payload)
                self.json({"path": str(target)})
            elif self.path == "/api/agent/start":
                settings = _json_file(SETTINGS_PATH, {"agent_command": "codeagent"})
                assert isinstance(settings, dict)
                command = str(data.get("command") or settings.get("agent_command") or "codeagent")
                executable = shutil.which(command)
                if executable is None: raise FileNotFoundError(f"agent command not found: {command}")
                command_line = [executable]
                if Path(executable).suffix.lower() in {".cmd", ".bat"}:
                    command_line = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", executable]
                AGENT_SESSION.start(command_line, cwd=ROOT_DIR)
                self.json({"ok": True})
            elif self.path == "/api/agent/input":
                AGENT_SESSION.input(str(data.get("value", ""))); self.json({"ok": True})
            elif self.path == "/api/agent/stop":
                AGENT_SESSION.stop(); self.json({"ok": True})
            else:
                self.json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"AutoEnv Web: {url}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        AUTOENV_SESSION.stop(); AGENT_SESSION.stop(); server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
