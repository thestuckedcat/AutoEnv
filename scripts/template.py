"""AutoEnv 全接口模板；复制后请删除不需要的步骤并替换示例目标。

这个文件只会在脚本发现阶段注册函数，导入时不会连接任何远端。实际运行任一
``template_*`` 脚本会访问示例 HDFS、SSH、FTP 或 Telnet 目标，因此不要直接在
生产环境执行本模板。

覆盖范围是 ``autoenv.__all__`` 的全部公共导出，以及环境函数中可用的 RunContext
业务方法。``webPage/tools/*.py`` 属于另一套动态 Web Tool 插件，不应从环境脚本
调用；Web HTTP 桥和 RunContext 的保存/关闭方法也由框架管理。

Web 关联
--------
脚本中的声明调用是与“环境启动”页面的契约：

* ``description`` 显示在脚本下拉框中。
* ``package(name, alias=..., description=...)`` 自动生成 HDFS 链接输入；``name``
  必须对应 ``config.json``。留空时 Web 请求沿用配置的 ``link/base_link``。
* ``parameters`` 生成普通输入框；脚本用 ``ctx.argument(name)`` 读取。
* ``ctx.register_ssh_host/register_telnet/register_ftp_host`` 自动生成“环境 / 标签
  资源”下拉框；协议由函数确定，``resource_label`` 决定可选资源。``alias`` 默认
  使用 ``name``，``description`` 默认空字符串。
* Web 会把以上输入转换成结构化 LaunchRequest；运行结果及日志持续显示在页面。
* ``register_func`` 当前只提供 CLI 菜单，没有对应 Web 输入控件，因此专门放在
  ``template_post_start_functions`` 中，不应从 Web 启动该示例。

结果对象
--------
下载、远端下载、提取、上传和命令分别返回 ``DownloadResult``、
``RemoteDownloadResult``、``ExtractResult``、``UploadResult`` 和 ``CommandResult``。
每一步都先看 ``success``，失败时直接返回该结果；框架最终包装成 ``ScriptResult``，
并写入 ``run.log``/``result.json``。``with_failure()`` 用来把“命令成功但业务检查
失败”转换为明确失败。RunContext 会自动保存上次参数并关闭连接，不要在业务脚本中
手动调用它的生命周期方法。

阅读完成并替换全部示例地址、命令和文件名后，可用 ``autoenv run <脚本名>``
首次运行；``autoenv rerun <脚本名>`` 会复用上次参数但创建新的运行目录。只导入或
执行 ``autoenv list`` 不会触发本文中的远端操作。
"""

from autoenv import (
    CommandPhase,
    CommandProtocol,
    CommandResult,
    CommandStatus,
    DownloadResult,
    ExtractResult,
    FTPDefaults,
    RemoteDownloadResult,
    SSHDefaults,
    ScriptResult,
    TelnetDefaults,
    UploadResult,
    extra_file,
    match,
    package,
    register_func,
    register_script,
)


