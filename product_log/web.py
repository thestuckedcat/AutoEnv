"""HTTP adapter for the offline product-log workflow, on AutoEnv's single server.

Inputs can be explicit local paths. Every other operation accepts an opaque ID;
output paths cannot be supplied by the browser. Upload chunks never pass through
the older JSON/base64 upload endpoint.
"""
from __future__ import annotations

import json
import mimetypes
import posixpath
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .retention import DEFAULT_STORAGE_LIMIT_BYTES, enforce_retention, managed_root, remove_managed_tree, scan_managed_storage


STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_CHUNK_BYTES = 4 * 1024 * 1024
MAX_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024
MAX_UPLOAD_FILES = 100000
MAX_JSON_BYTES = 2 * 1024 * 1024
_ID = re.compile(r"^[0-9a-f]{32}$")
_MANAGERS: dict[str, "TaskManager"] = {}
_MANAGERS_LOCK = threading.Lock()


class BusyError(RuntimeError):
    pass


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid task/upload identifier")
    return value


def _relative_file(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value or ":" in value:
        raise ValueError("upload path must be a safe relative path")
    path = PurePosixPath(value)
    # Check raw components too: PurePath normalizes dot and double separators.
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("upload path must be a safe relative path")
    if any(part.endswith((" ", ".")) for part in path.parts):
        raise ValueError("upload path cannot end with spaces or dots")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(part.split(".")[0].upper() in reserved or any(ord(c) < 32 or c in '<>"|?*' for c in part) for part in path.parts):
        raise ValueError("upload path contains a reserved name or character")
    return path.as_posix()


def _within(root: Path, path: Path) -> Path:
    root, path = root.resolve(), path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("path escapes the managed workspace")
    return path


@dataclass
class Upload:
    root: Path
    kind: str
    sizes: dict[str, int]
    offsets: dict[str, int] = field(default_factory=dict)
    total: int = 0
    finished: bool = False
    claimed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Task:
    batch_id: str
    batch_dir: Path
    input_path: Path
    status: str = "preparing"
    events: list[dict[str, Any]] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    manifest: dict[str, Any] | None = None
    error: str = ""
    upload_id: str | None = None
    created_at_ns: int = field(default_factory=time.time_ns)


class TaskManager:
    """Single active parsing task, isolated from environment-script sessions."""

    def __init__(self, runtime_root: Path, *, storage_limit_bytes: int = DEFAULT_STORAGE_LIMIT_BYTES):
        self.root = managed_root(runtime_root)
        self.storage_limit_bytes = storage_limit_bytes
        self.lock = threading.RLock()
        self.tasks: dict[str, Task] = {}
        self.uploads: dict[str, Upload] = {}
        self.active: str | None = None
        self.viewed_batch_id: str | None = None
        self.last_cleanup: dict[str, Any] | None = None
        self._load_history()
        self.storage()

    def _load_history(self) -> None:
        trees, _ = scan_managed_storage(self.root)
        for tree in sorted(trees, key=lambda item: (item.created_at_ns, item.identifier)):
            if tree.kind != "batches" or not tree.safe:
                continue
            batch_id, batch_dir = tree.identifier, tree.path
            manifest_file = batch_dir / "manifest.json"
            try:
                metadata_file = batch_dir / "web-task.json"
                try:
                    metadata = json.loads(metadata_file.read_text(encoding="utf-8")) if metadata_file.exists() else {}
                except (OSError, ValueError):
                    metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}
                if not manifest_file.exists():
                    manifest_file = metadata_file
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    continue
                for name in ("upload_id", "managed_created_at_ns"):
                    if name not in manifest and name in metadata:
                        manifest[name] = metadata[name]
                status = str(manifest.get("status", "failed"))
                from ._storage import INDEX_SCHEMA_VERSION
                if status == "ready" and (manifest.get("index_schema_version") != INDEX_SCHEMA_VERSION or manifest.get("time_unit") != "microseconds"):
                    status = "failed"
                    manifest = dict(manifest, status=status, error="此批次需要重新导入源日志")
                # An interrupted process must not publish an unfinished database.
                if status not in {"ready", "failed", "cancelled"}:
                    status = "failed"
                upload_id = manifest.get("upload_id")
                if not isinstance(upload_id, str) or not _ID.fullmatch(upload_id):
                    upload_id = None
                    # Older directory uploads predate the explicit association.
                    input_root = managed_root(Path(str(manifest.get("input_root", ""))))
                    if input_root.is_relative_to(self.root / "uploads"):
                        parts = input_root.relative_to(self.root / "uploads").parts
                        if parts and _ID.fullmatch(parts[0]):
                            upload_id = parts[0]
                created_at_ns = manifest.get("managed_created_at_ns", tree.created_at_ns)
                if not isinstance(created_at_ns, int) or isinstance(created_at_ns, bool) or created_at_ns <= 0:
                    created_at_ns = tree.created_at_ns
                input_path = Path(str(manifest.get("input_path", metadata.get("input_root", manifest.get("input_root", "")))))
                kind, identifier = self._managed_input(input_path)
                if upload_id is None and kind == "uploads":
                    upload_id = identifier
                self.tasks[batch_id] = Task(batch_id, batch_dir, input_path, status=status, manifest=manifest, upload_id=upload_id, created_at_ns=created_at_ns)
            except (OSError, ValueError, TypeError):
                continue

    def pin_batch(self, batch_id: str) -> None:
        """Protect the reader's current batch without adding a new lock system."""
        with self.lock:
            if batch_id:
                self.task(batch_id)
            self.viewed_batch_id = batch_id or None

    def _managed_input(self, path: Path) -> tuple[str | None, str | None]:
        path = managed_root(path)
        if path.is_relative_to(self.root):
            parts = path.relative_to(self.root).parts
            if len(parts) >= 2 and parts[0] in {"batches", "uploads"} and _ID.fullmatch(parts[1]):
                return parts[0], parts[1]
        return None, None

    def storage(self, pin_batch_id: str | None = None, *, protected_input: Path | None = None) -> dict[str, Any]:
        with self.lock:
            if pin_batch_id is not None:
                self.pin_batch(pin_batch_id)
            result: dict[str, Any] | None = None
            while True:
                terminal = [task for task in self.tasks.values() if task.status in {"ready", "failed", "cancelled"}]
                protected = {task.batch_id for task in self.tasks.values() if task.status not in {"ready", "failed", "cancelled"}}
                for choices in (terminal, [task for task in terminal if task.status == "ready"]):
                    if choices:
                        protected.add(max(choices, key=lambda task: (task.created_at_ns, task.batch_id)).batch_id)
                protected.update(value for value in (self.active, self.viewed_batch_id) if value)
                protected_uploads = {identifier for identifier, upload in self.uploads.items() if not upload.claimed}
                inputs = [task.input_path for task in self.tasks.values()]
                if protected_input is not None:
                    inputs.append(protected_input)
                for path in inputs:
                    kind, identifier = self._managed_input(path)
                    if kind == "batches":
                        # A kept directory-import index can refer into an older
                        # batch. Delete dependents before releasing their input.
                        protected.add(identifier)
                    elif kind == "uploads" and protected_input is not None and path == protected_input:
                        protected_uploads.add(identifier)
                latest = enforce_retention(
                    self.root, limit_bytes=self.storage_limit_bytes,
                    protected_batch_ids=protected,
                    protected_upload_ids=protected_uploads,
                    batch_uploads={task.batch_id: task.upload_id for task in self.tasks.values() if task.upload_id is not None},
                    batch_created_at_ns={task.batch_id: task.created_at_ns for task in self.tasks.values()},
                )
                for identifier in latest["removed_batch_ids"]:
                    self.tasks.pop(identifier, None)
                for identifier in latest["removed_upload_ids"]:
                    self.uploads.pop(identifier, None)
                if result is None:
                    result = latest
                else:
                    for name in ("removed_batch_ids", "removed_upload_ids"):
                        result[name].extend(latest[name])
                    result["reclaimed_bytes"] += latest["reclaimed_bytes"]
                    result["errors"] = list(dict.fromkeys([*result["errors"], *latest["errors"]]))
                    for name in ("usage_bytes", "over_limit", "protected_batch_ids", "protected_upload_ids"):
                        result[name] = latest[name]
                if not latest["over_limit"] or not latest["removed_batch_ids"]:
                    break
            if result["removed_batch_ids"] or result["removed_upload_ids"]:
                self.last_cleanup = {name: result[name] for name in ("removed_batch_ids", "removed_upload_ids", "reclaimed_bytes")}
                self.last_cleanup["at"] = time.time()
            result["last_cleanup"] = self.last_cleanup
            return result

    def maintenance(self, pin_batch_id: str | None = None) -> dict[str, Any]:
        return self.storage(pin_batch_id)

    def create_upload(self, data: dict[str, Any]) -> dict[str, Any]:
        kind = data.get("kind", "archive")
        files = data.get("files")
        if kind not in {"archive", "directory"} or not isinstance(files, list) or not 1 <= len(files) <= MAX_UPLOAD_FILES:
            raise ValueError("upload needs kind archive/directory and a bounded file list")
        sizes: dict[str, int] = {}
        normalized: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("each upload file needs path and size")
            name = _relative_file(item.get("path"))
            size = item.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError("upload size must be a nonnegative integer")
            if name.casefold() in normalized:
                raise ValueError("duplicate upload path")
            normalized.add(name.casefold())
            sizes[name] = size
        if kind == "archive" and (len(sizes) != 1 or "/" in next(iter(sizes))):
            raise ValueError("archive upload accepts one file")
        if sum(sizes.values()) > MAX_UPLOAD_BYTES:
            raise ValueError("upload exceeds 20 GiB limit")
        # Reject file/directory conflicts before any bytes are written.
        if any(parent.as_posix().casefold() in normalized for name in sizes for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise ValueError("upload file and directory paths conflict")
        upload_id = uuid.uuid4().hex
        root = self.root / "uploads" / upload_id
        upload = Upload(root, str(kind), sizes, {name: 0 for name in sizes})
        with self.lock:
            self.storage()
            root.mkdir(parents=True, exist_ok=False)
            self.uploads[upload_id] = upload
        return {"upload_id": upload_id, "chunk_bytes": MAX_CHUNK_BYTES, "total_bytes": sum(sizes.values())}

    def upload(self, upload_id: Any) -> Upload:
        with self.lock:
            upload = self.uploads.get(_identifier(upload_id))
        if upload is None:
            raise ValueError("unknown upload identifier")
        return upload

    def write_chunk(self, upload_id: str, name: str, offset: int, length: int, stream: Any) -> dict[str, Any]:
        upload = self.upload(upload_id)
        name = _relative_file(name)
        if length < 0 or length > MAX_CHUNK_BYTES:
            raise ValueError("upload chunk exceeds 4 MiB limit")
        with upload.lock:
            if upload.finished or upload.claimed:
                raise ValueError("upload is already completed")
            if name not in upload.sizes or offset != upload.offsets[name]:
                raise ValueError("unknown file or out-of-order chunk")
            if offset + length > upload.sizes[name] or upload.total + length > MAX_UPLOAD_BYTES:
                raise ValueError("chunk exceeds declared file/upload size")
            target = _within(upload.root, upload.root / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Disk is bounded by the declared length. Never read the entire file.
            try:
                with target.open("ab") as destination:
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError("incomplete upload chunk")
                        destination.write(chunk)
                        remaining -= len(chunk)
            except BaseException:
                if target.exists():
                    with target.open("r+b") as destination:
                        destination.truncate(offset)
                raise
            upload.offsets[name] += length
            upload.total += length
            return {"ok": True, "offset": upload.offsets[name], "received_bytes": upload.total}

    def finish_upload(self, upload_id: str) -> dict[str, Any]:
        upload = self.upload(upload_id)
        with upload.lock:
            if upload.claimed:
                raise ValueError("upload is already used")
            if upload.offsets != upload.sizes:
                raise ValueError("upload is incomplete")
            for name in upload.sizes:
                target = _within(upload.root, upload.root / name)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.touch()
            upload.finished = True
            result = {"ok": True, "upload_id": upload_id, "file_count": len(upload.sizes), "total_bytes": upload.total}
        result["storage"] = self.storage()
        return result

    def cancel_upload(self, upload_id: str) -> dict[str, Any]:
        upload = self.upload(upload_id)
        with self.lock, upload.lock:
            if upload.claimed:
                raise ValueError("upload belongs to a task; stop that task instead")
            upload.finished = True
            remove_managed_tree(self.root, "uploads", upload_id)
            self.uploads.pop(upload_id, None)
        return {"ok": True}

    def _event(self, task: Task, event: dict[str, Any]) -> None:
        with self.lock:
            # Limit progress chatter without losing completion or task identity.
            if len(task.events) < 10000 or event.get("type") == "complete":
                task.events.append(event)

    def start(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.active is not None:
                raise BusyError("a product-log task is running; wait or stop it before starting another")
            upload = None
            if data.get("upload_id"):
                upload = self.upload(data["upload_id"])
                with upload.lock:
                    if not upload.finished or upload.claimed:
                        raise ValueError("upload is incomplete or already used")
                    input_path = upload.root if upload.kind == "directory" else upload.root / next(iter(upload.sizes))
            else:
                value = data.get("input_path")
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("input_path or completed upload_id is required")
                input_path = Path(value).expanduser()
                if not input_path.is_absolute():
                    raise ValueError("local input path must be absolute")
                if input_path.is_symlink():
                    raise ValueError("symbolic link input roots are not supported")
                input_path = input_path.resolve()
                if not input_path.exists():
                    raise ValueError("local input path does not exist")
            rules = _rules(data)
            self.storage(protected_input=input_path)
            input_kind, input_owner = self._managed_input(input_path)
            batch_id = uuid.uuid4().hex
            batch_dir = self.root / "batches" / batch_id
            batch_dir.mkdir(parents=True, exist_ok=False)
            task = Task(batch_id, batch_dir, input_path, upload_id=data.get("upload_id") if upload is not None else input_owner if input_kind == "uploads" else None)
            # run_analysis owns manifest.json, including intermediate progress.
            # Keep Web ownership separately until its final manifest is written.
            initial_manifest = {"batch_id": batch_id, "batch_dir": str(batch_dir), "input_root": str(input_path), "input_path": str(input_path), "status": "preparing", "upload_id": task.upload_id, "managed_created_at_ns": task.created_at_ns}
            (batch_dir / "web-task.json").write_text(json.dumps(initial_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            self.tasks[batch_id] = task
            self.active = batch_id
            if upload is not None:
                upload.claimed = True
            self._event(task, {"type": "progress", "stage": "preparing", "message": "准备读取输入", "batch_dir": str(batch_dir), "input_path": str(input_path)})
            threading.Thread(target=self._run, args=(task, rules), daemon=True, name=f"product-log-{batch_id[:8]}").start()
            return {"ok": True, "batch_id": batch_id, "task_id": batch_id, "batch_dir": str(batch_dir)}

    def _run(self, task: Task, rules: dict[str, Any]) -> None:
        started = time.monotonic()
        try:
            from .workflow import run_analysis

            def progress(value: dict[str, Any]) -> None:
                if not isinstance(value, dict):
                    return
                with self.lock:
                    task.status = str(value.get("stage", task.status))
                self._event(task, {**value, "type": "progress"})

            manifest = run_analysis(task.input_path, task.batch_dir, **rules, progress=progress, cancelled=task.cancel.is_set)
            task.manifest = dict(manifest)
            task.status = str(manifest.get("status", "ready"))
            if task.cancel.is_set() and task.status != "ready":
                task.status = "cancelled"
        except Exception as exc:
            task.status = "cancelled" if task.cancel.is_set() else "failed"
            task.error = str(exc)
            task.manifest = {"batch_id": task.batch_id, "batch_dir": str(task.batch_dir), "input_root": str(task.input_path), "product_log_path": str(task.batch_dir / "product_log"), "status": task.status, "error": task.error, "duration_seconds": round(time.monotonic() - started, 3), "counts": {}}
        finally:
            assert task.manifest is not None
            task.manifest.update(batch_id=task.batch_id, batch_dir=str(task.batch_dir), status=task.status, upload_id=task.upload_id, input_path=str(task.input_path), managed_created_at_ns=task.created_at_ns)
            try:
                (task.batch_dir / "manifest.json").write_text(json.dumps(task.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                task.error = f"cannot persist manifest: {exc}"
                task.status = "failed"
                task.manifest.update(status=task.status, error=task.error)
            with self.lock:
                self._event(task, {"type": "complete", "status": task.status, "manifest": task.manifest, "error": task.error})
                if self.active == task.batch_id:
                    self.active = None
                self.storage()

    def task(self, batch_id: Any, *, ready: bool = False) -> Task:
        with self.lock:
            task = self.tasks.get(_identifier(batch_id))
            if task is None:
                raise ValueError("unknown batch identifier")
            _within(self.root / "batches", task.batch_dir)
            if ready and task.status != "ready":
                raise ValueError("batch is not ready")
            return task

    def events(self, batch_id: str, cursor: int) -> dict[str, Any]:
        task = self.task(batch_id)
        if cursor < 0:
            raise ValueError("cursor must be nonnegative")
        with self.lock:
            return {"batch_id": task.batch_id, "events": task.events[cursor:], "next": len(task.events), "status": task.status, "manifest": task.manifest}

    def stop(self, batch_id: str) -> dict[str, Any]:
        task = self.task(batch_id)
        with self.lock:
            if self.active == task.batch_id:
                task.cancel.set()
                self._event(task, {"type": "progress", "stage": "cancelling", "message": "已请求停止，正在保留诊断并清理临时数据"})
        return {"ok": True}

    def batches(self) -> list[dict[str, Any]]:
        with self.lock:
            return [{"batch_id": task.batch_id, "status": task.status, "batch_dir": str(task.batch_dir), "manifest": task.manifest, "error": task.error} for task in sorted(self.tasks.values(), key=lambda item: (item.created_at_ns, item.batch_id), reverse=True)]


def _rules(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "analysis_config" in data:
        from .configuration import normalize_config
        if not isinstance(data["analysis_config"], dict):
            raise ValueError("analysis_config must be a configuration object")
        result["analysis_config"] = normalize_config(data["analysis_config"])
    for name in ("archive_patterns", "file_patterns", "copy_patterns"):
        value = data.get(name)
        if value is None or value == "":
            result[name] = None
        elif isinstance(value, str):
            result[name] = [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
        elif isinstance(value, list) and all(isinstance(part, str) for part in value):
            result[name] = value
        else:
            raise ValueError(f"{name} must be a string list")
    result["directory_pattern"] = data.get("directory_pattern", "")
    if not isinstance(result["directory_pattern"], str):
        raise ValueError("directory_pattern must be a string")
    result["collection_mode"] = data.get("collection_mode", "auto")
    if result["collection_mode"] not in {"auto", "product", "generic"}:
        raise ValueError("collection_mode must be auto, product or generic")
    for name, default in (("diag_directory_pattern", "diag"), ("var_directory_pattern", "varlog")):
        value = data.get(name, default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty directory pattern")
        result[name] = value
    result["recursive"] = data.get("recursive", True)
    if not isinstance(result["recursive"], bool):
        raise ValueError("recursive must be boolean")
    workers = data.get("workers", 4)
    if not isinstance(workers, int) or isinstance(workers, bool) or not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    result["workers"] = workers
    renames = data.get("rename_map")
    if renames is not None and (not isinstance(renames, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in renames.items())):
        raise ValueError("rename_map must map file names to new names")
    result["rename_map"] = renames
    return result


def manager(root_dir: Path) -> TaskManager:
    root = managed_root(root_dir / "webPage" / ".runtime" / "product-logs")
    with _MANAGERS_LOCK:
        return _MANAGERS.setdefault(str(root), TaskManager(root)) if str(root) not in _MANAGERS else _MANAGERS[str(root)]


def _json_body(handler: Any) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if not 0 <= length <= MAX_JSON_BYTES:
        raise ValueError("product-log JSON request exceeds 2 MiB limit")
    value = json.loads(handler.rfile.read(length).decode("utf-8") or "{}")
    if not isinstance(value, dict):
        raise ValueError("request body must be an object")
    return value


def _query(parsed: Any) -> dict[str, str]:
    values = parse_qs(parsed.query, keep_blank_values=True)
    if any(len(value) != 1 for value in values.values()):
        raise ValueError("duplicate query parameters")
    return {key: value[0] for key, value in values.items()}


def _filters(query: dict[str, str], *, paginated: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {name: query[name] for name in ("kind", "group_id", "slot_id", "cpu_id", "module_name", "device_id", "log_type", "keyword", "start_time", "end_time", "view", "mode", "driver_domain", "module") if name in query}
    result.setdefault("kind", "firmware")
    if result["kind"] not in {"all", "firmware", "driver", "unclassified"}:
        raise ValueError("invalid log kind")
    if result.get("driver_domain", "") not in {"", "user", "kernel"}:
        raise ValueError("invalid driver domain")
    if result.get("view", "time") not in {"time", "stitched"} or result.get("mode", "concise") not in {"concise", "raw"}:
        raise ValueError("invalid view or display mode")
    result["raw_search"] = query.get("raw_search", "false") == "true"
    if paginated:
        offset, limit = int(query.get("offset", "0")), int(query.get("limit", "200"))
        context = int(query.get("context_lines", "0"))
        if offset < 0 or not 1 <= limit <= 500 or not 0 <= context <= 10:
            raise ValueError("offset/limit/context are outside allowed ranges")
        result.update(offset=offset, limit=limit, context_lines=context)
    return result


def handle_get(handler: Any, parsed: Any, root_dir: Path) -> bool:
    normalized_path = posixpath.normpath(unquote(parsed.path).replace("\\", "/"))
    # SimpleHTTPRequestHandler otherwise exposes the webPage directory. Runtime
    # files (uploads, databases, manifests) are only reachable by registered IDs.
    if normalized_path == "/.runtime" or normalized_path.startswith("/.runtime/"):
        handler.json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        return True
    if parsed.path.startswith("/product-logs"):
        resources = {"/product-logs/": "index.html", "/product-logs": "index.html", "/product-logs/index.html": "index.html", "/product-logs/app.js": "app.js", "/product-logs/styles.css": "styles.css", "/product-logs/configuration.js": "configuration.js"}
        filename = resources.get(parsed.path)
        if filename is None:
            handler.json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return True
        payload = ((STATIC_DIR.parent / "standalone" / filename) if filename == "configuration.js" else (STATIC_DIR / filename)).read_bytes()
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", (mimetypes.guess_type(filename)[0] or "application/octet-stream") + "; charset=utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
        return True
    prefix = "/api/product-logs/"
    if not parsed.path.startswith(prefix):
        return False
    try:
        current = manager(root_dir)
        query = _query(parsed)
        route = parsed.path[len(prefix):]
        if route == "configuration":
            from .configuration import default_config
            handler.json({"analysis_config": default_config()})
        elif route == "batches":
            storage = current.storage()
            handler.json({"batches": current.batches(), "active": current.active, "storage": storage})
        elif route == "storage":
            handler.json(current.storage(pin_batch_id=query.get("batch_id") if "batch_id" in query else None))
        elif route == "events":
            handler.json(current.events(query.get("batch_id", query.get("task_id", "")), int(query.get("cursor", "0"))))
        elif route in {"groups", "query", "source", "export", "diagnostics", "modules"}:
            from .index import get_source, iter_export, list_diagnostics, list_groups, query_records
            task = current.task(query.get("batch_id", ""), ready=True)
            current.pin_batch(task.batch_id)
            if route == "groups":
                kind = query.get("kind", "firmware")
                if kind not in {"all", "driver", "firmware", "unclassified"}:
                    raise ValueError("invalid log kind")
                domain = query.get("driver_domain", "")
                if domain not in {"", "user", "kernel"}:
                    raise ValueError("invalid driver domain")
                handler.json({"groups": list_groups(task.batch_dir, kind, driver_domain=domain)})
            elif route == "modules":
                from .module_map import list_module_mappings
                handler.json({"modules": list_module_mappings(task.batch_dir)})
            elif route == "query":
                handler.json(query_records(task.batch_dir, **_filters(query)))
            elif route == "diagnostics":
                offset, limit = int(query.get("offset", "0")), int(query.get("limit", "200"))
                if offset < 0 or not 1 <= limit <= 500:
                    raise ValueError("invalid diagnostic offset/limit")
                handler.json(list_diagnostics(task.batch_dir, offset=offset, limit=limit))
            elif route == "source":
                offset, limit = int(query.get("offset", "0")), int(query.get("limit", "200"))
                if offset < 0 or not 1 <= limit <= 500:
                    raise ValueError("invalid source offset/limit")
                handler.json(get_source(task.batch_dir, query.get("source_id", ""), offset=offset, limit=limit))
            else:
                export = iter_export(task.batch_dir, **_filters(query, paginated=False))
                # Resolve the initial database/query before sending HTTP headers.
                first = next(export, "")
                handler.send_response(HTTPStatus.OK)
                handler.send_header("Content-Type", "text/plain; charset=utf-8")
                handler.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote('product-logs-' + task.batch_id[:8] + '.log')}")
                handler.send_header("Cache-Control", "no-store")
                handler.end_headers()
                for line in _export_lines(first, export):
                    handler.wfile.write(line.encode("utf-8"))
        else:
            handler.json({"error": "not found"}, HTTPStatus.NOT_FOUND)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        handler.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
    return True


def _export_lines(first: str, remainder: Any):
    if first:
        yield first if first.endswith("\n") else first + "\n"
    for line in remainder:
        yield line if line.endswith("\n") else line + "\n"


def handle_post(handler: Any, root_dir: Path) -> bool:
    parsed = urlparse(handler.path)
    prefix = "/api/product-logs/"
    if not parsed.path.startswith(prefix):
        return False
    try:
        current = manager(root_dir)
        route = parsed.path[len(prefix):]
        if route == "uploads/chunk":
            query = _query(parsed)
            length = int(handler.headers.get("Content-Length", "-1"))
            result = current.write_chunk(query.get("upload_id", ""), query.get("path", ""), int(query.get("offset", "0")), length, handler.rfile)
        else:
            data = _json_body(handler)
            if route == "uploads/create":
                result = current.create_upload(data)
            elif route == "uploads/finish":
                result = current.finish_upload(_identifier(data.get("upload_id")))
            elif route == "uploads/cancel":
                result = current.cancel_upload(_identifier(data.get("upload_id")))
            elif route == "start":
                result = current.start(data)
            elif route == "stop":
                result = current.stop(_identifier(data.get("batch_id", data.get("task_id"))))
            elif route == "modules":
                from .module_map import save_module_mappings
                with current.lock:
                    task = current.task(data.get("batch_id"), ready=True)
                    current.pin_batch(task.batch_id)
                    result = {"modules": save_module_mappings(task.batch_dir, data.get("modules"))}
            else:
                handler.json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return True
        handler.json(result)
    except BusyError as exc:
        handler.json({"error": str(exc)}, HTTPStatus.CONFLICT)
    except Exception as exc:
        handler.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
    return True
