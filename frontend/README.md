# AutoEnv Frontend

这是一个独立的本地界面目录，没有修改 AutoEnv 现有 Python 模块。

## 使用方式

### 直接预览界面

双击 `index.html` 即可打开，不需要安装依赖或启动 Web Server。此方式运行内置演示流程，不会连接真实设备。

### 执行真实 AutoEnv 脚本

Windows 下双击 `start_ui.bat`。它会在 `127.0.0.1:8765` 启动仅限本机访问的桥接服务并自动打开浏览器：

1. 页面从现有 AutoEnv 注册表加载脚本列表。
2. 选择 `run` 或 `rerun`，点击 `INITIALIZE RUN`。
3. Python 脚本请求输入时，页面会显示对应交互卡片；Enter 提交，Ctrl+Enter 也可提交。
4. 输出实时显示并自动高亮成功、错误、警告、路径、MD5、`Ctrl+B`、`KEYWORD` 和 `!newest`。

关闭启动脚本的命令行窗口即可停止桥接服务。运行中的任务也可使用页面的 `ABORT` 按钮终止。

## 当前模式

- 直接打开时使用内置 Demo 流程，可完整体验脚本选择、run/rerun、SSH 参数、密码、package `!newest`、实时输出、自动高亮、停止、筛选和日志导出。
- 浏览器基于安全限制不能直接启动本机 Python、SSH 或 Telnet 进程，因此不会在 Demo Bridge 下操作真实设备。
- `start_ui.bat` 使用 Python 标准库桥接现有 `autoenv.registry.run_script`，无需额外安装前端依赖。
- 桥接服务只监听回环地址，但它启动的是实际 AutoEnv 任务，执行前请确认所选脚本和目标设备。
