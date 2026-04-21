# AutoEnv 函数功能简述与输入输出边界

本文档按模块列出仓库中**所有显式定义的函数/方法**（含类方法与局部类方法），说明其用途，以及输入/输出边界与主要异常行为。

---

## `composite_runner.py`

### `run_composite_environments(env_sequence: Sequence[str]) -> None`
- **功能简述**：按给定顺序串行执行多个已注册环境，每个子环境都会走完整下载、渲染、上传流程。
- **输入边界**：
  - `env_sequence` 必须是非空序列。
  - 序列中的环境名应能被 `execute_environment` 正常识别，否则会在下游抛错。
- **输出边界**：
  - 无返回值（`None`），通过标准输出打印执行进度。
- **异常/副作用**：
  - `env_sequence` 为空时抛出 `ValueError`。
  - 会触发网络请求、文件写入、SSH 上传等下游副作用。

---

## `config_loader.py`

### `load_image_specs(config_path: str = "config.json") -> Dict[str, ImageSpec]`
- **功能简述**：读取镜像配置 JSON，校验字段并转换为 `name -> ImageSpec` 映射。
- **输入边界**：
  - `config_path` 需指向可读 JSON 文件。
  - JSON 顶层必须为数组；数组元素需至少包含非空 `name` 与 `image_name`。
  - `name` 在配置中必须唯一。
- **输出边界**：
  - 返回 `Dict[str, ImageSpec]`，键为 `name`。
  - 允许 `link` 与 `base_link` 为空串（由下游决定如何处理）。
- **异常/副作用**：
  - 文件不存在/不可读会抛 `OSError`。
  - 非法 JSON 抛 `json.JSONDecodeError`。
  - 结构或字段校验不通过抛 `ValueError`。

---

## `env_config.py`

### `list_env_names() -> list[str]`
- **功能简述**：返回当前已注册单环境名称列表（排序后）。
- **输入边界**：无输入。
- **输出边界**：返回可能为空的 `list[str]`。

### `get_env(env_name: str) -> EnvironmentSpec`
- **功能简述**：按名称获取单环境配置。
- **输入边界**：`env_name` 必须存在于 `ENV_REGISTRY`。
- **输出边界**：返回对应 `EnvironmentSpec`。
- **异常/副作用**：不存在时抛 `KeyError`。

### `list_composite_env_names() -> list[str]`
- **功能简述**：返回已注册组合环境名称列表（排序后）。
- **输入边界**：无输入。
- **输出边界**：返回可能为空的 `list[str]`。

### `get_composite_env(name: str) -> List[str]`
- **功能简述**：按名称获取组合环境对应的子环境序列。
- **输入边界**：`name` 必须存在于 `COMPOSITE_ENV_REGISTRY`。
- **输出边界**：返回 `List[str]`（执行顺序即列表顺序）。
- **异常/副作用**：不存在时抛 `KeyError`。

### `get_ssh_defaults(env_name: str) -> Dict[str, str | int]`
- **功能简述**：将全局 SSH 默认值与环境级覆盖合并后返回。
- **输入边界**：任意字符串均可；未知环境将仅使用全局默认值。
- **输出边界**：返回至少包含 `username`、`password`、`port` 的字典。

---

## `env_executor.py`

### `ask_target_host() -> str`
- **功能简述**：交互式读取目标主机地址，空输入时使用默认值。
- **输入边界**：来自终端输入；空串回退为 `192.168.1.100`。
- **输出边界**：返回非空字符串。
- **异常/副作用**：读取标准输入。

### `ask_ssh_credentials(default_username: str, default_password: str, default_port: int) -> Tuple[str, str, int]`
- **功能简述**：交互式读取 SSH 用户名/密码/端口，并做最小化合法性处理。
- **输入边界**：
  - 默认值可为任意字符串与整数。
  - 端口输入仅在纯数字时生效，否则回退 `default_port`。
- **输出边界**：返回 `(username, password, port)`；`port` 保证为 `int`。
- **异常/副作用**：
  - 读取终端输入与隐藏密码输入。
  - 非数字端口不会抛错，仅提示并回退默认值。

### `ask_package_link_overrides(image_vars: Dict[str, str], image_specs: Dict[str, ImageSpec]) -> Dict[str, str]`
- **功能简述**：按环境使用到的 `spec_name` 提供交互式覆盖下载目录。
- **输入边界**：
  - `image_vars` 的值需能在 `image_specs` 中找到。
  - 用户输入非空路径时会被规范化为前导 `/` 且去尾部 `/`。
- **输出边界**：
  - 返回覆盖字典，仅包含用户明确输入的 `spec_name` 项。
  - 若某 `spec` 的 `link/base_link` 都为空，则用户必须输入可用路径才能继续。
