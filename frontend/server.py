from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


FRONTEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = FRONTEND_DIR.parent
HOST = "127.0.0.1"
PORT = 8765


class Session:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict[str, object]] = []
        self.process: subprocess.Popen[str] | None = None
        self.generation = 0

    def append(self, event: dict[str, object], generation: int | None = None) -> None:
        with self.lock:
            if generation is not None and generation != self.generation:
                return
            self.events.append(event)

    def start(self, script: str, mode: str) -> None:
        self.stop()
        with self.lock:
            self.events = []
            self.generation += 1
            generation = self.generation
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", mode, script]
        process = subprocess.Popen(
            command,
            cwd=ROOT_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.process = process
        threading.Thread(
            target=self._read_worker,
            args=(process, generation),
            daemon=True,
        ).start()

    def _read_worker(self, process: subprocess.Popen[str], generation: int) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"type": "output", "text": line.rstrip("\n")}
            self.append(event, generation)
        code = process.wait()
        with self.lock:
            current = generation == self.generation
            has_complete = current and any(
                item.get("type") == "complete" for item in self.events
            )
        if current and not has_complete:
            self.append(
                {
                    "type": "complete",
                    "success": False,
                    "status": "stopped" if code < 0 else "worker_exit",
                },
                generation,
            )

    def submit(self, value: str) -> None:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("no AutoEnv run is waiting for input")
        process.stdin.write(json.dumps({"value": value}, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        self.process = None


SESSION = Session()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/scripts":
            try:
                from autoenv.registry import list_scripts

                scripts = [
                    {"name": item.name, "description": item.description}
                    for item in list_scripts(root_dir=ROOT_DIR)
                ]
                self.send_json({"scripts": scripts})
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/events":
            cursor = int(parse_qs(parsed.query).get("cursor", ["0"])[0])
            with SESSION.lock:
                events = SESSION.events[cursor:]
                next_cursor = len(SESSION.events)
            self.send_json({"events": events, "next": next_cursor})
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        try:
            if self.path == "/api/start":
                body = self.read_json()
                script = str(body.get("script", "")).strip()
                mode = str(body.get("mode", "run")).strip()
                if not script or mode not in {"run", "rerun"}:
                    raise ValueError("invalid script or mode")
                SESSION.start(script, mode)
                self.send_json({"ok": True})
                return
            if self.path == "/api/input":
                SESSION.submit(str(self.read_json().get("value", "")))
                self.send_json({"ok": True})
                return
            if self.path == "/api/stop":
                SESSION.stop()
                self.send_json({"ok": True})
                return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


class WorkerStream:
    def write(self, text: str) -> int:
        if text:
            emit({"type": "output", "text": text.rstrip("\n")})
        return len(text)

    def flush(self) -> None:
        return


def emit(event: dict[str, object]) -> None:
    sys.__stdout__.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


def worker(mode: str, script: str) -> int:
    from autoenv.registry import run_script

    def ask(prompt: str, secret: bool = False) -> str:
        emit({"type": "prompt", "label": prompt, "secret": secret})
        line = sys.stdin.readline()
        if not line:
            raise EOFError("frontend input channel closed")
        return str(json.loads(line).get("value", ""))

    try:
        result = run_script(
            script,
            mode=mode,
            root_dir=ROOT_DIR,
            input_func=ask,
            password_input=lambda prompt: ask(prompt, True),
            console=WorkerStream(),
        )
        emit({"type": "complete", "success": result.success, "status": result.status})
        return 0 if result.success else 1
    except Exception as exc:
        emit({"type": "output", "text": f"AutoEnv frontend worker error: {exc}"})
        emit({"type": "complete", "success": False, "status": "program_error"})
        return 2


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        return worker(sys.argv[2], sys.argv[3])
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"AutoEnv frontend: {url}")
    print("Press Ctrl+C to stop.")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SESSION.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
