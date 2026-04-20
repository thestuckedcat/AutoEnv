from typing import Dict

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
}


def list_env_names() -> list[str]:
    return sorted(ENV_REGISTRY.keys())


def get_env(env_name: str) -> EnvironmentSpec:
    return ENV_REGISTRY[env_name]
