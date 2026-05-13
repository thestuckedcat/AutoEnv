from __future__ import annotations

import importlib
from pathlib import Path
from typing import Callable, Dict, List

from models import EnvironmentSpec

ENV_DIR = Path(__file__).resolve().parent / "ENV"

ENV_REGISTRY: Dict[str, EnvironmentSpec] = {}
COMPOSITE_ENV_REGISTRY: Dict[str, List[str]] = {}
_ENV_MODULES_LOADED = False

# 全局兜底默认值；每个 ENV/<env_name>.py 可以通过 EnvironmentSpec.*_defaults 覆盖。
SSH_DEFAULTS: Dict[str, str | int] = {
    "username": "root",
    "password": "root",
    "port": 22,
    "host": "192.168.1.100",
}
TELNET_DEFAULTS: Dict[str, str | int | float] = {
    "host": "192.168.1.100",
    "port": 23,
    "timeout": 30.0,
}
FTP_DEFAULTS: Dict[str, str | int] = {
    "username": "root",
    "password": "root",
    "port": 21,
    "remote_path": "/root/autoEnv",
}


def register_env(env: EnvironmentSpec) -> EnvironmentSpec:
    if env.env_name in ENV_REGISTRY:
        raise ValueError(f"环境重复注册: {env.env_name}")
    ENV_REGISTRY[env.env_name] = env
    return env


def register_composite_env(name: str, env_sequence: List[str]) -> List[str]:
    if name in COMPOSITE_ENV_REGISTRY:
        raise ValueError(f"组合环境重复注册: {name}")
    if not env_sequence:
        raise ValueError(f"组合环境不能为空: {name}")
    COMPOSITE_ENV_REGISTRY[name] = list(env_sequence)
    return COMPOSITE_ENV_REGISTRY[name]


def ENV_REGISTER(env_name: str) -> Callable[[Callable[[str], EnvironmentSpec]], Callable[[str], EnvironmentSpec]]:
    """装饰器式环境注册入口，供 ENV/<env_name>.py 调用。"""

    def decorator(factory: Callable[[str], EnvironmentSpec]) -> Callable[[str], EnvironmentSpec]:
        register_env(factory(env_name))
        return factory

    return decorator


def load_env_modules() -> None:
    """自动导入 ENV 目录下的环境模块，触发 ENV_REGISTER 注册。"""
    global _ENV_MODULES_LOADED
    if _ENV_MODULES_LOADED:
        return
    if not ENV_DIR.exists():
        _ENV_MODULES_LOADED = True
        return

    for module_path in sorted(ENV_DIR.glob("*.py")):
        if module_path.name == "__init__.py" or module_path.stem.startswith("_"):
            continue
        importlib.import_module(f"ENV.{module_path.stem}")
    _ENV_MODULES_LOADED = True


def list_env_names() -> list[str]:
    load_env_modules()
    return sorted(ENV_REGISTRY.keys())


def get_env(env_name: str) -> EnvironmentSpec:
    load_env_modules()
    return ENV_REGISTRY[env_name]


def list_composite_env_names() -> list[str]:
    load_env_modules()
    return sorted(COMPOSITE_ENV_REGISTRY.keys())


def get_composite_env(name: str) -> List[str]:
    load_env_modules()
    return COMPOSITE_ENV_REGISTRY[name]


def get_ssh_defaults(env_name: str) -> Dict[str, str | int]:
    env = get_env(env_name)
    merged = dict(SSH_DEFAULTS)
    merged.update(env.ssh_defaults)
    return merged


def get_telnet_defaults(env_name: str) -> Dict[str, str | int | float]:
    env = get_env(env_name)
    merged = dict(TELNET_DEFAULTS)
    merged.update(env.telnet_defaults)
    return merged


def get_ftp_defaults(env_name: str) -> Dict[str, str | int]:
    env = get_env(env_name)
    merged = dict(FTP_DEFAULTS)
    merged.update(env.ftp_defaults)
    return merged
