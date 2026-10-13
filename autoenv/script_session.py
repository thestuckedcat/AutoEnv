from __future__ import annotations

import contextlib
import contextvars
import inspect
import io
import json
import math
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, Sequence, overload

from .recorder import RunRecorder
from .remote_catalog import allowed_pairs, compatible_topologies, read_catalog, revision
from .remote_environment import FTPRemote, SSHRemote, TelnetRemote
from .results import result_to_dict


_CURRENT = contextvars.ContextVar("autoenv_script_session", default=None)
_TRACE = contextvars.ContextVar("autoenv_script_trace", default=None)
_AUTOENV_DIR = Path(__file__).parent.resolve()

def current_session():
    return _CURRENT.get()


def _source() -> dict:
    frame = inspect.currentframe().f_back
    session = current_session()
    fallback = None
    while frame:
        path = Path(frame.f_code.co_filename).resolve()
        origin = {"file": str(path), "line": frame.f_lineno, "function": frame.f_code.co_name}
        if session and session.descriptor and path == Path(session.descriptor["path"]):
            if fallback is not None and fallback != origin:
                origin["callsite"] = fallback
            return origin
        if fallback is None and path.parent != _AUTOENV_DIR and path.name != "contextlib.py" and frame.f_code.co_filename not in {"<string>", "<frozen runpy>"}:
            fallback = origin
        frame = frame.f_back
    return fallback or {"file": "unknown", "line": 0, "function": "unknown"}


@dataclass(frozen=True)
class ScriptPaths:
    run_id: str
    run_dir: Path
    runtime_path: Path
    log_path: Path
    output_path: Path
    params_path: Path
    result_path: Path
    arguments: Mapping[str, object]
    _owner: object

    def close(self) -> None:
        self._owner.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc and exc[0] is not None and not self._owner.hosted:
            self._owner.finish(success=False, status="program_error", error=exc[1])
        self.close()


class SessionRecorder(RunRecorder):
    def __init__(self, session):
        self.session = session
        super().__init__(session.log_path, console=io.StringIO())

    def record_result(self, operation, result, *, console=True):
        origin = _TRACE.get() or {"source": _source(), "label": operation}
        self.log(f"FLOW {json.dumps(origin, ensure_ascii=False)}", console=False)
        super().record_result(operation, result, console=False)
        self.session.record(result)


