from __future__ import annotations

from env_common import register_composite_env


register_composite_env("A_B_CHAIN_RUN", ["A_ENV_RUN", "B_ENV_RUN"])
