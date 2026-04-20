# AutoEnv

一个用于**按环境模板自动拉取驱动包、渲染安装脚本并上传到目标服务器**的工具。

## 快速开始

1. 配置 `config.json` 中的镜像规则（name/link/image_name）。
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

## 目录结构

- `main.py`：交互入口。
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