@register_script(
    name="template_host_and_transfer",
    description="Template: HDFS, selectors, extraction, SSH/SCP/SFTP, FTP and results",
    parameters=({
        "name": "release_channel",
        "type": "string",
        "label": "发布通道",
        "placeholder": "例如 debug 或 release",
        "required": True,
    },),
)
def template_host_and_transfer(ctx):
    # 声明区：所有选择器和连接必须只声明一次，并放在任何实际操作之前。
    main_package = package(
        "A1",
        alias="A1 主安装包",
        description="示例主安装包；留空链接时使用 config.json 的 link/base_link。",
    )
    manual_archive = extra_file("manual_bundle.tar.gz")
    extracted_driver = extra_file("driver.bin")
    firmware_image = match(r"^firmware-[\w.-]+\.bin$")
    generated_script = extra_file("template_install.sh")
    ssh_host = ctx.register_ssh_host(
        "template_ssh",
        resource_label="1260网口",
        alias="1260 管理网口",
        description="用于远端下载、上传文件、执行命令和检查业务状态。",
        defaults=SSHDefaults(
            host="192.0.2.10",
            port=22,
            username="root",
            password="",
            connect_timeout=30.0,
        ),
    )
    ftp_host = ctx.register_ftp_host(
        "template_ftp",
        resource_label="1712网口",
        alias="1712 FTP 网口",
        description="用于演示独立普通 FTP 上传；不会复用 SSH 凭据。",
        defaults=FTPDefaults(
            host="192.0.2.20",
            port=21,
            username="anonymous",
            password="",
            timeout=30.0,
            passive=True,
        ),
    )

    # Web：值来自 parameters；CLI：提示输入。required=True 时空值立即失败。
    release_channel = str(ctx.argument("release_channel", default="debug", required=True))
    if release_channel not in {"debug", "release"}:
        raise ValueError("release_channel must be debug or release")

    # HDFS 下载。成功时 local_file 指向本次 run_dir/packages 中经过大小校验的文件。
    download_result: DownloadResult = ctx.download_package(main_package)
    if not download_result.success:
        return download_result

    # resolve_local_file() 只解析本地选择器，不下载；package() 必须唯一匹配。
    resolved_package = ctx.resolve_local_file(main_package)
    expected_pattern = ctx.image_pattern_for("A1")
    if resolved_package.pattern != expected_pattern:
        raise RuntimeError("A1 local selector pattern does not match config.json")

    # 提取单文件；成功时 driver.bin 出现在本次 packages 根目录。
    extract_result: ExtractResult = ctx.extract_file_from(
        source=main_package,
        target_file="driver.bin",
    )
    if not extract_result.success:
        return extract_result

    # 提取目录；target_file 与 target_dir 必须二选一，目标必须是安全相对路径。
    extract_result = ctx.extract_file_from(
        source=manual_archive,
        target_dir="manual_bundle",
    )
    if not extract_result.success:
        return extract_result

    # SCP/SFTP 上传会创建远端目录并做 MD5 校验；overwrite=False 拒绝覆盖已有文件。
    upload_result: UploadResult = ssh_host.scp_upload(
        local_file=main_package,
        remote_dir="/opt/autoenv/packages",
        overwrite=True,
    )
    if not upload_result.success:
        return upload_result

    upload_result = ssh_host.sftp_upload(
        local_file=extracted_driver,
        remote_dir="/opt/autoenv/packages",
        overwrite=True,
    )
    if not upload_result.success:
        return upload_result

    # match() 在 packages 根目录按名称稳定排序后取首个正则匹配文件。
    upload_result = ssh_host.sftp_upload(
        local_file=firmware_image,
        remote_dir="/opt/autoenv/firmware",
        overwrite=False,
    )
    if not upload_result.success:
        return upload_result

    # 独立 FTP 只做上传及大小校验；FTPDefaults.passive 控制主动/被动模式。
    upload_result = ftp_host.upload(
        local_file=manual_archive,
        remote_dir="/incoming/autoenv",
        overwrite=True,
    )
    if not upload_result.success:
        return upload_result

    # 远端下载必须在 remote_file 与 pattern 中二选一；pattern 必须唯一匹配。
    exact_download: RemoteDownloadResult = ssh_host.scp_download(
        "/var/log/autoenv",
        remote_file="support.log",
        overwrite=True,
    )
    if not exact_download.success:
        return exact_download

    pattern_download: RemoteDownloadResult = ssh_host.sftp_download(
        "/var/log/autoenv",
        pattern=r"^report-[0-9]+\.zip$",
        overwrite=True,
    )
    if not pattern_download.success:
        return pattern_download

    # 成功的 RemoteDownloadResult 也是文件选择器，可直接交给上传接口。
    upload_result = ftp_host.upload(
        local_file=pattern_download,
        remote_dir="/incoming/reports",
        overwrite=True,
    )
    if not upload_result.success:
        return upload_result

    # S{选择器参数} 只替换为已上传到同一 SSH Host 的真实文件名。
    ctx.generate_sh_file(
        "template_install.sh",
        """#!/bin/sh
set -e
cd /opt/autoenv/packages
chmod +x "S{A1}"
./"S{A1}" --driver "S{driver.bin}"
""",
    )

    upload_result = ssh_host.sftp_upload(
        local_file=generated_script,
        remote_dir="/opt/autoenv",
        overwrite=True,
    )
    if not upload_result.success:
        return upload_result

    command_result: CommandResult = ssh_host.execute(
        "bash /opt/autoenv/S{template_install.sh}",
        timeout=600.0,
    )
    if not command_result.success:
        return command_result

    # execute_on_output() 在同一通道中等待关键词并发送原始 bytes，不自动加换行。
    command_result = ssh_host.execute_on_output(
        "bash /opt/autoenv/confirm.sh",
        keyword="Continue?",
        send_data=b"yes\n",
        timeout=60.0,
    )
    if not command_result.success:
        return command_result

    # CommandResult 提供协议、阶段、退出码、stdout/stderr/raw_output 和合并 output。
    if command_result.protocol != CommandProtocol.SSH:
        return command_result.with_failure("命令结果协议不是 SSH")
    if command_result.phase != CommandPhase.COMPLETE:
        return command_result.with_failure("命令未进入 COMPLETE 阶段")

    status_result = ssh_host.execute("cat /tmp/autoenv_status", timeout=30.0)
    if status_result.timed_out:
        return status_result
    if not status_result.success:
        return status_result
    if "READY" not in status_result.output:
        return status_result.with_failure(
            "业务状态中没有 READY",
            error_type="ENVIRONMENT_NOT_READY",
        )

    # 对会主动重启/断开连接的命令使用 expect_disconnect=True。
    reboot_result = ssh_host.execute(
        "sudo reboot",
        timeout=30.0,
        expect_disconnect=True,
    )
    return reboot_result