class ExecutionSession:
    def __init__(self, root_dir: Path | str, *, event_file: Path | str | None = None):
        self.root_dir = Path(root_dir).resolve()
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
        self.run_dir = self.root_dir / "logs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.log_path = self.run_dir / "run.log"
        self.output_path = self.run_dir / "output.log"
        self.runtime_path = self.run_dir / "runtime"
        self.params_path = self.run_dir / "params.json"
        self.result_path = self.run_dir / "result.json"
        self._output = self.output_path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.RLock()
        self.recorder = SessionRecorder(self)
        self.remotes = []
        self.operations = []
        self.final_operation = None
        self._recorded_ids = set()
        self._step = 0
        self._closed = False
        self._body_closed = False
        self.hosted = False
        self._initialized = False
        self._finished = False
        self._token = None
        self.descriptor = None
        self.catalog = {}
        self.topology = None
        self.bindings = {}
        self.packages = {}
        self.arguments = {}
        self.non_interactive = False
        self.history = {}
        self.script_output = None
        self.recorder.log(f"SESSION START run_id={self.run_id}", console=False)
        self.bootstrap_error = None
        if event_file:
            try:
                self.recorder.write_json(Path(event_file), {"log_path": str(self.log_path), "output_path": str(self.output_path), "run_dir": str(self.run_dir)})
            except Exception as exc:
                # The execution log is already usable. Report this preparation
                # failure through the same session instead of leaking its streams.
                self.bootstrap_error = exc
                self.recorder.log(f"EVENT LOCATION FAILED {type(exc).__name__}: {exc}", console=False)

    @contextlib.contextmanager
    def activate(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    def emit(self, text: str) -> None:
        with self._lock:
            self._output.write(text.replace("\r", "").rstrip("\n") + "\n")
            self._output.flush()
            self.recorder.log(f"OUTPUT {text}", console=False)

    @contextlib.contextmanager
    def trace(self, label: str):
        if self._closed or self._body_closed:
            raise RuntimeError("script session is closed")
        with self._lock:
            self._step += 1
            step = self._step
        source = _source()
        origin = {"step": step, "label": label, "source": source}
        token = _TRACE.set(origin)
        before = len(self.operations)
        brief = label.replace("\n", "\\n").replace("\r", "\\r")
        if len(brief) > 200:
            brief = brief[:197] + "..."
        prefix = f"#{step:04d} {Path(source['file']).name}:{source['line']} {brief}"
        self.recorder.log(f"FLOW START {json.dumps(origin, ensure_ascii=False)}", console=False)
        self.emit(f"[{prefix}] 开始")
        try:
            yield
        except BaseException as exc:
            self.recorder.log(f"FLOW FAILED {json.dumps(origin, ensure_ascii=False)} {type(exc).__name__}: {exc}", console=False)
            self.emit(f"[{prefix}] 失败 {type(exc).__name__}: {exc}")
            raise
        else:
            records = self.operations[before:]
            result = records[-1]["result"] if records else {}
            state = result.get("status", "success")
            exit_code = result.get("exit_code")
            suffix = f" exit={exit_code}" if exit_code is not None else ""
            if result.get("check_scope") is not None:
                suffix += f" check_scope={result['check_scope']}"
            if result.get("exit_status_available") is not None:
                suffix += " exit_status=" + ("known" if result["exit_status_available"] else "unknown")
            self.emit(f"[{prefix}] {state}{suffix}")
            if result.get("error_type") or result.get("error_message"):
                detail = ": ".join(str(result[name]) for name in ("error_type", "error_message") if result.get(name))
                detail = detail.replace("\n", "\\n").replace("\r", "\\r")
                self.emit(f"[{prefix}] 原因 {detail[:240]}")
            self.recorder.log(f"FLOW END {json.dumps(origin, ensure_ascii=False)} status={state}{suffix}", console=False)
        finally:
            _TRACE.reset(token)

    def record(self, result: object) -> None:
        value = result_to_dict(result)
        if not isinstance(value, dict):
            return
        key = (value.get("operation_id"), value.get("status"), value.get("target_name"))
        with self._lock:
            if key in self._recorded_ids:
                return
            self._recorded_ids.add(key)
            self.operations.append({"flow": _TRACE.get(), "result": value})

    def select_final(self, result: object) -> None:
        self.record(result)
        value = result_to_dict(result)
        self.final_operation = next((item for item in reversed(self.operations) if item["result"] == value), {"flow": _TRACE.get(), "result": value})

    def prepare(self, descriptor: dict, request: dict) -> None:
        self.descriptor = descriptor
        self.non_interactive = request.get("non_interactive", False)
        if not isinstance(self.non_interactive, bool):
            raise TypeError("non_interactive must be a boolean")
        self.catalog = read_catalog(self.root_dir)
        topologies = compatible_topologies(descriptor["slots"], self.catalog)
        policy = request.get("history_policy", "ignore" if self.non_interactive else "use")
        if policy not in {"ignore", "use", "require"}:
            raise ValueError("history_policy must be ignore, use or require")
        self.history_path = self.root_dir / "state" / "script_runs" / (descriptor["script"] + "_" + _identity(descriptor["path"]) + ".json")
        if policy != "ignore" and self.history_path.is_file():
            self.history = json.loads(self.history_path.read_text(encoding="utf-8-sig"))
            if self.history.get("layout") != descriptor["layout"]:
                self.history = {}
        if policy == "require" and not self.history:
            raise ValueError("no valid script history is available")
        supplied = request.get("bindings", {})
        packages = request.get("packages", {})
        arguments = request.get("arguments", {})
        if not all(isinstance(value, dict) for value in (supplied, packages, arguments)):
            raise TypeError("bindings, packages and arguments must be objects")
        roles = {slot["role"] for slot in descriptor["slots"]}
        if set(supplied) - roles:
            raise ValueError(f"unknown environment bindings: {sorted(set(supplied) - roles)}")
        if set(packages) - set(descriptor["packages"]):
            raise ValueError("unknown package binding")
        if any(not isinstance(value, str) or not value.strip() for value in packages.values()):
            raise ValueError("package remote paths must be nonempty strings")
        self.packages = {**self.history.get("packages", {}), **packages}
        if descriptor["packages"]:
            from .package_manager import PackageManager
            specs = PackageManager._load_specs(self.root_dir / "config.json")
            for name in descriptor["packages"]:
                if name not in specs:
                    raise ValueError(f"unknown config.json package: {name}")
        inherited = self.history.get("arguments", {})
        self.arguments = validate_arguments(descriptor["arguments"], {**inherited, **arguments}, non_interactive=self.non_interactive)
        explicit_topology = request.get("selected_topology")
        if explicit_topology is not None and (not isinstance(explicit_topology, str) or explicit_topology not in topologies):
            raise ValueError("selected topology is outside the script whitelist intersection")
        if topologies:
            old_topology = self.history.get("selected_topology")
            if explicit_topology:
                self.topology = explicit_topology
            elif self.non_interactive:
                if old_topology not in topologies:
                    raise ValueError("non-interactive request requires selected_topology")
                self.topology = old_topology
            else:
                self.topology = _choose("组网", topologies, old_topology)
        for slot in descriptor["slots"]:
            role = slot["role"]
            candidates = [name for topology, name in slot["allowed_envs"] if topology == self.topology and self.catalog[topology][name]["enabled"]]
            value = supplied.get(role)
            if value is not None and (not isinstance(value, str) or value not in candidates):
                raise ValueError(f"invalid binding for {role}")
            old = self.history.get("connections", {}).get(role, {})
            old_name = old.get("name")
            if (old.get("topology") != self.topology or old_name not in candidates or old.get("revision") != revision(self.catalog[self.topology][old_name])):
                old_name = None
            if value is None:
                if self.non_interactive:
                    if old_name is None:
                        raise ValueError(f"non-interactive request requires binding: {role}")
                    value = old_name
                else:
                    value = _choose(f"{role} ({slot['required_type']})", candidates, old_name)
            self.bindings[role] = value
        self.params = {
            "schema_version": 2, "script": descriptor["script"], "path": descriptor["path"],
            "revision": descriptor["revision"],
            "layout": descriptor["layout"], "selected_topology": self.topology,
            "bindings": self.bindings, "arguments": self.arguments, "packages": self.packages,
            "connections": {role: {"topology": self.topology, "name": name, "revision": revision(self.catalog[self.topology][name])} for role, name in self.bindings.items()},
        }
        self.recorder.write_json(self.params_path, self.params)
        self.recorder.write_json(self.run_dir / "workflow-source.json", {
            "path": descriptor["path"], "revision": descriptor["revision"],
            "source": Path(descriptor["path"]).read_text(encoding="utf-8-sig"),
        })
        self.recorder.log("PREPARED " + json.dumps(self.params, ensure_ascii=False), console=False)

    def initialize(self) -> ScriptPaths:
        if self._closed or self._body_closed:
            raise RuntimeError("script session is closed")
        if not self._initialized:
            self.runtime_path.mkdir(parents=True, exist_ok=True)
            self._initialized = True
            self.emit("[初始化] runtime 与日志已就绪")
        return ScriptPaths(self.run_id, self.run_dir, self.runtime_path, self.log_path, self.output_path, self.params_path, self.result_path, MappingProxyType(self.arguments), self)

    def finish(self, *, success: bool, status: str, error: BaseException | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        self.recorder.write_json(self.result_path, {
            "run_id": self.run_id, "success": success, "status": status,
            "script": self.descriptor["script"] if self.descriptor else None,
            "script_output": self.script_output, "operations": self.operations,
            "final_operation": self.final_operation or (self.operations[-1] if self.operations else None),
            "error_type": type(error).__name__ if error else None, "error_message": str(error) if error else None,
            "log_path": str(self.log_path), "output_path": str(self.output_path),
        })
        if self._initialized and self.descriptor:
            self.recorder.write_json(self.history_path, self.params)
        self.emit(f"[脚本结束] {'成功' if success else '失败'} status={status}")

    def close(self) -> None:
        if self._closed:
            return
        if self._initialized and not self._finished:
            unavailable = next((remote for remote in self.remotes if not remote.available), None)
            available = unavailable is None
            if unavailable:
                self.select_final(unavailable.self_check_result)
            self.finish(success=available, status="success" if available else "self_check_failed")
        for remote in self.remotes:
            try:
                remote.close()
            except Exception as exc:
                self.recorder.log(f"CLOSE ERROR {exc}", console=False)
        self.recorder.close()
        self._output.close()
        self._closed = True
        if self._token is not None:
            _CURRENT.reset(self._token)
            self._token = None

    def release(self) -> None:
        if not self.hosted:
            self.close()
            return
        for remote in self.remotes:
            remote.close()
        self._body_closed = True


def _identity(path: str) -> str:
    import hashlib

    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]


def _choose(label: str, candidates: list[str], previous: str | None = None) -> str:
    if not candidates:
        raise ValueError(f"no candidates for {label}")
    if len(candidates) == 1:
        return candidates[0]
    for i, name in enumerate(candidates, 1):
        print(f"{i}. {name}", file=prompt_stream())
    print(f"{label} [回车: {previous or '无'}]: ", end="", file=prompt_stream(), flush=True)
    answer = input().strip()
    if not answer and previous in candidates:
        return previous
    if answer in candidates:
        return answer
    if answer.isdecimal() and 1 <= int(answer) <= len(candidates):
        return candidates[int(answer) - 1]
    raise ValueError(f"invalid selection for {label}")


def validate_arguments(schemas: list[dict], supplied: dict, *, non_interactive: bool) -> dict:
    expected = {schema["name"] for schema in schemas}
    if set(supplied) - expected:
        raise ValueError(f"unknown script arguments: {sorted(set(supplied) - expected)}")
    result = {}
    for schema in schemas:
        name = schema["name"]
        if name in supplied:
            value = supplied[name]
        elif "default" in schema:
            value = schema["default"]
        elif schema.get("required", False):
            if non_interactive:
                raise ValueError(f"missing required argument: {name}")
            print(f"参数 {name}: ", end="", file=prompt_stream(), flush=True)
            value = input()
        else:
            result[name] = None
            continue
        if value is None and not schema.get("required", False):
            result[name] = None
            continue
        kind = schema.get("type", "str")
        if kind == "str":
            if not isinstance(value, str):
                raise TypeError(f"argument {name} must be str")
        elif kind == "bool":
            if isinstance(value, str) and value in {"true", "false"}:
                value = value == "true"
            if not isinstance(value, bool):
                raise TypeError(f"argument {name} must be true/false")
        elif kind in {"int", "float"}:
            if isinstance(value, bool):
                raise TypeError(f"argument {name} must be {kind}")
            if kind == "int" and isinstance(value, float):
                raise TypeError(f"argument {name} must be int")
            value = int(value) if kind == "int" else float(value)
            if not math.isfinite(value):
                raise ValueError(f"argument {name} must be finite")
        elif not isinstance(value, list if kind == "list" else dict):
            raise TypeError(f"argument {name} must be {kind}; use a JSON request")
        if schema.get("required") and value == "":
            raise ValueError(f"required argument is empty: {name}")
        result[name] = value
    return result


@contextlib.contextmanager
def operation_scope(label: str):
    session = current_session()
    if session:
        with session.trace(label):
            yield
    else:
        yield


def prompt_stream():
    session = current_session()
    return getattr(session, "prompt_stream", sys.__stderr__) if session else sys.__stderr__


def init_script() -> ScriptPaths:
    session = current_session()
    if session is None:
        from .script_files import inspect_script

        source = _source()
        session = ExecutionSession(Path.cwd())
        try:
            session.prepare(inspect_script(source["file"]), {})
            session._token = _CURRENT.set(session)
        except BaseException:
            session.close()
            raise
    return session.initialize()


@overload
def get_remote_info(required_type: Literal["ssh"], allowed_envs: Sequence[tuple[str, str]]) -> SSHRemote: ...


@overload
def get_remote_info(required_type: Literal["telnet"], allowed_envs: Sequence[tuple[str, str]]) -> TelnetRemote: ...


@overload
def get_remote_info(required_type: Literal["ftp"], allowed_envs: Sequence[tuple[str, str]]) -> FTPRemote: ...


def get_remote_info(required_type: str, allowed_envs: Sequence[tuple[str, str]]) -> SSHRemote | TelnetRemote | FTPRemote:
    from .remote_environment import create_remote

    session = current_session()
    if session is None or not session._initialized:
        raise RuntimeError("call init_script() before get_remote_info()")
    pairs = allowed_pairs(allowed_envs)
    source = _source()
    matches = [slot for slot in session.descriptor["slots"] if slot["required_type"] == required_type and slot["allowed_envs"] == pairs and slot["line"] <= source["line"] <= slot["end_line"] and Path(session.descriptor["path"]) == Path(source["file"])]
    if len(matches) != 1:
        raise ValueError("environment request does not match a statically declared role")
    role = matches[0]["role"]
    name = session.bindings[role]
    config = {**session.catalog[session.topology][name], "topology": session.topology}
    with session.trace(f"get_remote_info {role} -> {session.topology}/{name}"):
        remote = create_remote(session, config)
        if remote not in session.remotes:
            session.remotes.append(remote)
        return remote
