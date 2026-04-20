from typing import Dict, List

from models import EnvironmentSpec


ENV_REGISTRY: Dict[str, EnvironmentSpec] = {
    "A_ENV_RUN": EnvironmentSpec(
        env_name="A_ENV_RUN",
        image_vars={
            "A1_image": "A1",
            "A2_image": "A2",
            "A3_image": "A3",
        },
        script_template="""#!/bin/bash
set -e
cd /root/autoEnv

echo "Start A environment"
chmod +x ${A1_image}
chmod +x ${A2_image}

echo "Use package ${A1_image}"
echo "Use package ${A2_image}"
echo "Use package ${A3_image}"

# TODO: 填写真实环境启动命令
""",
    ),
    "B_ENV_RUN": EnvironmentSpec(
        env_name="B_ENV_RUN",
        image_vars={
            "A1_image": "A1",
            "A2_image": "A2",
        },
        script_template="""#!/bin/bash
set -e
cd /root/autoEnv

echo "Start B environment"
echo "B use ${A1_image}"
echo "B use ${A2_image}"

# TODO: 填写 B 环境启动命令
""",
    ),
}

# 组合环境示例：按数组顺序依次启动已有环境脚本。
COMPOSITE_ENV_REGISTRY: Dict[str, List[str]] = {
    "A_B_CHAIN_RUN": ["A_ENV_RUN", "B_ENV_RUN"],
}

# SSH 默认值，可按环境覆盖。
SSH_DEFAULTS: Dict[str, str | int] = {
    "username": "root",
    "password": "root",
    "port": 22,
}

ENV_SSH_DEFAULTS: Dict[str, Dict[str, str | int]] = {
    # "A_ENV_RUN": {"username": "root", "password": "root", "port": 22},
}


def list_env_names() -> list[str]:
    return sorted(ENV_REGISTRY.keys())


def get_env(env_name: str) -> EnvironmentSpec:
    return ENV_REGISTRY[env_name]


def list_composite_env_names() -> list[str]:
    return sorted(COMPOSITE_ENV_REGISTRY.keys())


def get_composite_env(name: str) -> List[str]:
    return COMPOSITE_ENV_REGISTRY[name]


def get_ssh_defaults(env_name: str) -> Dict[str, str | int]:
    merged = dict(SSH_DEFAULTS)
    merged.update(ENV_SSH_DEFAULTS.get(env_name, {}))
    return merged
