# AutoEnv 快速开始

本页负责安装和普通脚本的最短流程。其他场景及文档权威来源见 [文档入口与能力边界](DOCUMENTATION_MAP.md)。

## 最简实现

要求 Python 3.11+，在仓库根目录安装：

```powershell
python -m pip install -e ".[test]"
python -X utf8 -m autoenv.cli list --json
```

如需沿用当前 Windows/Python 3.11.4 环境的已测直接依赖，将安装命令改为 `python -m pip install -c constraints-local-tested.txt -e ".[test]"`。快照见 [constraints-local-tested.txt](../constraints-local-tested.txt)，它不是完整锁文件，也不表示其他 Python/依赖组合均已验证。SDK/CLI 可随项目安装使用，以下 Web 操作仍在源码仓库根目录启动。

打开常用网页：`python -X utf8 startWeb.py`，固定访问 `http://127.0.0.1:8765/`。先登记一级组网和各 SSH/Telnet/FTP 连接，再选择脚本及绑定，页面会按最新静态声明提供输入，并调用真实 CLI。

在 `scripts/demo.py` 创建普通文件：

```python
from autoenv import create_file, get_remote_info, init_script

def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("单机直连", "设备-ssh")])
    file = create_file(paths.runtime_path, "hello.sh", "#!/bin/sh\necho READY\n")
    dut.upload(file, "/tmp/demo")
    print(dut.run("echo READY"))
```

不用注册装饰器。先静态检查，再完整指定执行参数：

```powershell
python -X utf8 -m autoenv.cli describe demo --json
python -X utf8 -m autoenv.cli run demo --non-interactive --topology "单机直连" --bind "dut=设备-ssh"
```

执行会访问绑定的设备。完成 stdout 只有 run.log 绝对路径，退出码 0/1/2 分别为成功/执行失败/准备失败。`run.log` 包含脚本源码位置、动作、结果、远端输出和 print；同目录 `output.log` 只记录每条动作的简洁结果，网页实时显示它。

## 补充功能

产品日志独立于环境脚本：同一 Web 服务的 `/product-logs/` 页面支持压缩包/文件夹导入，分别收集 `diag` 用户态/固件和 `var` 内核日志，并按分类或统一时间查看。可用以下命令显式选择产品模式：

```powershell
python -X utf8 -m product_log INPUT --collection-mode product --diag-directory diag/slot62 --var-directory var
```

默认 `auto` 在找到精确的 `diag` 或 `var` 目录后使用产品规则，未找到则按原通用规则处理；显式旧 `--directory` 在 `auto` 中继续选择通用模式。需要任意日志目录/通配符时使用 `--collection-mode generic --directory PATH`。产品目录多命中会报错，支持 `diag/slot62` 等部分路径消歧；只找到一类仍可解析，显式产品模式两类都不存在时报错。

产品默认将 `diaglog_*.log` 字典序排列后读最新 `diag.log`，内核按 `.1/.2/.../.10/...` 数字顺序后读最新 current。网页模块表预置 `udk→udk→udk`、`UBE→UBE→mami`；旧索引批次需要重新导入。完整整理、分类及映射规则见 [product_log/README.md](../product_log/README.md)。

单文件离线日志页在源码根目录构建：

```powershell
python -X utf8 tools/build_product_log_page.py
```

把生成的 `dist/product-log-offline.html` 交给使用者，直接在现代桌面浏览器打开并选择压缩包或文件夹。构建仅需 Python 标准库，使用阶段只有浏览器；全部资源内嵌，无服务或网络加载。来源保留相对路径，日志仅存页面内存，模块表可导入/下载 JSON；磁盘工作区、历史批次与 3 GiB 清理由原服务端版本提供。详细步骤见 [独立离线页说明](../product_log/standalone/README.md)。

普通脚本可使用命令执行、文件传输、本地文件/HDFS、Telnet 关键词等待和业务参数。各接口的参数、返回值及失败语义统一查 [接口说明](SCRIPT_API_ANALYSIS.md)；连接类型、上传方式、白名单与历史选择查 [环境登记](ENVIRONMENT_REGISTRATION_GUIDE.md)。

最小例子：[example.py](../scripts/example.py)；可复制模板：[template.py](../scripts/template.py)；全部接口：[all_interfaces.py](../scripts/all_interfaces.py)。

具体业务例子：[UDK V100 环境拉起与固件替换](UDK_V100_WORKFLOWS.md)。两个脚本复用 `SingleUnions_S3`，需要填写连接与包路径；生成的 `start_env.sh` 目前只有用户要求的占位内容。

发布示例的 `if __name__ == "__main__"` 转发同一个 CLI，因此 `python scripts/example.py --non-interactive ...` 也有完整日志捕获/释放。CLI执行脚本时跳过这个guard再调用main，不递归。

## 组件与测试

```powershell
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py
python -X utf8 -m compileall autoenv scripts webPage product_log
python -X utf8 -m pytest
```

无需设备的实际协议演示与命令结果见 [LOCAL_SIMULATION.md](LOCAL_SIMULATION.md)。环境配置见 [ENVIRONMENT_REGISTRATION_GUIDE.md](ENVIRONMENT_REGISTRATION_GUIDE.md)；接口/入参出参见 [SCRIPT_API_ANALYSIS.md](SCRIPT_API_ANALYSIS.md)；整体决定见 [重构计划](REFACTOR_PLAN_FOUR_API_CATEGORIES.md)；Agent 生成规则见 [脚本 skill](../.agents/skills/autoenv-script-generator/SKILL.md)。
