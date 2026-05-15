# AutoEnv 函数功能简述与输入输出边界

本文档按模块说明主要显式函数/方法的用途、输入/输出边界与副作用。当前逻辑以 `EnvironmentSpec` 为环境配置核心：环境级 SSH/Telnet/FTP 默认值均写在 `EnvironmentSpec` 内，由 `env_config.get_*_defaults()` 与全局兜底合并。

---

## `config_loader.py`

### `load_image_specs(config_path: str = "config.json") -> Dict[str, ImageSpec]`

- **功能简述**：读取包规则 JSON，校验字段并转换为 `name -> ImageSpec` 映射。
- **输入边界**：`config_path` 指向可读 JSON；顶层必须是数组；每项至少需要非空 `name` 和 `image_name`；`target_file` 可为字符串、字符串数组、空值。
- **输出边界**：返回 `Dict[str, ImageSpec]`，键为唯一 `name`。
- **异常/副作用**：文件/JSON 错误向外抛出；结构错误、缺字段、`name` 重复时抛 `ValueError`。

---

## `env_config.py`

### `register_env(env: EnvironmentSpec) -> EnvironmentSpec`

- **功能简述**：把单环境注册到 `ENV_REGISTRY`。
- **输入边界**：`env.env_name` 必须非重复。
- **输出边界**：返回原 `EnvironmentSpec`。
- **异常/副作用**：重复注册抛 `ValueError`；会修改全局注册表。

### `register_composite_env(name: str, env_sequence: List[str]) -> List[str]`

- **功能简述**：注册组合环境名称与子环境执行序列。
- **输入边界**：`name` 必须非重复；`env_sequence` 不能为空。
- **输出边界**：返回注册后的子环境列表副本。
- **异常/副作用**：重复或空序列抛 `ValueError`；会修改组合环境注册表。

### `ENV_REGISTER(env_name: str)`

- **功能简述**：装饰器式环境注册入口，供 `ENV/<env_name>.py` 使用。
- **输入边界**：被装饰函数需接收 `env_name` 并返回 `EnvironmentSpec`。
- **输出边界**：返回原 factory，便于保留函数本身。
- **异常/副作用**：装饰时立即调用 factory 并注册环境。

### `load_env_modules() -> None`

- **功能简述**：自动导入 `ENV` 目录下的非 `_` 开头 Python 文件，触发注册。
- **输入边界**：无显式输入；依赖 `ENV_DIR`。
- **输出边界**：无返回值。
- **异常/副作用**：导入模块会执行模块级注册代码；使用 `_ENV_MODULES_LOADED` 保证只加载一次。

### `list_env_names() -> list[str]`

- **功能简述**：加载环境模块并返回已注册单环境名称列表。
- **输入边界**：无。
- **输出边界**：排序后的环境名列表。

### `get_env(env_name: str) -> EnvironmentSpec`

- **功能简述**：加载环境模块并按名称获取 `EnvironmentSpec`。
- **输入边界**：`env_name` 必须已注册。
- **输出边界**：返回对应环境定义。
- **异常/副作用**：未知环境抛 `KeyError`。

### `list_composite_env_names() -> list[str]`

- **功能简述**：返回已注册组合环境名称列表。
- **输出边界**：排序后的组合环境名列表。

### `get_composite_env(name: str) -> List[str]`

- **功能简述**：返回组合环境的子环境执行序列。
- **输入边界**：`name` 必须已注册。
- **输出边界**：`List[str]`，顺序即执行顺序。
- **异常/副作用**：未知组合环境抛 `KeyError`。

### `get_ssh_defaults(env_name: str) -> Dict[str, str | int]`

- **功能简述**：合并全局 `SSH_DEFAULTS` 与 `EnvironmentSpec.ssh_defaults`。
- **输入边界**：`env_name` 必须已注册。
- **输出边界**：至少包含 `host`、`username`、`password`、`port`。
- **异常/副作用**：未知环境由 `get_env()` 抛 `KeyError`。

### `get_telnet_defaults(env_name: str) -> Dict[str, str | int | float]`

- **功能简述**：合并全局 `TELNET_DEFAULTS` 与 `EnvironmentSpec.telnet_defaults`。
- **输出边界**：至少包含 `host`、`port`、`timeout`。

### `get_ftp_defaults(env_name: str) -> Dict[str, str | int]`

- **功能简述**：合并全局 `FTP_DEFAULTS` 与 `EnvironmentSpec.ftp_defaults`。
- **输出边界**：至少包含 `username`、`password`、`port`、`remote_path`。