@register_script(
    name="template_console",
    description="Template: Telnet shell modes, command results and raw-byte interaction",
)
def template_console(ctx):
    console_script = extra_file("console_command.sh")
    upload_host = ctx.register_ssh_host(
        "template_console_ssh",
        resource_label="1260网口",
        alias="1260 串口配套网口",
        description="先上传串口命令文件，再供 Telnet 占位符解析使用。",
        defaults=SSHDefaults(host="192.0.2.10", username="root", password=""),
    )
    console = ctx.register_telnet(
        "template_console",
        resource_label="1260串口",
        alias="1260 调试串口",
        description="用于执行串口命令、判断未知结果并按启动提示发送 Ctrl+B。",
        defaults=TelnetDefaults(
            host="192.0.2.30",
            port=23,
            timeout=30.0,
            shell_mode="auto",
        ),
        uploaded_files_from="template_console_ssh",
    )

    upload_result = upload_host.sftp_upload(
        local_file=console_script,
        remote_dir="/opt/autoenv",
        overwrite=True,
    )
    if not upload_result.success:
        return upload_result

    # uploaded_files_from 让 Telnet 解析指定 SSH Host 已上传文件的 S{...} 占位符。
    # shell_mode 可为 auto/posix/prompt；Telnet 会复用会话并保留目录等 shell 状态。
    command_result: CommandResult = console.execute(
        "sh /opt/autoenv/S{console_command.sh}",
        timeout=30.0,
    )
    if command_result.status == CommandStatus.RESULT_UNKNOWN:
        if "READY" not in command_result.output:
            return command_result.with_failure("串口输出无法证明环境 READY")
    elif not command_result.success:
        return command_result

    # 命中关键词并成功发送 b"\x02" 只证明交互已发送，不证明设备进入目标模式。
    interaction_result = console.execute_on_output(
        "reboot",
        keyword="Press Ctrl+B",
        send_data=b"\x02",
        timeout=60.0,
    )
    return interaction_result


@register_script(
    name="template_post_start_functions",
    description="Template (CLI only): reusable register_func menu after startup",
)
def template_post_start_functions(ctx):
    ssh_host = ctx.register_ssh_host(
        "template_func_ssh",
        resource_label="1260网口",
        alias="1260 状态检查网口",
        description="供 CLI 启动后的固定检查和日志收集函数复用。",
        defaults=SSHDefaults(host="192.0.2.10", username="root", password=""),
    )

    initial_result = ssh_host.execute("test -f /tmp/autoenv_status", timeout=30.0)
    if not initial_result.success:
        return initial_result

    # 必须位于主流程末尾。主流程成功后 CLI 循环展示这些函数，选择 0 才退出。
    @register_func(
        name="template_check_status",
        description="读取并验证 READY 标记",
    )
    def template_check_status(_func_ctx):
        result = ssh_host.execute("cat /tmp/autoenv_status", timeout=30.0)
        if result.success and "READY" not in result.output:
            return result.with_failure("环境状态不是 READY")
        return result

    @register_func(
        name="template_collect_logs",
        description="列出待收集日志",
    )
    def template_collect_logs(_func_ctx):
        return ssh_host.execute("find /var/log/autoenv -maxdepth 1 -type f", timeout=30.0)

    return initial_result


@register_script(
    name="template_combined",
    description="Template: run registered host and console scripts serially",
)
def template_combined(_ctx):
    # 装饰后的函数返回独立 ScriptResult；每个子脚本有自己的 run_dir 与连接。
    host_result: ScriptResult = template_host_and_transfer()
    if not host_result.success:
        return host_result
    return template_console()
