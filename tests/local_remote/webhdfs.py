"""Loopback WebHDFS endpoint with real HTTP redirects and streamed file bodies."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit, quote

from autoenv.package_manager import HDFSClient


class LocalWebHDFS:
    def __init__(self, directories=None, downloads=None):
        self.directories = directories or {}
        self.downloads = downloads or {}
        self.list_calls: list[str] = []
        self.open_calls: list[str] = []
        self.transfers: list[tuple[str, int]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send_body(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def fail(self, value):
                status = 404 if isinstance(value, FileNotFoundError) else (
                    408 if isinstance(value, TimeoutError) else 500
                )
                self.send_body(status, json.dumps({"RemoteException": {
                    "exception": type(value).__name__, "message": str(value)
                }}).encode())

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path.startswith("/data/"):
                    path = unquote(parsed.path[len("/data"):])
                    value = owner.downloads.get(path, FileNotFoundError(path))
                    if isinstance(value, BaseException):
                        self.fail(value)
                        return
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(value)))
                    self.send_header("Content-Type", "application/octet-stream")
                    self.end_headers()
                    for offset in range(0, len(value), 1024):
                        self.wfile.write(value[offset:offset + 1024])
                    self.wfile.flush()
                    owner.transfers.append((path, len(value)))
                    return
                path = unquote(parsed.path.removeprefix("/webhdfs/v1"))
                query = parse_qs(parsed.query)
                if not parsed.path.startswith("/webhdfs/v1") or query.get("user.name") != ["hadoop"]:
                    self.send_body(400, b"{}")
                    return
                op = query.get("op", [""])[0]
                if op == "LISTSTATUS":
                    owner.list_calls.append(path)
                    values = owner.directories.get(path, FileNotFoundError(path))
                    if isinstance(values, BaseException):
                        self.fail(values)
                        return
                    statuses = []
                    for item in values:
                        if isinstance(item, dict):
                            statuses.append(item)
                        else:
                            statuses.append({
                                "pathSuffix": item.name,
                                "type": "DIRECTORY" if item.is_directory else "FILE",
                                "length": item.length,
                                "modificationTime": int(item.modification_time.timestamp() * 1000)
                                if item.modification_time else 0,
                            })
                    self.send_body(200, json.dumps({"FileStatuses": {"FileStatus": statuses}}).encode())
                elif op == "OPEN":
                    owner.open_calls.append(path)
                    self.send_response(307)
                    self.send_header("Location", owner.base_url + "/data" + quote(path))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_body(400, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.host, self.port = self.server.server_address
        self.base_url = f"http://{self.host}:{self.port}"
        self.client = HDFSClient(self.base_url)
        self.client.session.trust_env = False
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()

    def close(self):
        self.client.session.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("WebHDFS service did not stop")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