---

## `models.py`

### `EnvironmentSpec.get_script_templates(self) -> Dict[str, str]`

- **功能简述**：兼容新旧脚本模板配置。
- **输入边界**：读取实例字段 `script_templates` 与 `script_template`。
- **输出边界**：优先返回 `script_templates`；否则把旧 `script_template` 包装为 `{"main": script_template}`；都为空时返回空字典。

### `normalize_image_var_ref(value: ImageVarInput) -> ImageVarRef`

- **功能简述**：把 `image_vars` 中字符串、元组/列表、字典或 `ImageVarRef` 统一为 `ImageVarRef`。
- **输入边界**：支持 `"name"`、`("name", "target")`、`{"name": "...", "target_file": "..."}`。
- **输出边界**：返回包含 `spec_name` 和可选 `target_file` 的对象。
- **异常/副作用**：字典缺少 `name/spec_name`、序列长度非法或类型不支持时抛异常。

---

## `env_executor.py`

### `ask_upload_protocol(default_protocol: str) -> str`

- **功能简述**：交互式读取上传协议。
- **输入边界**：默认值应为 `scp` 或 `ftp`。
- **输出边界**：返回 `scp` 或 `ftp`。
- **异常/副作用**：读取标准输入；非法输入提示并回退默认值。

### `ask_ftp_credentials(...) -> Tuple[str, str, int, str]`

- **功能简述**：交互式读取 FTP 用户名、密码、端口和远端目录。
- **输入边界**：默认端口为整数；默认远端目录为字符串。
- **输出边界**：返回 `(username, password, port, remote_path)`。
- **异常/副作用**：读取标准输入和隐藏密码输入；非法端口回退默认值。

### `ask_telnet_run(...) -> Tuple[bool, str, int, float, List[str]]`

- **功能简述**：交互式决定是否通过 Telnet 执行命令，并读取 Telnet 参数。
- **输出边界**：未启用时返回 `False` 和默认参数；启用时返回命令列表。
- **异常/副作用**：读取标准输入。

### `ask_target_host(default_host: str) -> str`

- **功能简述**：交互式读取目标主机地址，空输入回退默认值。
- **输出边界**：返回非空字符串。

### `ask_ssh_credentials(default_username: str, default_password: str, default_port: int) -> Tuple[str, str, int]`

- **功能简述**：交互式读取 SSH 用户名、密码和端口。
- **输出边界**：返回 `(username, password, port)`。
- **异常/副作用**：读取标准输入和隐藏密码输入；非法端口回退默认值。

### `ask_package_link_overrides(image_vars, image_specs) -> Dict[str, str]`

- **功能简述**：按当前环境实际引用的包规则询问是否覆盖远端下载路径。
- **输入边界**：`image_vars` 的每个值必须可标准化为 `ImageVarRef`，且 `spec_name` 存在于 `image_specs`。
- **输出边界**：仅返回用户显式输入的覆盖路径。
- **异常/副作用**：读取标准输入；缺失规则会抛 `KeyError`。

### `get_directory_size(path: str) -> int`

- **功能简述**：递归统计目录下文件大小。
- **输出边界**：返回非负整数；单个文件读取失败会忽略。

### `enforce_runtime_size_limit(runtime_root: str, max_bytes: int, protected_dir: str) -> None`

- **功能简述**：当 runtime 总大小超限时删除最旧历史目录，排除当前保护目录。
- **异常/副作用**：会删除目录。

### `EnvironmentProcessContext` 方法

- `main_script_path` / `main_script_name`：返回主脚本路径/文件名。
- `get_image_path(var_name, selected=True)`：获取变量对应的原始包或提取文件路径。
- `download_image_var(...)`：process 内继续从 WebHDFS 下载包，并按需提取 `target_file`。
- `extract_package_targets(...)`：对已有包继续提取目标文件。
- `render_template(...)`：渲染额外脚本，加入脚本映射和上传列表。
- `upload_files_scp(...)`：显式参数调用 SCP 上传。
- `upload_files_ftp(...)`：显式参数调用 FTP 上传。
- `upload_file_to_telnet_path(...)`：通过 FTP 上传单文件到指定远端文件路径。
- `send_telnet_commands(...)`：调用 Telnet 串口命令接口。
- `send_ssh_commands(...)`：调用 SSH 命令接口。
- `default_upload()`：按 `EnvironmentSpec.upload_protocol` 询问协议；合并环境默认连接参数；上传 `context.upload_files`。
- `default_telnet_run()`：按环境 Telnet 默认值询问是否执行命令，并替换 `${script_name}`。

