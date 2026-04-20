# AutoEnv

一个用于**按环境模板自动拉取驱动包、渲染安装脚本并上传到目标服务器**的工具。

## 快速开始

1. 配置 `config.json` 中的镜像规则（name/link/base_link/image_name）。
2. 在 `env_config.py` 注册环境脚本模板与依赖镜像变量。
3. 运行：

```bash
python3 main.py
```

程序会列出所有可用环境，选择后自动：
- 根据 `name` 找到包规则；
- 若 `link` 为空则自动走 `newest` 目录；
- 正则匹配真实包名并下载；
- 将脚本模板中的 `${A1_image}` 变量替换为真实包名；
- 生成并上传 `.sh` + 包到目标机器 `/root/autoEnv`。
- 每次执行都会在 `runtime/YYYYMMDD_HHMMSS/` 下保存本次脚本和下载包；
- `runtime/` 总容量超过 1GB 时，会自动删除最早的历史执行目录（保留本次目录）；
- `logs/autoenv_YYYYMMDD_HHMMSS.log` 与 `runtime/YYYYMMDD_HHMMSS/` 使用同一 run_id，便于一一对应排查；

### link / base_link 优先级交互示例

```text
- A1 路径已配置 link=/a/b/c（直接回车保持现状）
- A1 路径 [默认: /a/b/c]:
# 直接回车 => 继续使用 link=/a/b/c（优先级最高）

- A2 路径 [默认: <自动 newest from base_link=/x/y/z>]:
# 直接回车 => 使用 base_link=/x/y/z，自动在 newest 目录内匹配包

- A3 路径 [默认: <必填: link/base_link 均为空>]:
# 直接回车 => 报错并阻止继续，必须手工输入路径
```

规则总结：
- 手工输入路径 > `link` > `base_link(newest 自动解析)`；
- 当 `link` 和 `base_link` 都为空时，程序会在输入阶段立即阻止继续。

## 目录结构

- `main.py`：交互入口。
- `composite_runner.py`：按数组顺序组合执行多个已注册环境。
- `models.py`：核心数据结构。
- `config_loader.py`：加载并校验 `config.json`。
- `env_config.py`：用户注册环境脚本。
- `renderer.py`：脚本变量替换与渲染。
- `tools.py`：WebHDFS 拉包与 SCP 上传。
- `logger.py`：统一日志初始化。
- `config.json`：镜像规则配置。
- `logs/`：运行日志目录。

## 依赖

```bash
pip install requests urllib3 paramiko scp
```

## 组合环境执行

当你希望把多个已有环境按顺序串起来执行时，可使用 `composite_runner.py`：

```bash
python3 composite_runner.py
```

也可以在代码里直接调用：

```python
from composite_runner import run_composite_environments
run_composite_environments(["A_ENV_RUN", "B_ENV_RUN"])
```

注意：组合执行时，每个子环境都会分别询问包路径（link）和目标服务器。
- `env_config.py` 中提供了组合环境示例 `A_B_CHAIN_RUN = ["A_ENV_RUN", "B_ENV_RUN"]`。


## SSH 默认值配置

可在 `env_config.py` 配置全局或按环境的 SSH 默认值：

- `SSH_DEFAULTS`：全局默认用户名/密码/端口
- `ENV_SSH_DEFAULTS`：按环境覆盖默认值

执行时在输入目标服务器后，会继续询问用户名、密码、端口；如果直接回车，就使用上述默认值。
