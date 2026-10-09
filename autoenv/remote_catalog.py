from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def safe_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value in {".", ".."}:
        raise ValueError("name must be a nonempty string")
    if value != value.strip() or any(char in value for char in '\\/:*?"<>|\x00'):
        raise ValueError("name must be a plain directory/connection name")
    return value


def validate_connection(value: object) -> dict:
    from .ftp_host import FTPConnectionInfo
    from .ssh_host import SSHConnectionInfo
    from .telnet_client import TelnetConnectionInfo

    if not isinstance(value, dict):
        raise TypeError("connection must be an object")
    config = dict(value)
    config["name"] = safe_name(config.get("name"))
    kind = config.get("type")
    if kind not in {"ssh", "telnet", "ftp"}:
        raise ValueError("connection type must be ssh, telnet or ftp")
    if not isinstance(config.get("enabled", True), bool):
        raise TypeError("enabled must be a boolean")
    config["enabled"] = config.get("enabled", True)
    if kind == "ssh":
        info = SSHConnectionInfo(**{key: config[key] for key in ("host", "port", "username", "password", "connect_timeout") if key in config})
        config["upload_type"] = config.get("upload_type", "scp")
        if config["upload_type"] not in {"scp", "sftp"}:
            raise ValueError("SSH upload_type must be scp or sftp")
    elif kind == "telnet":
        info = TelnetConnectionInfo(**{key: config[key] for key in ("host", "port", "timeout", "shell_mode") if key in config})
        for key in ("username", "password", "probe_command", "probe_keyword", "login_prompt", "password_prompt"):
            if key in config and not isinstance(config[key], str):
                raise TypeError(f"{key} must be a string")
        if config.get("password") and not config.get("username"):
            raise ValueError("Telnet password requires username")
    else:
        info = FTPConnectionInfo(**{key: config[key] for key in ("host", "port", "username", "password", "timeout", "passive") if key in config})
    from dataclasses import asdict

    config.update(asdict(info))
    return config


def revision(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def read_catalog(root: Path | str) -> dict[str, dict[str, dict]]:
    directory = Path(root) / "environments" / "topologies"
    result = {}
    if not directory.exists():
        return result
    for folder in sorted(directory.iterdir()):
        if not folder.is_dir():
            continue
        topology = safe_name(folder.name)
        file = folder / "connections.json"
        if not file.is_file():
            continue
        values = json.loads(file.read_text(encoding="utf-8-sig"))
        if not isinstance(values, list):
            raise TypeError(f"connections.json must contain an array: {file}")
        connections = {}
        for value in values:
            config = validate_connection(value)
            name = config["name"]
            if name in connections:
                raise ValueError(f"duplicate connection: {topology}/{name}")
            connections[name] = config
        result[topology] = connections
    return result


def save_connection(root: Path | str, topology: str, config: dict) -> None:
    topology = safe_name(topology)
    validated = validate_connection(config)
    directory = Path(root) / "environments" / "topologies" / topology
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / "connections.json"
    existing = read_catalog(root).get(topology, {})
    existing[validated["name"]] = validated
    fd, temporary = tempfile.mkstemp(prefix=".connections-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(list(existing.values()), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, file)
    finally:
        Path(temporary).unlink(missing_ok=True)


def allowed_pairs(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("allowed_envs must be a nonempty array of (topology, connection) pairs")
    pairs = []
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("each allowed environment must contain two names")
        item = (safe_name(pair[0]), safe_name(pair[1]))
        if item in pairs:
            raise ValueError(f"duplicate allowed environment: {item}")
        pairs.append(item)
    return sorted(pairs)


def compatible_topologies(slots: list[dict], catalog: dict) -> list[str]:
    groups = []
    for slot in slots:
        suitable = set()
        for topology, name in allowed_pairs(slot["allowed_envs"]):
            config = catalog.get(topology, {}).get(name)
            if config is None:
                raise ValueError(f"unknown allowed environment: {topology}/{name}")
            if config["type"] != slot["required_type"]:
                raise ValueError(f"connection type mismatch for {topology}/{name}")
            if config["enabled"]:
                suitable.add(topology)
        groups.append(suitable)
    if not groups:
        return []
    shared = set.intersection(*groups)
    if not shared:
        raise ValueError("no topology satisfies all script environment slots")
    return sorted(shared)
