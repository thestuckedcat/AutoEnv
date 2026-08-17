# Web 唯一启动链与固定端口

## 意图

保证当前仓库只有一套可执行 Web 服务、一个启动命令和一个端点。旧实现不作为运行期回退保留；需要恢复历史行为时，以 Git commit 为粒度回退。

## 用户可见行为

- 唯一启动命令：`python -X utf8 startWeb.py`；
- 唯一端点：`http://127.0.0.1:8765/`；
- 启动命令不接受 `--host`、`--port`、`--no-browser` 或其他参数；
- 端口被占用时返回失败，提示停止占用进程，不自动选择其他端口；
- `webPage/server.py` 只提供被入口调用的实现，不能通过模块自身直接启动；
- 旧 `frontend/` 服务、页面和批处理启动器全部删除。

## 架构与数据流

```text
python -X utf8 startWeb.py
  -> 校验没有启动参数
  -> webPage.server.main()
  -> ExclusiveThreadingHTTPServer
  -> 固定绑定 127.0.0.1:8765
  -> 端口冲突：失败并退出（无 fallback）
```

`webPage/server.py` 中的 `WEB_HOST`、`WEB_PORT` 和派生的 `WEB_URL` 是运行期端点的唯一代码来源。文档重复展示地址只用于说明，不构成配置入口。

## 重要决策

- 不保留旧服务器、旧 HTML 页面、兼容批处理或第二入口；
- 不从命令行、环境变量、配置文件或空闲端口探测中覆盖 host/port；
- 不把端口冲突解释为可回退条件；
- 回退由版本控制完成，避免两套逻辑长期漂移；
- `AGENTS.md` 将以上约束设为后续功能修改的仓库级规则。

## 主要文件

- 修改：`startWeb.py`、`webPage/server.py`、`AGENTS.md`；
- 删除：旧 Web 原型目录中的 server、batch launcher 和静态资源；
- 测试：`tests/test_interfaces_web_ftp.py`；
- 文档：`README.md`、`webPage/QUICK_START.md`、`docs/web_usage.md`、`docs/WEB_ARCHITECTURE_AND_HANDOFF.md`、`docs/DOCUMENTATION_AUDIT.md`。

## 测试与验收

- 静态扫描要求业务代码中只有一个 `serve_forever()` 实现；
- 检查旧 Web 目录不存在；
- 子进程验证任何启动参数都会在绑定端口前失败；
- fake HTTP server 验证唯一绑定地址为 `127.0.0.1:8765`；
- 验证端口冲突返回失败且不会尝试第二地址；
- 运行 Python 编译、聚焦 UT、完整离线 pytest 和 `git diff --check`。

验证结果：

- 单一路径聚焦测试：通过；
- Python `compileall`：通过；
- 环境脚本契约：`2 file(s)` 通过；
- 两个 Web Tool 验证：通过；
- 本次合并提交的最终完整离线 pytest：`269 passed, 1 skipped`。跳过项是当前 Windows 主机不允许创建符号链接；另有 Paramiko Blowfish 弃用和测试故意创建 ZIP 重名项的预期警告；
- `git diff --check`：通过。
