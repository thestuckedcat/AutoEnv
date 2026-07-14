from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Sequence
from urllib.parse import unquote

from .recorder import RunRecorder
from .results import DownloadResult
from .selectors import PackageSelector


DEFAULT_HDFS_BASE_URL = "https://hdfs-ngx1.turing-ci.hisilicon.com"


@dataclass(frozen=True)
class PackageSpec:
    """One validated entry from the legacy top-level config array."""

    name: str
    link: str
    base_link: str
    image_name: str
    # Kept solely for config compatibility. Download and extraction never use it.
    target_file: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HDFSFileEntry:
    """The WebHDFS metadata needed by package selection."""

    name: str
    is_directory: bool
    length: int
    modification_time: datetime | None
    full_path: str = ""


class HDFSClientProtocol(Protocol):
    """Small client surface used by PackageManager and by test fakes."""

    def list_directory(self, path: str) -> Sequence[object]: ...

    def download_file(self, remote_path: str, local_path: str) -> None: ...


class HDFSClient:
    """Minimal WebHDFS client preserving the project's existing URL semantics."""

    def __init__(
        self,
        base_url: str = DEFAULT_HDFS_BASE_URL,
        *,
        verify_ssl: bool = False,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must not be empty")
        self.base_url = base_url.strip().rstrip("/")
        self.verify_ssl = verify_ssl
        self._session: Any | None = None

    @property
    def session(self) -> Any:
        # Import lazily so config parsing and fake-client tests do not require requests.
        if self._session is None:
            import requests
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self._session = requests.Session()
        return self._session

    def list_directory(self, path: str) -> list[HDFSFileEntry]:
        url = f"{self.base_url}/webhdfs/v1{path}?op=LISTSTATUS&user.name=hadoop"
        response = self.session.get(url, timeout=15, verify=self.verify_ssl)
        response.raise_for_status()
        statuses = response.json().get("FileStatuses", {}).get("FileStatus", [])
        if isinstance(statuses, dict):
            statuses = [statuses]

        entries: list[HDFSFileEntry] = []
        for item in statuses:
            modification_ms = item.get("modificationTime", 0)
            name = item.get("pathSuffix", "")
            if not name:
                continue
            entries.append(
                HDFSFileEntry(
                    name=name,
                    is_directory=item.get("type") == "DIRECTORY",
                    length=int(item.get("length", 0)),
                    modification_time=(
                        datetime.fromtimestamp(modification_ms / 1000)
                        if modification_ms
                        else None
                    ),
                    full_path=unquote(item.get("path", "")),
                )
            )
        return entries

    def download_file(self, remote_path: str, local_path: str) -> None:
        url = f"{self.base_url}/webhdfs/v1{remote_path}?op=OPEN&user.name=hadoop"
        with self.session.get(
            url, stream=True, timeout=60, verify=self.verify_ssl
        ) as response:
            response.raise_for_status()
            with open(local_path, "wb") as handle:
                for chunk in response.iter_content(8192):
                    if chunk:
                        handle.write(chunk)


class _RemoteLookupError(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


class PackageManager:
    """Load package rules, resolve the legacy newest layout, and download safely."""

    def __init__(
        self,
        config_path: Path | str,
        package_dir: Path | str,
        run_id: str,
        recorder: RunRecorder,
        hdfs_client: HDFSClientProtocol | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.package_dir = Path(package_dir).resolve()
        self.run_id = run_id
        self.recorder = recorder
        self.hdfs_client = hdfs_client if hdfs_client is not None else HDFSClient()
        self._specs = self._load_specs(self.config_path)

    def get_spec(self, name: str) -> PackageSpec:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("package name must not be empty")
        normalized = name.strip()
        try:
            return self._specs[normalized]
        except KeyError as exc:
            raise KeyError(f"package config does not exist: {normalized}") from exc

    def image_pattern_for(self, name: str) -> str:
        """Return the already validated image-name regex for a package config."""

        return self.get_spec(name).image_name

    def download(
        self,
        selector: PackageSelector,
        path_override: str | None = None,
        path_mode: str | None = None,
    ) -> DownloadResult:
        """Download one package without raising for runtime failures."""

        if not isinstance(selector, PackageSelector):
            raise TypeError("download() accepts PackageSelector only")
        if path_override is not None and not isinstance(path_override, str):
            raise TypeError("path_override must be a string or None")
        if path_mode is not None and path_mode not in {
            "override",
            "link",
            "base_link_newest",
        }:
            raise ValueError(f"invalid package path_mode: {path_mode!r}")

        spec = self.get_spec(selector.config_name)
        override = path_override.strip().rstrip("/") if path_override else None
        selected_mode = path_mode or (
            "override" if override else ("link" if spec.link else "base_link_newest")
        )
        if selected_mode == "override" and not override:
            raise ValueError("override path_mode requires path_override")
        if selected_mode == "link" and not spec.link:
            raise ValueError(f"package {spec.name} does not define link")
        if selected_mode == "base_link_newest" and not spec.base_link:
            raise ValueError(
                f"package {spec.name} does not define base_link; provide a remote directory override"
            )
        operation_id = self.recorder.next_operation_id()
        started_at = datetime.now().astimezone()

        remote_dir: str | None = None
        remote_file: str | None = None
        remote_size: int | None = None
        remote_modified_at: datetime | None = None
        local_file: Path | None = None
        local_size: int | None = None
        local_existed = False
        local_md5_before: str | None = None
        local_md5_after: str | None = None
        md5_changed: bool | None = None
        size_verified = False
        part_file: Path | None = None

        def result(
            *,
            success: bool,
            status: str,
            error: BaseException | None = None,
            error_type: str | None = None,
        ) -> DownloadResult:
            finished_at = datetime.now().astimezone()
            value = DownloadResult(
                run_id=self.run_id,
                operation_id=operation_id,
                success=success,
                status=status,
                config_name=spec.name,
                image_pattern=spec.image_name,
                package_dir=str(self.package_dir),
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(
                    0, int((finished_at - started_at).total_seconds() * 1000)
                ),
                remote_dir=remote_dir,
                remote_file=remote_file,
                remote_size=remote_size,
                remote_modified_at=remote_modified_at,
                local_file=str(local_file) if local_file is not None else None,
                local_size=local_size,
                local_existed=local_existed,
                local_md5_before=local_md5_before,
                local_md5_after=local_md5_after,
                md5_changed=md5_changed,
                size_verified=size_verified,
                error_type=error_type or (type(error).__name__ if error else None),
                error_message=str(error) if error else None,
            )
            self.recorder.record_result("DOWNLOAD", value)
            return value

        try:
            self.package_dir.mkdir(parents=True, exist_ok=True)
            try:
                remote_dir, entry = self._resolve_remote(spec, override, selected_mode)
            except _RemoteLookupError as exc:
                return result(
                    success=False,
                    status=exc.status,
                    error=exc,
                    error_type=exc.status.upper(),
                )
            except Exception as exc:
                return result(
                    success=False,
                    status="connection_failed",
                    error=exc,
                    error_type="CONNECTION_FAILED",
                )

            entry_name = _entry_name(entry)
            if Path(entry_name).name != entry_name or "/" in entry_name or "\\" in entry_name:
                return result(
                    success=False,
                    status="download_failed",
                    error=ValueError(f"invalid remote filename: {entry_name!r}"),
                    error_type="INVALID_REMOTE_FILENAME",
                )

            remote_size = _entry_length(entry)
            remote_modified_at = _entry_modified_at(entry)
            remote_file = f"{remote_dir.rstrip('/')}/{entry_name}"
            local_file = self.package_dir / entry_name
            part_file = local_file.with_name(local_file.name + ".part")
            local_existed = local_file.is_file()
            if local_existed:
                try:
                    local_md5_before = _file_md5(local_file)
                except OSError as exc:
                    return result(
                        success=False,
                        status="local_replace_failed",
                        error=exc,
                        error_type="LOCAL_FILE_READ_FAILED",
                    )

            try:
                part_file.unlink(missing_ok=True)
                self.hdfs_client.download_file(remote_file, str(part_file))
            except Exception as exc:
                status = "download_timeout" if _is_timeout(exc) else "download_failed"
                return result(
                    success=False,
                    status=status,
                    error=exc,
                    error_type=status.upper(),
                )

            try:
                local_size = part_file.stat().st_size
            except OSError as exc:
                return result(
                    success=False,
                    status="download_failed",
                    error=exc,
                    error_type="DOWNLOADED_FILE_NOT_FOUND",
                )
            size_verified = local_size == remote_size
            if not size_verified:
                return result(
                    success=False,
                    status="size_verification_failed",
                    error=ValueError(
                        f"downloaded size {local_size} does not match remote size {remote_size}"
                    ),
                    error_type="SIZE_VERIFICATION_FAILED",
                )

            try:
                local_md5_after = _file_md5(part_file)
                os.replace(part_file, local_file)
            except OSError as exc:
                return result(
                    success=False,
                    status="local_replace_failed",
                    error=exc,
                    error_type="LOCAL_REPLACE_FAILED",
                )

            md5_changed = local_md5_before != local_md5_after
            return result(success=True, status="success")
        except Exception as exc:
            # Configuration and call-shape errors are validated above. Everything
            # reached here is an operational failure and must be returned.
            return result(
                success=False,
                status="download_failed",
                error=exc,
                error_type="DOWNLOAD_FAILED",
            )
        finally:
            if part_file is not None:
                try:
                    part_file.unlink(missing_ok=True)
                except OSError:
                    # Cleanup failure must not hide the operation's actual result.
                    pass

    def _resolve_remote(
        self, spec: PackageSpec, override: str | None, path_mode: str
    ) -> tuple[str, object]:
        if path_mode in {"override", "link"}:
            remote_dir = (override if path_mode == "override" else spec.link).rstrip("/")
            try:
                entries = self.hdfs_client.list_directory(remote_dir)
            except FileNotFoundError as exc:
                raise _RemoteLookupError(
                    "remote_directory_not_found",
                    f"remote directory does not exist: {remote_dir}",
                ) from exc
            match = _latest_matching_file(entries, spec.image_name)
            if match is None:
                raise _RemoteLookupError(
                    "remote_file_not_found",
                    f"no file in {remote_dir} matched {spec.image_name!r}",
                )
            return remote_dir, match

        root = spec.base_link.rstrip("/")
        try:
            level_one = self.hdfs_client.list_directory(root)
        except FileNotFoundError as exc:
            raise _RemoteLookupError(
                "remote_directory_not_found",
                f"remote directory does not exist: {root}",
            ) from exc

        direct_directories = [item for item in level_one if _entry_is_directory(item)]
        if not direct_directories:
            raise _RemoteLookupError(
                "newest_directory_not_found",
                f"no direct child directory exists under {root}",
            )
        latest = max(direct_directories, key=_entry_sort_time)
        latest_dir = f"{root}/{_entry_name(latest)}"
        try:
            level_two = self.hdfs_client.list_directory(latest_dir)
        except FileNotFoundError as exc:
            raise _RemoteLookupError(
                "newest_directory_not_found",
                f"latest directory disappeared while resolving package: {latest_dir}",
            ) from exc
        newest = [
            item
            for item in level_two
            if _entry_is_directory(item) and "newest" in _entry_name(item).lower()
        ]
        newest.sort(key=_entry_sort_time, reverse=True)
        if not newest:
            raise _RemoteLookupError(
                "newest_directory_not_found",
                f"no newest directory exists under {latest_dir}",
            )

        available_candidate = False
        for candidate in newest:
            candidate_dir = f"{latest_dir}/{_entry_name(candidate)}"
            try:
                entries = self.hdfs_client.list_directory(candidate_dir)
            except FileNotFoundError:
                continue
            available_candidate = True
            match = _latest_matching_file(entries, spec.image_name)
            if match is not None:
                return candidate_dir, match
        if not available_candidate:
            raise _RemoteLookupError(
                "newest_directory_not_found",
                f"all newest candidates disappeared under {latest_dir}",
            )
        raise _RemoteLookupError(
            "remote_file_not_found",
            f"no newest candidate under {latest_dir} contained a file matching {spec.image_name!r}",
        )

    @staticmethod
    def _load_specs(config_path: Path) -> dict[str, PackageSpec]:
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON config: {config_path}: {exc}") from exc
        if not isinstance(raw, list):
            raise ValueError("config.json top level must be an array")

        specs: dict[str, PackageSpec] = {}
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise TypeError(f"config entry {index} must be an object")
            name = _config_text(item.get("name"), f"config entry {index} name")
            link = _config_optional_text(item.get("link", ""), f"config {name} link")
            base_link = _config_optional_text(
                item.get("base_link", ""), f"config {name} base_link"
            )
            image_name = _config_text(
                item.get("image_name"), f"config {name} image_name"
            )
            try:
                re.compile(image_name)
            except re.error as exc:
                raise ValueError(f"config {name} has invalid image_name regex: {exc}") from exc
            if name in specs:
                raise ValueError(f"duplicate package config name: {name}")
            specs[name] = PackageSpec(
                name=name,
                link=link.rstrip("/"),
                base_link=base_link.rstrip("/"),
                image_name=image_name,
                target_file=_parse_target_file(item.get("target_file", []), name),
            )
        return specs


def _config_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _config_optional_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value.strip()


def _parse_target_file(value: object, config_name: str) -> list[str]:
    # This mirrors the old loader, including its permissive string conversion.
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    raise ValueError(f"config {config_name} target_file must be a string or array")


def _entry_value(entry: object, name: str, default: object = None) -> object:
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _entry_name(entry: object) -> str:
    value = _entry_value(entry, "name", _entry_value(entry, "pathSuffix", ""))
    return str(value)


def _entry_is_directory(entry: object) -> bool:
    value = _entry_value(entry, "is_directory", None)
    if value is not None:
        return bool(value)
    return _entry_value(entry, "type", "") == "DIRECTORY"


def _entry_length(entry: object) -> int:
    return int(_entry_value(entry, "length", 0))


def _entry_modified_at(entry: object) -> datetime | None:
    value = _entry_value(
        entry, "modification_time", _entry_value(entry, "modificationTime", None)
    )
    if isinstance(value, datetime) or value is None:
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000)
    raise TypeError(f"unsupported modification time: {value!r}")


def _entry_sort_time(entry: object) -> float:
    modified_at = _entry_modified_at(entry)
    return modified_at.timestamp() if modified_at is not None else float("-inf")


def _latest_matching_file(entries: Sequence[object], pattern: str) -> object | None:
    regex = re.compile(pattern)
    matches = [
        item
        for item in entries
        if not _entry_is_directory(item) and regex.search(_entry_name(item))
    ]
    return max(matches, key=_entry_sort_time) if matches else None


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
