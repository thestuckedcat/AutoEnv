from __future__ import annotations

from env_common import ENV_REGISTER, EnvironmentSpec, default_environment_process


@ENV_REGISTER("A_ENV_RUN")
def build_env(env_name: str) -> EnvironmentSpec:
    return EnvironmentSpec(
        env_name=env_name,
        image_vars={
            # 两字段：脚本变量名 -> config.json name。
            "A1_image": "A1",
            "A2_image": "A2",
            "A3_image": "A3",
            # 三字段：脚本变量名 -> (config.json name, target_file)。
            # "hn922_drv": ("UnionS_SDK_Drv", "file1"),
        },
        script_templates={
            "main": """#!/bin/bash
set -e
cd /root/autoEnv

echo "Start A environment"
chmod +x ${A1_image}
chmod +x ${A2_image}

echo "Use package ${A1_image}"
echo "Use package ${A2_image}"
echo "Use package ${A3_image}"

# TODO: 填写真实环境启动命令
"""
        },
        process=default_environment_process,
        telnet_commands=[],
        ssh_defaults={"username": "root", "password": "root", "host": "192.168.1.100"},
        telnet_defaults={"host": "192.168.1.100", "port": 23},
        ftp_defaults={"username": "root", "password": "root"},
    )