- **异常/副作用**：
  - 若 `spec_name` 缺失会触发 `KeyError`。
  - 读取标准输入并打印提示信息。

### `get_directory_size(path: str) -> int`
- **功能简述**：递归统计目录内全部文件大小（字节）。
- **输入边界**：`path` 可为任意路径；不存在目录时 `os.walk` 返回空迭代。
- **输出边界**：返回非负整数。
- **异常/副作用**：
  - 单个文件 `getsize` 失败会被忽略并继续。

### `enforce_runtime_size_limit(runtime_root: str, max_bytes: int, protected_dir: str) -> None`
- **功能简述**：当 `runtime_root` 总大小超限时，循环删除最旧子目录（排除 `protected_dir`）直到不超限或无可删目录。
- **输入边界**：
  - `runtime_root` 应为目录路径；不存在则直接返回。
  - `max_bytes` 建议为非负整数；过小会更频繁触发删除。
- **输出边界**：无返回值。
- **异常/副作用**：
  - 会执行目录删除（`shutil.rmtree`）。
  - 若目录时间戳读取失败等问题，可能在排序阶段抛出 `OSError`。

### `execute_environment(env_name: str, *, runtime_suffix: str | None = None) -> Tuple[str, str]`
- **功能简述**：执行单环境主流程：加载配置、下载包、渲染脚本、容量控制、询问 SSH、上传文件。
- **输入边界**：
  - `env_name` 必须是已注册环境名。
  - `runtime_suffix` 可为空；非空时用于区分运行目录名。
  - 依赖交互输入（包路径覆盖、目标主机、SSH 凭据）。
- **输出边界**：
  - 成功返回 `(run_dir, script_name)`。
  - `run_dir` 指向本次运行目录；`script_name` 为已生成脚本文件名。
- **异常/副作用**：
  - 可能抛出 `KeyError`、`ValueError`、`FileNotFoundError`、网络/SSH 异常等。
  - 会创建本地目录与文件、下载文件、写日志、上传远端文件。

---

## `logger.py`

### `setup_logger(log_dir: str = "logs", run_id: Optional[str] = None) -> logging.Logger`
- **功能简述**：创建并配置统一日志器（文件 + 控制台），并将 `run_id` 注入日志字段。
- **输入边界**：
  - `log_dir` 为可写目录路径；不存在则自动创建。
  - `run_id` 可为空；为空时自动生成为时间戳。
- **输出边界**：返回已配置好的 `logging.Logger`（名称固定为 `autoenv`）。
- **异常/副作用**：
  - 会清空该 logger 现有 handler 后重新挂载。
  - 会创建/写入日志文件。

### `RunIdFilter.__init__(self, run: str)`
- **功能简述**：保存本次运行标识供过滤器注入日志记录。
- **输入边界**：`run` 可为任意字符串。
- **输出边界**：无返回值。

### `RunIdFilter.filter(self, record: logging.LogRecord) -> bool`
- **功能简述**：向日志记录对象添加 `run_id` 字段，供 formatter 使用。
- **输入边界**：`record` 必须是 `logging.LogRecord`。
- **输出边界**：总是返回 `True`（表示不过滤掉该日志）。
- **异常/副作用**：会修改 `record` 对象（新增/覆盖 `run_id` 属性）。

---

## `main.py`

### `choose_environment() -> str`
- **功能简述**：交互式展示环境列表并读取用户选择。
- **输入边界**：
  - 环境列表不能为空。
  - 用户输入必须是范围内数字编号，否则会循环重试。
- **输出边界**：返回选中的环境名。
- **异常/副作用**：
  - 无已注册环境时抛 `RuntimeError`。
  - 读取标准输入并打印提示。

### `main() -> None`
- **功能简述**：程序入口，执行“选环境 + 执行环境”流程。
- **输入边界**：依赖 `choose_environment` 的交互输入。
- **输出边界**：无返回值。
- **异常/副作用**：下游异常会向上传播。

---

## `renderer.py`

### `render_script(template: str, mappings: Iterable[Tuple[str, str, str]]) -> str`
- **功能简述**：将模板中的 `${var_name}` 占位符替换为真实包名，并校验是否还有未替换变量。
- **输入边界**：
  - `template` 为脚本模板字符串。
  - `mappings` 每项为 `(var_name, spec_name, real_name)`，其中仅 `var_name` 与 `real_name` 参与替换。
- **输出边界**：返回渲染后的脚本文本。
- **异常/副作用**：
  - 若仍存在未替换占位符，抛 `ValueError`。

---

## `tools.py`

