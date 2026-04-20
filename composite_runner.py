from typing import Sequence

from env_config import get_composite_env
from env_executor import execute_environment


def run_composite_environments(env_sequence: Sequence[str]) -> None:
    """组合执行：按数组顺序依次执行多个已注册环境。"""
    if not env_sequence:
        raise ValueError("env_sequence 不能为空")

    print("=== 开始组合环境执行 ===")
    for index, env_name in enumerate(env_sequence, start=1):
        print(f"\n[{index}/{len(env_sequence)}] 执行子环境: {env_name}")
        run_dir, script_name = execute_environment(env_name, runtime_suffix=env_name)
        print(f"✅ 子环境 {env_name} 完成：{run_dir}/{script_name}")

    print("\n🎉 组合环境执行完成")


if __name__ == "__main__":
    # 示例：按顺序组合执行多个环境。
    # 你可以按需改成自己的环境名数组。
    run_composite_environments(get_composite_env("A_B_CHAIN_RUN"))
