from env_config import list_env_names
from env_executor import execute_environment


def choose_environment() -> str:
    envs = list_env_names()
    if not envs:
        raise RuntimeError("没有已注册环境")

    print("可选环境：")
    for idx, name in enumerate(envs, start=1):
        print(f"  {idx}. {name}")

    while True:
        value = input("请选择环境编号: ").strip()
        if not value.isdigit():
            print("请输入数字编号")
            continue
        i = int(value)
        if i < 1 or i > len(envs):
            print("编号超出范围")
            continue
        return envs[i - 1]


def main() -> None:
    selected = choose_environment()
    execute_environment(selected)


if __name__ == "__main__":
    main()
