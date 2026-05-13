from __future__ import annotations

from env_common import ENV_REGISTER, EnvironmentSpec, default_environment_process


@ENV_REGISTER("B_ENV_RUN")
def build_env(env_name: str) -> EnvironmentSpec:
    return EnvironmentSpec(
        env_name=env_name,
        image_vars={
            "A1_image": "A1",
            "A2_image": "A2",
        },
        script_templates={
            "main": """#!/bin/bash
set -e
cd /root/autoEnv

echo "Start B environment"
echo "B use ${A1_image}"
echo "B use ${A2_image}"

# TODO: 填写 B 环境启动命令
"""
        },
        process=default_environment_process,
        telnet_commands=[],
        ssh_defaults={"username": "root", "password": "root", "host": "192.168.1.100"},
        telnet_defaults={"host": "192.168.1.100", "port": 23},
        ftp_defaults={"username": "root", "password": "root"},
    )
