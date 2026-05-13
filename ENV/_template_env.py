"""Copy this file to `<env_name>.py` when adding a new environment.

Files starting with `_` are not auto-imported by env_config, so this module is a
safe template and will not register an environment by itself.
"""

from __future__ import annotations

from env_common import ENV_REGISTER, EnvironmentSpec, default_environment_process


# @ENV_REGISTER("MY_ENV_RUN")
def build_env(env_name: str) -> EnvironmentSpec:
    return EnvironmentSpec(
        env_name=env_name,
        image_vars={
            "pkg_var": "ConfigJsonName",
            "target_file_var": ("ConfigJsonName", "file_inside_package"),
        },
        script_templates={
            "main": """#!/bin/bash
set -e
cd /root/autoEnv

echo "Use ${pkg_var} and ${target_file_var}"
"""
        },
        process=default_environment_process,
    )
