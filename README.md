# AutoEnv

完整文档清单见 [README 文档总指引](README_GUIDE.md)，包含使用说明、设计评审、测试、功能记录、Agent 技能、SDD 模板和配套资料。

## 最简实现：快速开始

AutoEnv 是个人本地工具，用普通 Python 顺序流程操作 SSH、Telnet、FTP 与文件。脚本显式申请环境对象和本地 handler，无需注册装饰器或 ctx。按任务查找说明见 [文档入口与能力边界](docs/DOCUMENTATION_MAP.md)。

```powershell
python -m pip install -e ".[test]"
python -X utf8 -m autoenv.cli list --json
python -X utf8 startWeb.py
```

网页固定 `http://127.0.0.1:8765/`，登记环境、选择脚本/组网并绑定连接后运行。页面将表单封装为实际 CLI 命令，实时显示每条操作的简洁结果。

项目要求 Python 3.11+。如需沿用当前 Windows/Python 3.11.4 的已测直接依赖，可改用 `python -m pip install -c constraints-local-tested.txt -e ".[test]"`。该 [环境快照](constraints-local-tested.txt) 不是完整锁文件，也不代表所有版本组合已验证。Web 使用源码仓库启动；SDK 和独立产品日志 CLI 的安装范围见 [文档地图](docs/DOCUMENTATION_MAP.md)。

```python
from autoenv import create_file, get_remote_info, init_script

def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("示例组网", "设备-ssh")])
    file = create_file(paths.runtime_path, "hello.sh", "#!/bin/sh\necho READY\n")
    dut.upload(file, "/tmp/demo")
    print(dut.run("echo READY"))
```

保存为 `scripts/demo.py`，无需 @register_script。CLI 可以一次传完所有参数：

```powershell
python -X utf8 -m autoenv.cli describe demo --json
python -X utf8 -m autoenv.cli run demo --non-interactive --topology "示例组网" --bind "dut=设备-ssh"
```

run/rerun 结束只输出完整 run.log 的绝对路径，退出码 0 成功、1 执行失败、2 准备失败。完整日志记录源码位置、动作、执行结果、远端输出和 print；同目录 output.log 是简洁逐步结果，供网页实时展示。

## 补充功能

UDK V100 的两个普通脚本为 [环境拉起](scripts/UDK_V100.py) 和 [固件替换](scripts/UDK_V100_Firmware.py)，共用 `SingleUnions_S3` 组网。配置、占位 shell、串口完成判据与本地模拟测试见 [使用说明](docs/UDK_V100_WORKFLOWS.md)。

本地产品日志入口为 <http://127.0.0.1:8765/product-logs/>：拖入压缩包、选择上传文件夹或填写服务端绝对目录，自动收集 `diag` 用户态/固件及 `varlog` 内核态日志；默认只有找不到 `varlog` 时才回退到旧 `var`，显式选择 `var` 仍有效。页面保留分类视图、统一时间视图和“模块 → 用户态 ModuleName → 内核关键词”对应表；每次任务显示独立工作区的绝对路径。

功能集中在 [product_log](product_log/README.md)，也可独立执行 `python -X utf8 -m product_log INPUT --collection-mode product --diag-directory diag/slot62 --var-directory varlog`，无需登记设备。CLI 默认 `auto` 发现精确的 `diag`/`varlog`（或兼容的旧 `var`）目录后采用产品规则，未发现则使用通用规则；`generic` 保留任意目录/文件通配符整理，网页固定采用产品规则。所有模式都先递归展开整个输入中的支持压缩包，再定位目录和选择日志，包括 diag、通用目录及未选中目录里的包。旧版本解析批次需重新导入。

文件 SDK 的搜索、解压、复制、重命名接口见 [接口表](docs/SCRIPT_API_ANALYSIS.md)；RAR 解压的本机后端边界、产品日志整理与 Web 达到 3 GiB 后的自动清理规则见 [产品日志说明](product_log/README.md)。清理只覆盖 Web 管理的上传与历史批次，外部原文件和独立 CLI 输出由使用者管理。

需要单文件离线网页时，在源码根目录运行 `python -X utf8 tools/build_product_log_page.py`，生成 `dist/product-log-offline.html`。将这个 HTML 交给使用者，在现代桌面浏览器打开后选择压缩包或文件夹即可解析；使用阶段只需要浏览器。支持 ZIP LZMA（算法 14）和 RAR4/RAR5 普通及固实压缩，代码、样式、解码器、WASM 与许可全部内嵌，构建脚本仅用 Python 标准库，运行不加载网络资源或系统解压程序。日志保存在当前页面内存，来源显示相对路径，模块表可导入/下载 JSON；详细用法及能力边界见 [独立离线页说明](product_log/standalone/README.md)。归档与串口适配的本轮变化及验证范围见 [功能记录](newFeature/2026-10-13-archive-and-serial-compatibility.md)。

最小示例：[example.py](scripts/example.py)；模板：[template.py](scripts/template.py)；全量用法：[all_interfaces.py](scripts/all_interfaces.py)。上手见 [快速开始](docs/QUICK_START.md)，连接配置见 [环境登记](docs/ENVIRONMENT_REGISTRATION_GUIDE.md)，参数和返回语义见 [接口表](docs/SCRIPT_API_ANALYSIS.md)，网页操作见 [Web 用法](docs/web_usage.md)。Agent 生成 Python 的规则见 [脚本 skill](.agents/skills/autoenv-script-generator/SKILL.md)。

## 组件与测试

```powershell
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py
python -X utf8 -m compileall autoenv scripts webPage product_log
python -X utf8 -m pytest
```

[本地模拟搭建与命令结果](docs/LOCAL_SIMULATION.md) 使用 loopback SSH/SCP/SFTP、Telnet 与 FTP 真协议，命令只记录，文件实际传输/落盘，不打桩；本轮模拟不访问 HDFS。测试入口/排查见 [tests/README.md](tests/README.md)。原高级日志采集与提取组件保留，但新的常用流程统一通过普通脚本和 CLI。
