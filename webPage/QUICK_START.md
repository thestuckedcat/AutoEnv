# AutoEnv 网页入口

在源码仓库根目录执行唯一启动命令：

```powershell
python -X utf8 startWeb.py
```

固定访问 <http://127.0.0.1:8765/>，登记环境、选择普通 Python 脚本和连接后运行。产品日志使用同一服务的 <http://127.0.0.1:8765/product-logs/>。

实际表单步骤、输出与参数说明统一见 [网页用法](../docs/web_usage.md)；diag 用户态/固件、var 内核日志、模块对应表、统一时间视图及 3 GiB 自动清理规则见 [产品日志说明](../product_log/README.md)；开发接手见 [Web 架构](../docs/WEB_ARCHITECTURE_AND_HANDOFF.md)。网页使用产品规则收集，命令行另可选 auto/product/generic；旧解析批次需重新导入。

安装、SDK/CLI 使用范围、已测依赖和高级组件边界见 [文档入口与能力边界](../docs/DOCUMENTATION_MAP.md)。本页只提供入口，不再重复维护接口签名和完整操作步骤。