### `execute_environment(env_name: str, *, runtime_suffix: str | None = None) -> Tuple[str, str]`

- **功能简述**：执行单环境完整流程。
- **输入边界**：`env_name` 必须已注册；`config.json` 必须可读取；远端 HDFS/目标服务器需可访问。
- **输出边界**：返回 `(run_dir, main_script_name)`。
- **异常/副作用**：网络请求、本地文件写入、runtime 清理、SCP/FTP 上传、可选 Telnet/SSH 执行；下游异常向外传播。

---

## `env_processes.py`

### `default_environment_process(context) -> None`

- **功能简述**：默认环境处理流程，先上传所有已准备文件，再按需通过 Telnet 执行命令。
- **输入边界**：`context` 需提供 `default_upload()` 和 `default_telnet_run()`。
- **输出边界**：无返回值。
- **异常/副作用**：执行上传和可选远端命令。

---

## `main.py`

### `choose_environment() -> str`

- **功能简述**：展示环境列表并读取用户选择。
- **输出边界**：返回选中的环境名。
- **异常/副作用**：无环境时抛 `RuntimeError`；读取标准输入。

### `main() -> None`

- **功能简述**：入口函数，选择环境并调用 `execute_environment()`。

---

## `composite_runner.py`

### `run_composite_environments(env_sequence: Sequence[str]) -> None`

- **功能简述**：按给定顺序串行执行多个环境。
- **输入边界**：`env_sequence` 不能为空，且每个环境名必须可被 `execute_environment()` 识别。
- **异常/副作用**：空序列抛 `ValueError`；每个子环境都会产生完整运行副作用。

---

## `renderer.py`

### `render_script(template: str, mappings: Iterable[Tuple[str, str, str]]) -> str`

- **功能简述**：把 `${var_name}` 替换为真实包名或提取文件名。
- **输入边界**：`mappings` 每项为 `(var_name, spec_name, real_name)`；仅 `var_name` 和 `real_name` 参与替换。
- **输出边界**：返回渲染后的字符串。
- **异常/副作用**：仍有未替换变量时抛 `ValueError`。

---

## `tools.py`

### `HDFSClient.__init__(self, base_url: str, verify_ssl: bool = False)`

- **功能简述**：初始化 WebHDFS 客户端。
- **输入边界**：`base_url` 为 HTTP(S) 前缀；尾部 `/` 会去除。

### `HDFSClient.list_directory(self, path: str) -> List[FileEntry]`

- **功能简述**：调用 WebHDFS `LISTSTATUS` 并转换为 `FileEntry` 列表。
- **异常/副作用**：发送网络请求；HTTP/JSON 错误向外抛出。

### `HDFSClient.choose_latest_directory(self, dirs: Sequence[FileEntry]) -> Optional[FileEntry]`

- **功能简述**：从候选项中选择修改时间最新的目录。

### `HDFSClient.resolve_link(self, link: str, base_link: str = "") -> str`

- **功能简述**：优先返回显式 `link`，否则返回 `base_link`。
- **异常/副作用**：两者均为空时抛 `ValueError`。

### `HDFSClient.list_newest_candidates(self, base_link: str) -> List[str]`

- **功能简述**：在 `base_link` 下找最新日期目录，并返回其中按修改时间倒序的 `newest` 候选目录。
- **异常/副作用**：找不到目录或 newest 时抛 `RuntimeError`；发送网络请求。

### `HDFSClient.find_image(self, remote_dir: str, pattern: str) -> FileEntry`

- **功能简述**：在远端目录内按正则匹配文件名，返回修改时间最新的匹配文件。
- **异常/副作用**：无匹配时抛 `FileNotFoundError`；正则非法会抛 `re.error`。

### `HDFSClient.download_file(self, remote_path: str, local_path: str) -> None`

- **功能简述**：通过 WebHDFS `OPEN` 流式下载远端文件到本地。
- **异常/副作用**：创建/覆盖本地文件；发送网络请求。

### `fetch_and_download_image(client: HDFSClient, spec: ImageSpec, download_dir: str) -> str`

- **功能简述**：按 `ImageSpec` 策略查找并下载包，返回真实文件名。
- **异常/副作用**：创建下载目录；下载文件；全部候选未命中时抛 `FileNotFoundError`。

