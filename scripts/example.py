"""Copyable examples for registering standalone and combined environments."""

from autoenv import (
    CommandStatus,
    SSHDefaults,
    TelnetDefaults,
    extra_file,
    match,
    package,
    register_script,
)


@register_script(
    name="example_host_environment",
    description="Example: download, extract, upload and run SSH commands",
)
def example_host_environment(ctx):
    host = ctx.register_ssh_host(
        "example_host",
        defaults=SSHDefaults(
            host="192.168.1.100",
            port=22,
            username="root",
            password="root",
            connect_timeout=30.0,
        ),
    )

    result = ctx.download_package(package("A1"))
    if not result.success:
        return result

    # Extraction is always explicit. Change the target to a real file in A1.
    result = ctx.extract_file_from(
        source=package("A1"),
        target_file="driver.bin",
    )
    if not result.success:
        return result

    # target_dir is mutually exclusive with target_file. This manual archive must
    # already be present in the packages directory printed at script startup.
    result = ctx.extract_file_from(
        source=extra_file("manual_bundle.tar.gz"),
        target_dir="firmware",
    )
    if not result.success:
        return result

    result = host.scp_upload(
        local_file=package("A1"),
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    result = host.sftp_upload(
        local_file=extra_file("driver.bin"),
        remote_dir="/root/autoEnv",
        overwrite=True,
    )
    if not result.success:
        return result

    result = host.sftp_upload(
        local_file=match(r"^firmware-.*\.bin$"),
        remote_dir="/root/autoEnv/firmware",
        overwrite=False,
    )
    if not result.success:
        return result

    result = host.execute(
        "bash /root/autoEnv/install.sh",
        timeout=600,
    )
    if not result.success:
        return result

    status = host.execute("cat /tmp/env_status", timeout=30)
    if status.success and "READY" not in status.output:
        return status.with_failure("environment status is not READY")
    return status


@register_script(
    name="example_console_environment",
    description="Example: Telnet auto detection and an expected reboot disconnect",
)
def example_console_environment(ctx):
    console = ctx.register_telnet(
        "example_console",
        defaults=TelnetDefaults(
            host="192.168.1.200",
            port=23,
            timeout=30.0,
            shell_mode="auto",
        ),
    )

    result = console.execute(
        "source /root/start_slave.sh",
        timeout=300,
    )
    if result.status == CommandStatus.RESULT_UNKNOWN:
        if "READY" not in result.output:
            return result.with_failure("Telnet output does not contain READY")
    elif not result.success:
        return result

    return console.execute(
        "reboot",
        timeout=60,
        expect_disconnect=True,
    )


@register_script(
    name="example_combined_environment",
    description="Example: run two registered scripts serially and independently",
)
def example_combined_environment(ctx):
    host_result = example_host_environment()
    if not host_result.success:
        return host_result
    return example_console_environment()
