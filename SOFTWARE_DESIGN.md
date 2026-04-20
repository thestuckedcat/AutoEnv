# AutoEnv 软件设计说明

## 1. 目标与范围

本项目目标是将“拉包 + 脚本渲染 + 上传服务器”流程通用化，使用户通过配置即可启动不同环境。

核心能力：
- 通过 `config.json` 管理镜像来源与正则匹配规则；
- 通过 `env_config.py` 注册环境（依赖包 + 启动脚本模板）；
- 执行时自动选择环境、解析依赖、下载真实包、替换模板变量、上传文件。

## 2. 总体架构

模块分层：

1. **配置层**
   - `config.json`：镜像配置清单（name/link/image_name）。
   - `env_config.py`：环境注册（env_name/image_vars/script_template）。

2. **核心逻辑层**
   - `config_loader.py`：读取并校验镜像配置。
   - `renderer.py`：模板变量替换与未替换变量检查。
   - `main.py`：主流程编排（选择环境 -> 拉包 -> 渲染 -> 上传）。

3. **基础设施层**
   - `tools.py`：
     - `HDFSClient`：WebHDFS 列目录、自动 newest、正则选包、下载；
     - `upload_files_via_scp`：SSH/SCP 上传文件。
   - `logger.py`：统一日志初始化到控制台 + 文件。

## 3. 数据模型

`models.py` 定义：
- `ImageSpec`：单个包规则（name/link/image_name）。
- `EnvironmentSpec`：环境定义（env_name/image_vars/script_template）。
- `FileEntry`：WebHDFS 文件项。
- `DownloadedImage`：运行时每个变量最终解析结果。
- `RuntimeContext`：环境执行上下文（预留扩展）。

## 4. 配置设计

### 4.1 config.json

格式为数组，每项：

- `name`（必填，唯一）
- `link`（可空；为空时自动最新路径）
- `image_name`（必填，正则表达式）

示例：

```json
{
  "name": "A1",
  "link": "",
  "image_name": "^HN922-driver-[\\w.-]+-rtos[\\w.-]+\\.aarch64-debug\\.run$"
}
```

### 4.2 env_config.py

每个环境包含：
- `env_name`：环境名（供 UI 选择）
- `image_vars`：模板变量名 -> config.name
- `script_template`：shell 模板，变量使用 `${A1_image}`

## 5. 核心流程

1. 启动后列出所有环境并让用户选择。
2. 加载 `config.json` 并校验。
3. 遍历环境依赖：
   - 按 `name` 找 `ImageSpec`；
   - `link` 为空时自动 newest；
   - 用 `image_name` 正则筛选真实包名；
   - 下载到 `runtime/`。
4. 将真实包名写入变量字典。
5. 用 `renderer.render_script` 替换脚本模板。
6. 生成 `runtime/<ENV>_<ts>.sh`。
7. 询问目标 host（22/root/root 默认）。
8. 把脚本和包上传到 `/root/autoEnv`。

## 6. 错误处理

- 配置错误（缺字段、name 重复）在加载阶段直接抛出。
- 环境引用不存在的 name 直接报错终止。
- 正则未匹配到文件时抛 `FileNotFoundError`。
- 脚本变量未替换完时 `render_script` 抛错。
- 上传失败抛出异常并记录日志。

## 7. 日志设计

- 日志输出到：
  - 控制台（实时）
  - `logs/autoenv_YYYYMMDD_HHMMSS.log`
- 关键节点记录：环境选择、每个包下载、脚本生成、上传开始/完成。

## 8. 扩展点（后续迭代）

- 支持每个环境单独 SSH 凭据。
- 支持“上传后自动远程执行 + 回收日志”。
- 增加包缓存与 MD5 校验。
- 增加 `--env A_ENV_RUN --host x.x.x.x` CLI 非交互模式。
- 增加单元测试（mock requests/paramiko）。

## 9. 交付物

- 可运行代码骨架已提供：
  - `main.py`
  - `tools.py`
  - `config_loader.py`
  - `renderer.py`
  - `env_config.py`
  - `logger.py`
  - `models.py`
  - `config.json`
- 文档：`README.md`、`SOFTWARE_DESIGN.md`