### `HDFSClient.__init__(self, base_url: str, verify_ssl: bool = False)`
- **功能简述**：初始化 WebHDFS 客户端，保存基础 URL、SSL 校验策略和复用 Session。
- **输入边界**：
  - `base_url` 应为合法 HTTP(S) 前缀；尾部 `/` 会被去除。
  - `verify_ssl` 控制 HTTPS 证书校验。
- **输出边界**：无返回值。

### `HDFSClient.list_directory(self, path: str) -> List[FileEntry]`
- **功能简述**：调用 WebHDFS `LISTSTATUS`，并将返回项转换为 `FileEntry` 列表。
- **输入边界**：`path` 应为 HDFS 路径（通常以 `/` 开头）。
- **输出边界**：返回 0..N 个 `FileEntry`，忽略 `pathSuffix` 为空的项。
- **异常/副作用**：
  - HTTP 失败抛 `requests` 异常。
  - JSON 结构异常可能引发解析相关异常。

### `HDFSClient.choose_latest_directory(self, dirs: Sequence[FileEntry]) -> Optional[FileEntry]`
- **功能简述**：从候选 `FileEntry` 中选择修改时间最新的目录项。
- **输入边界**：`dirs` 可为空、可混合文件与目录。
- **输出边界**：仅当存在目录时返回 `FileEntry`，否则返回 `None`。

### `HDFSClient.resolve_link(self, link: str, base_link: str = "") -> str`
- **功能简述**：解析下载目录：优先使用显式 `link`，否则使用 `base_link`。
- **输入边界**：
  - `link` 或 `base_link` 至少一个非空（去空白后）。
- **输出边界**：返回去尾 `/` 的目录路径字符串。
- **异常/副作用**：两者均为空时抛 `ValueError`。

### `HDFSClient.list_newest_candidates(self, base_link: str) -> List[str]`
- **功能简述**：在 `base_link` 下定位最新日期目录，并返回其中按修改时间倒序的 `newest` 子目录路径集合。
- **输入边界**：`base_link` 需可解析为有效目录。
- **输出边界**：返回非空路径列表（若成功）。
- **异常/副作用**：
  - 找不到目录或 newest 子目录时抛 `RuntimeError`。
  - 依赖远程 HDFS 请求，可能抛网络异常。

### `HDFSClient.find_image(self, remote_dir: str, pattern: str) -> FileEntry`
- **功能简述**：在远程目录内按正则匹配文件名，并返回最新修改的匹配文件。
- **输入边界**：
  - `pattern` 应为合法正则表达式。
  - `remote_dir` 应为可访问目录。
- **输出边界**：返回一个 `FileEntry`（保证为文件，不是目录）。
- **异常/副作用**：
  - 正则非法抛 `re.error`。
  - 无匹配时抛 `FileNotFoundError`。

### `HDFSClient.download_file(self, remote_path: str, local_path: str) -> None`
- **功能简述**：通过 WebHDFS `OPEN` 流式下载远程文件到本地路径。
- **输入边界**：
  - `remote_path` 为可访问远程文件路径。
  - `local_path` 的父目录应已存在（本方法不负责创建父目录）。
- **输出边界**：无返回值。
- **异常/副作用**：
  - 会创建/覆盖 `local_path` 文件内容。
  - 网络/权限问题会抛对应异常。

### `fetch_and_download_image(client: HDFSClient, spec: ImageSpec, download_dir: str) -> str`
- **功能简述**：按 `ImageSpec` 策略查找并下载驱动包，返回真实文件名。
- **输入边界**：
  - `download_dir` 不存在会自动创建。
  - `spec.link` 非空时走显式目录；否则走 `base_link` newest 自动发现。
- **输出边界**：成功返回下载文件名（不含目录）。
- **异常/副作用**：
  - 全部候选均未命中时抛 `FileNotFoundError`。
  - 会产生网络请求和本地文件写入。

### `upload_files_via_scp(host: str, local_files: Sequence[str], username: str, password: str, remote_path: str = "/root/autoEnv", port: int = 22) -> None`
- **功能简述**：通过 SSH + SCP 将多个本地文件上传到目标服务器目录。
- **输入边界**：
  - `host`/`username`/`password` 需可建立 SSH 连接。
  - `local_files` 中路径应存在且可读。
  - `remote_path` 不存在时会先执行 `mkdir -p`。
- **输出边界**：无返回值。
- **异常/副作用**：
  - 连接失败、认证失败、上传失败会抛异常。
  - 会在远端创建目录并写入文件。

---

## `workflow_common.py`

> 本模块不定义新函数，仅重新导出 `env_executor.py` 中的公共流程函数与常量。

