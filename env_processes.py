from __future__ import annotations


def default_environment_process(context) -> None:  # type: ignore[no-untyped-def]
    """默认环境处理流程：上传所有已准备文件，并按需通过 Telnet 执行命令。

    具体上传协议、凭据和 Telnet 交互仍由 context 中封装的交互接口处理。
    自定义环境可以提供同签名函数，在里面自由编排下载、解包、渲染、上传和命令执行。
    """
    context.default_upload()
    context.default_telnet_run()
