from __future__ import annotations

import getpass
import json
import shutil
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, TextIO

from .command_files import UploadedFileRegistry, generate_sh_file
from .recorder import RunRecorder, mask_sensitive
from .selectors import (
    LocalFileSelector,
    PackageSelector,
    ResolvedLocalFile,
    resolve_local_file,
)


DEFAULT_PACKAGE_CACHE_LIMIT = 1024 * 1024 * 1024
PACKAGE_NEWEST_SHORTCUT = "!newest"


class RunMode(str, Enum):
    RUN = "run"
    RERUN = "rerun"


class LastRunNotFoundError(RuntimeError):
    pass


class LastRunParameterError(RuntimeError):
    pass


def _validate_port(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if not 1 <= value <= 65535:
        raise ValueError(f"{label} must be between 1 and 65535")
    return value


def _validate_timeout(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    normalized = float(value)
    if normalized <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return normalized


class RunContext:
    def __init__(
        self,
        *,
        root_dir: Path,
        script_name: str,
        mode: RunMode,
        input_func: Callable[[str], str] = input,
        password_input: Callable[[str], str] = getpass.getpass,
        console: TextIO | None = None,
        package_cache_limit: int = DEFAULT_PACKAGE_CACHE_LIMIT,
        hdfs_client: object | None = None,
        parameters: dict[str, object] | None = None,
        non_interactive: bool = False,
    ) -> None:
        self.root_dir = root_dir.resolve()
        self.script_name = script_name
        self.mode = RunMode(mode)
        self.input_func = input_func
        self.password_input = password_input
        if parameters is not None and not isinstance(parameters, dict):
            raise TypeError("parameters must be a dictionary")
        self.supplied_params = dict(parameters or {})
        self.non_interactive = bool(non_interactive)
        if isinstance(package_cache_limit, bool) or not isinstance(package_cache_limit, int):
            raise TypeError("package_cache_limit must be an integer")
        if package_cache_limit < 0:
            raise ValueError("package_cache_limit must not be negative")
        self.package_cache_limit = package_cache_limit
        self.started_at = datetime.now().astimezone()

        self.logs_dir = self.root_dir / "logs"
        self.state_dir = self.root_dir / "state" / "last_runs"
        self.last_run_path = self.state_dir / f"{script_name}.json"
        self.run_id = self._allocate_run_id()
        self.run_dir = self.logs_dir / self.run_id
        self.package_dir = self.run_dir / "packages"
        self.log_path = self.run_dir / "run.log"
        self.params_path = self.run_dir / "params.json"
        self.result_path = self.run_dir / "result.json"

        self._last_params = self._load_last_params(required=self.mode == RunMode.RERUN)
        self.params: dict[str, object] = {
            "script_name": self.script_name,
            "ssh_hosts": {},
            "telnet_connections": {},
            "ftp_hosts": {},
            "packages": {},
            "arguments": {},
        }
        self._ssh_hosts: dict[str, object] = {}
        self._telnet_clients: dict[str, object] = {}
        self._ftp_hosts: dict[str, object] = {}
        self._hdfs_client = hdfs_client
        self._package_manager: object | None = None
        self._extractor: object | None = None
        self._uploaded_files = UploadedFileRegistry()
        self._closed = False

        self.package_dir.mkdir(parents=True, exist_ok=False)
        self.recorder = RunRecorder(self.log_path, console=console)
        try:
            self._save_params()
            self.recorder.log(
                f"SCRIPT START name={self.script_name} mode={self.mode.value}",
                console=False,
            )
            self.recorder.log(f"run_dir={self.run_dir}", console=False)
            self.recorder.log(f"package_dir={self.package_dir}", console=False)
            self.recorder.log(f"log_file={self.log_path}", console=False)
            self.recorder.console_block(
                "SCRIPT START",
                (
                    f"name: {self.script_name}",
                    f"mode: {self.mode.value}",
                    f"run_dir: {self.run_dir}",
                    f"package_dir: {self.package_dir}",
                    f"log_file: {self.log_path}",
                ),
            )
            self._cleanup_historical_packages()
        except Exception as exc:
            try:
                self.recorder.write_json(
                    self.result_path,
                    {
                        "run_id": self.run_id,
                        "script_name": self.script_name,
                        "success": False,
                        "status": "program_error",
                        "started_at": self.started_at,
                        "finished_at": datetime.now().astimezone(),
                        "final_operation_id": None,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            finally:
                self.recorder.close()
            raise

    def _allocate_run_id(self) -> str:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        base = f"{self.started_at:%Y%m%d_%H%M%S}_{self.started_at.microsecond // 1000:03d}_{self.script_name}"
        candidate = base
        suffix = 1
        while (self.logs_dir / candidate).exists():
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _load_last_params(self, *, required: bool) -> dict[str, object]:
        if not self.last_run_path.is_file():
            if required:
                raise LastRunNotFoundError(
                    f"no last-run parameters exist for script {self.script_name!r}: "
                    f"{self.last_run_path}"
                )
            return {}
        try:
            with self.last_run_path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise LastRunParameterError(
                f"failed to read last-run parameters: {self.last_run_path}"
            ) from exc
        if not isinstance(value, dict):
            raise LastRunParameterError("last-run parameters must be a JSON object")
        return value

    def register_ssh_host(self, name: str, *, defaults: object | None = None):
        from .ssh_host import SSHConnectionInfo, SSHDefaults, SSHHost

        normalized_name = self._validate_object_name(name, "SSH host")
        if normalized_name in self._ssh_hosts or normalized_name in self._telnet_clients:
            raise ValueError(f"connection object already registered in this run: {normalized_name}")
        if defaults is None:
            defaults = SSHDefaults()
        if not isinstance(defaults, SSHDefaults):
            raise TypeError("defaults must be SSHDefaults")

        history = self._history_for("ssh_hosts", normalized_name)
        values = self._collect_values(
            object_label=f"SSH {normalized_name}",
            defaults=asdict(defaults),
            history=history,
            supplied=self._supplied_section("ssh_hosts", normalized_name),
            fields=(
                ("host", str, False),
                ("port", int, False),
                ("username", str, False),
                ("password", str, True),
                ("connect_timeout", float, False),
            ),
        )
        values["host"] = str(values["host"]).strip()
        values["username"] = str(values["username"]).strip()
        if not values["host"]:
            raise ValueError(f"SSH {normalized_name} host must not be empty")
        if not values["username"]:
            raise ValueError(f"SSH {normalized_name} username must not be empty")
        values["port"] = _validate_port(int(values["port"]), "SSH port")
        values["connect_timeout"] = _validate_timeout(
            float(values["connect_timeout"]), "SSH connect_timeout"
        )
        info = SSHConnectionInfo(**values)
        host = SSHHost(
            name=normalized_name,
            info=info,
            run_id=self.run_id,
            package_dir=self.package_dir,
            recorder=self.recorder,
            image_pattern_for=self.image_pattern_for,
            uploaded_files=self._uploaded_files,
        )
        self._ssh_hosts[normalized_name] = host
        self.params["ssh_hosts"][normalized_name] = dict(values)  # type: ignore[index]
        self._save_params()
        self.recorder.log(
            f"REGISTER SSH name={normalized_name} values={mask_sensitive(values)}"
        )
        return host

    def register_telnet(
        self,
        name: str,
        *,
        defaults: object | None = None,
        uploaded_files_from: str | None = None,
    ):
        from .telnet_client import TelnetClient, TelnetConnectionInfo, TelnetDefaults

        normalized_name = self._validate_object_name(name, "Telnet")
        if normalized_name in self._telnet_clients or normalized_name in self._ssh_hosts:
            raise ValueError(f"connection object already registered in this run: {normalized_name}")
        if defaults is None:
            defaults = TelnetDefaults()
        if not isinstance(defaults, TelnetDefaults):
            raise TypeError("defaults must be TelnetDefaults")
        if uploaded_files_from is not None:
            uploaded_files_from = self._validate_object_name(
                uploaded_files_from, "uploaded_files_from SSH host"
            )
            if uploaded_files_from not in self._ssh_hosts:
                raise ValueError(
                    f"uploaded_files_from SSH host is not registered: {uploaded_files_from}"
                )

        history = self._history_for("telnet_connections", normalized_name)
        values = self._collect_values(
            object_label=f"Telnet {normalized_name}",
            defaults=asdict(defaults),
            history=history,
            supplied=self._supplied_section("telnet_connections", normalized_name),
            fields=(
                ("host", str, False),
                ("port", int, False),
                ("timeout", float, False),
                ("shell_mode", str, False),
            ),
        )
        values["host"] = str(values["host"]).strip()
        if not values["host"]:
            raise ValueError(f"Telnet {normalized_name} host must not be empty")
        values["port"] = _validate_port(int(values["port"]), "Telnet port")
        values["timeout"] = _validate_timeout(float(values["timeout"]), "Telnet timeout")
        info = TelnetConnectionInfo(**values)
        client = TelnetClient(
            name=normalized_name,
            info=info,
            run_id=self.run_id,
            recorder=self.recorder,
            uploaded_files=self._uploaded_files,
            uploaded_files_from=uploaded_files_from,
        )
        self._telnet_clients[normalized_name] = client
        self.params["telnet_connections"][normalized_name] = dict(values)  # type: ignore[index]
        self._save_params()
        self.recorder.log(
            f"REGISTER TELNET name={normalized_name} values={values} "
            f"uploaded_files_from={uploaded_files_from!r}"
        )
        return client

    def register_ftp_host(self, name: str, *, defaults: object | None = None):
        from .ftp_host import FTPConnectionInfo, FTPDefaults, FTPHost

        normalized_name = self._validate_object_name(name, "FTP host")
        if normalized_name in self._ssh_hosts or normalized_name in self._telnet_clients or normalized_name in self._ftp_hosts:
            raise ValueError(f"connection object already registered in this run: {normalized_name}")
        defaults = defaults or FTPDefaults()
        if not isinstance(defaults, FTPDefaults):
            raise TypeError("defaults must be FTPDefaults")
        values = self._collect_values(
            object_label=f"FTP {normalized_name}",
            defaults=asdict(defaults),
            history=self._history_for("ftp_hosts", normalized_name),
            supplied=self._supplied_section("ftp_hosts", normalized_name),
            fields=(("host", str, False), ("port", int, False), ("username", str, False), ("password", str, True), ("timeout", float, False), ("passive", bool, False)),
        )
        info = FTPConnectionInfo(**values)
        host = FTPHost(
            name=normalized_name,
            info=info,
            run_id=self.run_id,
            package_dir=self.package_dir,
            recorder=self.recorder,
            image_pattern_for=self.image_pattern_for,
        )
        self._ftp_hosts[normalized_name] = host
        self.params["ftp_hosts"][normalized_name] = dict(values)  # type: ignore[index]
        self._save_params()
        self.recorder.log(f"REGISTER FTP name={normalized_name} values={mask_sensitive(values)}")
        return host

    def download_package(self, selector: PackageSelector):
        if not isinstance(selector, PackageSelector):
            raise TypeError("download_package() accepts package() only")
        spec = self.package_manager.get_spec(selector.config_name)
        history = self._history_for("packages", selector.config_name)

        if self.mode == RunMode.RERUN:
            if not history:
                raise LastRunParameterError(
                    f"last-run has no package path selection for {selector.config_name!r}"
                )
            path_mode = str(history.get("path_mode", ""))
            path_override = history.get("path_override")
            override = str(path_override).strip() if path_override else None
            if path_mode not in {"override", "link", "base_link_newest"}:
                raise LastRunParameterError(
                    f"last-run has invalid path_mode for {selector.config_name!r}: {path_mode!r}"
                )
            if path_mode == "override" and not override:
                raise LastRunParameterError(
                    f"last-run override mode has no path for {selector.config_name!r}"
                )
            if path_mode == "link" and not spec.link:
                raise LastRunParameterError(
                    f"last-run selected config link for {selector.config_name!r}, "
                    "but config.json no longer defines link"
                )
            if path_mode == "base_link_newest" and not spec.base_link:
                raise LastRunParameterError(
                    f"last-run selected base_link for {selector.config_name!r}, "
                    "but config.json no longer defines base_link"
                )
        elif self.non_interactive:
            supplied = self._supplied_section("packages", selector.config_name)
            raw_override = supplied.get("path_override", supplied.get("link"))
            override = str(raw_override).strip() if raw_override not in (None, "") else None
            requested_mode = supplied.get("path_mode")
            if requested_mode:
                path_mode = str(requested_mode)
            elif override:
                path_mode = "override"
            elif spec.link:
                path_mode = "link"
            elif spec.base_link:
                path_mode = "base_link_newest"
            else:
                raise ValueError(f"package {selector.config_name} requires a remote directory")
        else:
            historical_override = history.get("path_override") if history else None
            default_override = str(historical_override).strip() if historical_override else None
            automatic = spec.link or (
                f"{spec.base_link} (automatic newest)" if spec.base_link else "<required>"
            )
            shown_default = default_override or automatic
            newest_hint = (
                f"; {PACKAGE_NEWEST_SHORTCUT}: {spec.base_link} (automatic newest)"
                if spec.base_link
                else ""
            )
            answer = self.input_func(
                f"Package {selector.config_name} remote directory "
                f"[default: {shown_default}{newest_hint}]: "
            ).strip()
            use_newest = answer.lower() == PACKAGE_NEWEST_SHORTCUT
            override = None if use_newest else (answer or default_override)
            if use_newest and not spec.base_link:
                raise ValueError(
                    f"package {selector.config_name} does not define base_link; "
                    f"{PACKAGE_NEWEST_SHORTCUT} is unavailable"
                )
            if not override and not spec.link and not spec.base_link:
                raise ValueError(
                    f"package {selector.config_name} requires a remote directory"
                )
            if use_newest:
                path_mode = "base_link_newest"
            elif override:
                path_mode = "override"
            elif spec.link:
                path_mode = "link"
            else:
                path_mode = "base_link_newest"

        package_values = {"path_mode": path_mode, "path_override": override}
        self.params["packages"][selector.config_name] = package_values  # type: ignore[index]
        self._save_params()
        self.recorder.log(
            f"PACKAGE PATH name={selector.config_name} mode={path_mode} override={override!r}"
        )
        return self.package_manager.download(
            selector, path_override=override, path_mode=path_mode
        )

    def argument(self, name: str, *, default: object = None, required: bool = False) -> object:
        normalized = self._validate_object_name(name, "argument")
        supplied = self.supplied_params.get("arguments", {})
        if not isinstance(supplied, dict):
            raise ValueError("parameters.arguments must be an object")
        history = self._history_for("arguments", normalized)
        value = supplied.get(normalized, history.get("value", default))
        if self.non_interactive:
            if required and value in (None, ""):
                raise ValueError(f"required script argument is missing: {normalized}")
        else:
            shown = "" if value is None else str(value)
            entered = self.input_func(f"Argument {normalized} [default: {shown}]: ")
            if entered != "":
                value = entered
            if required and value in (None, ""):
                raise ValueError(f"required script argument is missing: {normalized}")
        self.params["arguments"][normalized] = {"value": value}  # type: ignore[index]
        self._save_params()
        return value

    def extract_file_from(
        self,
        source: LocalFileSelector,
        *,
        target_file: str | None = None,
        target_dir: str | None = None,
    ):
        return self.extractor.extract(
            source, target_file=target_file, target_dir=target_dir
        )

    def resolve_local_file(self, selector: LocalFileSelector) -> ResolvedLocalFile:
        return resolve_local_file(
            selector, self.package_dir, self.image_pattern_for
        )

    def generate_sh_file(self, file_name: str, script: str) -> Path:
        return generate_sh_file(
            file_name,
            script,
            output_dir=self.package_dir,
            uploaded_files=self._uploaded_files,
        )

    @property
    def package_manager(self):
        if self._package_manager is None:
            from .package_manager import PackageManager

            self._package_manager = PackageManager(
                config_path=self.root_dir / "config.json",
                package_dir=self.package_dir,
                run_id=self.run_id,
                recorder=self.recorder,
                hdfs_client=self._hdfs_client,
            )
        return self._package_manager

    @property
    def extractor(self):
        if self._extractor is None:
            from .extractor import Extractor

            self._extractor = Extractor(
                package_dir=self.package_dir,
                run_id=self.run_id,
                recorder=self.recorder,
                image_pattern_for=self.image_pattern_for,
            )
        return self._extractor

    def image_pattern_for(self, name: str) -> str:
        return self.package_manager.image_pattern_for(name)

    def commit_last_run(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.recorder.write_json(self.last_run_path, self.params)
        self.recorder.log(f"LAST RUN UPDATED path={self.last_run_path}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for connection in [*self._ssh_hosts.values(), *self._telnet_clients.values(), *self._ftp_hosts.values()]:
            try:
                connection.close()  # type: ignore[attr-defined]
            except Exception as exc:  # pragma: no cover - defensive cleanup
                self.recorder.log(f"CLOSE ERROR {type(exc).__name__}: {exc}")

    def finish_recording(self) -> None:
        self.recorder.close()

    def _history_for(self, section: str, name: str) -> dict[str, object]:
        raw_section = self._last_params.get(section, {})
        if not isinstance(raw_section, dict):
            return {}
        raw_value = raw_section.get(name, {})
        return raw_value if isinstance(raw_value, dict) else {}

    def _collect_values(
        self,
        *,
        object_label: str,
        defaults: dict[str, object],
        history: dict[str, object],
        supplied: dict[str, object] | None = None,
        fields: tuple[tuple[str, type, bool], ...],
    ) -> dict[str, object]:
        values: dict[str, object] = {}
        supplied = supplied or {}
        for field_name, converter, secret in fields:
            default = history.get(field_name, defaults.get(field_name))
            if self.mode == RunMode.RERUN:
                if field_name not in history:
                    raise LastRunParameterError(
                        f"last-run is missing {object_label}.{field_name}"
                    )
                raw = history[field_name]
            elif self.non_interactive:
                raw = supplied.get(field_name, default)
                if raw is None:
                    raise LastRunParameterError(f"non-interactive input is missing {object_label}.{field_name}")
            else:
                display = "saved value" if secret and default not in (None, "") else str(default or "")
                prompt = f"{object_label} {field_name} [default: {display}]: "
                entered = self.password_input(prompt) if secret else self.input_func(prompt)
                raw = entered if entered != "" else default
            try:
                if converter is str:
                    values[field_name] = "" if raw is None else str(raw)
                elif converter is bool:
                    if isinstance(raw, bool):
                        values[field_name] = raw
                    elif str(raw).strip().lower() in {"1", "true", "yes", "on"}:
                        values[field_name] = True
                    elif str(raw).strip().lower() in {"0", "false", "no", "off"}:
                        values[field_name] = False
                    else:
                        raise ValueError("expected a boolean")
                else:
                    values[field_name] = converter(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid value for {object_label}.{field_name}: {raw!r}") from exc
        return values

    def _supplied_section(self, section: str, name: str) -> dict[str, object]:
        raw_section = self.supplied_params.get(section, {})
        if not isinstance(raw_section, dict):
            raise ValueError(f"parameters.{section} must be an object")
        raw = raw_section.get(name, {})
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            if section == "packages":
                return {"path_override": raw}
            raise ValueError(f"parameters.{section}.{name} must be an object")
        return raw

    @staticmethod
    def _validate_object_name(name: str, label: str) -> str:
        if not isinstance(name, str):
            raise TypeError(f"{label} name must be a string")
        normalized = name.strip()
        if not normalized:
            raise ValueError(f"{label} name must not be empty")
        return normalized

    def _save_params(self) -> None:
        self.recorder.write_json(self.params_path, self.params)

    def _cleanup_historical_packages(self) -> None:
        candidates: list[tuple[float, Path, int]] = []
        total = 0
        for run_dir in self.logs_dir.iterdir():
            packages = run_dir / "packages"
            if not packages.is_dir() or packages == self.package_dir:
                continue
            size = sum(item.stat().st_size for item in packages.rglob("*") if item.is_file())
            total += size
            candidates.append((run_dir.stat().st_mtime, packages, size))
        for _, packages, size in sorted(candidates, key=lambda item: item[0]):
            if total <= self.package_cache_limit:
                break
            shutil.rmtree(packages)
            packages.mkdir(parents=True, exist_ok=True)
            total -= size
            self.recorder.log(f"PACKAGE CACHE CLEANED path={packages} bytes={size}")