### `upload_files_via_scp(...) -> None`

- **功能简述**：通过 SSH/SCP 上传多个本地文件。
- **输入边界**：必须显式传入 `host`、`username`、`password`、`port`、`remote_path`；本接口不读取默认凭据。
- **异常/副作用**：远端 `mkdir -p` 并上传文件；连接/认证/上传失败抛异常。

### `_ftp_mkdir_p(ftp: FTP, remote_path: str) -> None`

- **功能简述**：在 FTP 服务器上递归创建目录。
- **异常/副作用**：目录已存在等异常会忽略。

### `upload_files_via_ftp(...) -> None`

- **功能简述**：通过 FTP 上传多个文件到远端目录。
- **输入边界**：必须显式传入连接参数。
- **异常/副作用**：连接 FTP、创建目录、上传文件。

### `upload_file_via_ftp(...) -> None`

- **功能简述**：通过 FTP 上传单个文件到指定远端文件路径。
- **异常/副作用**：远端路径不含文件名时抛 `ValueError`。

### `run_ssh_commands(...) -> List[str]`

- **功能简述**：通过 SSH 逐条执行命令并返回 stdout/stderr 合并输出。
- **输入边界**：必须显式传入连接参数和命令序列。
- **异常/副作用**：连接 SSH 并执行命令；任一命令非 0 退出码时抛 `RuntimeError`。

---

## `telnet.py`

### `TelnetCommandClient`

- `connect()`：建立 socket 连接并识别 shell 提示符。
- `detect_prompt()`：发送多次回车，取最后一次输出的非空最后行作为提示符。
- `run_command(command, timeout=None)`：发送单条命令，读取到提示符出现并返回输出。
- `run_commands(commands, timeout=None)`：逐条执行命令。
- `close()`：关闭 socket。

### `run_telnet_commands(...) -> list[str]`

- **功能简述**：便捷接口，创建 `TelnetCommandClient` 并逐条执行命令。
- **异常/副作用**：建立 Telnet/socket 连接；可写入 log；超时会抛 `TimeoutError`。

---

## `unextract.py`

### `find_git_sh() -> str`

- **功能简述**：查找 Git for Windows 的 `sh.exe` 或 PATH 中的 `sh`。
- **异常/副作用**：找不到时抛 `FileNotFoundError`。

### `_quote(path) -> str`

- **功能简述**：为 shell 命令中的路径添加单引号并转义单引号。

### `_run_shell(command, *, cwd=None) -> subprocess.CompletedProcess[str]`

- **功能简述**：通过 `sh -c` 执行命令。
- **异常/副作用**：命令非 0 会因 `check=True` 抛 `CalledProcessError`。

### `unextract_run(run_file: str, runtime_dir: str, *, tmp_name: str = "run_tmp") -> str`

- **功能简述**：执行 `.run --noexec --extract=...` 并返回解压目录。
- **异常/副作用**：会删除并重建临时目录，执行外部命令。

### `unextract_tar_gz(tar_gz_file: str, runtime_dir: str, *, tmp_name: str = "tar_tmp") -> str`

- **功能简述**：解压 `.tar.gz`/`.tgz` 到临时目录并返回路径。

### `_normalize_targets(target_files) -> list[str]`

- **功能简述**：把目标文件参数规范为列表。

### `extract_target_files(package_path: str, runtime_dir: str, target_files) -> list[str]`

- **功能简述**：解包并把指定目标文件/目录复制到 runtime 目录，最后删除临时目录。
- **异常/副作用**：不支持的包类型抛 `ValueError`；找不到目标文件抛 `FileNotFoundError`；会创建/覆盖复制结果。

---

## `logger.py`

### `setup_logger(log_dir: str = "logs", run_id: str = "") -> logging.Logger`

- **功能简述**：初始化统一 logger，输出到控制台和文件。
- **异常/副作用**：创建日志目录和文件；重置 logger handlers。

### `RunIdFilter.__init__(self, run: str)` / `RunIdFilter.filter(self, record) -> bool`

- **功能简述**：向日志记录注入 `run_id` 字段。

---

## `debug/config_debug.py`

### `debug_load_image_specs(config_path: str = "config.json") -> dict[str, dict[str, object]]`

- **功能简述**：以字典形式输出 `config.json` 解析结果，便于检查 `target_file`。

### `debug_env_transport_defaults(env_name: str) -> dict[str, dict[str, object]]`

- **功能简述**：输出指定环境合并后的 SSH/Telnet/FTP 默认值。
