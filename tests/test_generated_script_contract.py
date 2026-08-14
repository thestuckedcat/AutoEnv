from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = (
    ROOT
    / ".agents"
    / "skills"
    / "autoenv-script-generator"
    / "scripts"
    / "validate_environment_script.py"
)
SPEC = importlib.util.spec_from_file_location("autoenv_script_contract", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


def _validate_source(tmp_path: Path, source: str):
    path = tmp_path / "generated.py"
    path.write_text(source, encoding="utf-8")
    return VALIDATOR.validate_path(path)


def test_all_repository_environment_scripts_satisfy_the_shared_contract() -> None:
    violations = [
        item
        for path in sorted((ROOT / "scripts").glob("*.py"))
        if path.name != "__init__.py"
        for item in VALIDATOR.validate_path(path)
    ]
    assert violations == [], "\n".join(map(str, violations))


def test_contract_rejects_a_command_list_instead_of_complete_shell_text(tmp_path) -> None:
    violations = _validate_source(
        tmp_path,
        '''
from autoenv import extra_file, register_script

@register_script("bad")
def bad(ctx):
    generated = extra_file("install.sh")
    ctx.generate_sh_file("install.sh", ["echo first", "echo second"])
''',
    )
    assert any("one complete literal string" in item.message for item in violations)


def test_contract_rejects_inline_selectors_and_generate_before_upload(tmp_path) -> None:
    violations = _validate_source(
        tmp_path,
        '''
from autoenv import SSHDefaults, extra_file, package, register_script

@register_script("bad")
def bad(ctx):
    archive = package("api")
    generated = extra_file("install.sh")
    host = ctx.register_ssh_host("dut", resource_label="1260网口", defaults=SSHDefaults(host="192.0.2.1"))
    ctx.download_package(package("api"))
    ctx.generate_sh_file("install.sh", "tar -xf S{api}")
    host.sftp_upload(local_file=archive, remote_dir="/tmp")
''',
    )
    messages = [item.message for item in violations]
    assert any("package() must be assigned once" in item for item in messages)
    assert any("before generation" in item for item in messages)


def test_contract_rejects_execute_on_a_different_upload_target(tmp_path) -> None:
    violations = _validate_source(
        tmp_path,
        '''
from autoenv import SSHDefaults, extra_file, register_script

@register_script("bad")
def bad(ctx):
    archive = extra_file("api.tgz")
    first = ctx.register_ssh_host("first", resource_label="1260网口", defaults=SSHDefaults(host="192.0.2.1"))
    second = ctx.register_ssh_host("second", resource_label="1712网口", defaults=SSHDefaults(host="192.0.2.2"))
    first.sftp_upload(local_file=archive, remote_dir="/tmp")
    return second.execute("tar -xf S{api.tgz}")
''',
    )
    assert any("command target" in item.message for item in violations)


def test_contract_checks_execute_on_output_upload_target(tmp_path) -> None:
    violations = _validate_source(
        tmp_path,
        '''
from autoenv import SSHDefaults, extra_file, register_script

@register_script("bad")
def bad(ctx):
    archive = extra_file("api.tgz")
    first = ctx.register_ssh_host("first", resource_label="1260网口", defaults=SSHDefaults(host="192.0.2.1"))
    second = ctx.register_ssh_host("second", resource_label="1712网口", defaults=SSHDefaults(host="192.0.2.2"))
    first.sftp_upload(local_file=archive, remote_dir="/tmp")
    return second.execute_on_output(
        "tar -xf S{api.tgz}",
        keyword="Continue?",
        send_data=b"y\\n",
    )
''',
    )
    assert any("command target" in item.message for item in violations)


def test_contract_accepts_an_exact_regex_selector_with_braces(tmp_path) -> None:
    violations = _validate_source(
        tmp_path,
        r'''
from autoenv import SSHDefaults, match, register_script

@register_script("regex", resources=({"name": "dut", "alias": "主机网口", "description": "上传并执行测试包", "label": "1260网口", "protocol": "ssh"},))
def regex(ctx):
    archive = match(r"^api-\d{2}\.tgz$")
    host = ctx.register_ssh_host("dut", resource_label="1260网口", defaults=SSHDefaults(host="192.0.2.1"))
    result = host.sftp_upload(local_file=archive, remote_dir="/tmp")
    if not result.success:
        return result
    return host.execute(r"tar -xf S{^api-\d{2}\.tgz$}")
''',
    )
    assert violations == [], "\n".join(map(str, violations))


def test_contract_requires_resource_metadata_on_each_connection(tmp_path) -> None:
    violations = _validate_source(
        tmp_path,
        '''
from autoenv import SSHDefaults, register_script

@register_script("bad")
def bad(ctx):
    host = ctx.register_ssh_host("dut", defaults=SSHDefaults(host="192.0.2.1"))
''',
    )
    messages = [item.message for item in violations]
    assert any("literal resource_label" in item for item in messages)
    assert any("register_script resources" in item for item in messages)


def test_contract_requires_alias_and_description_for_web_inputs(tmp_path) -> None:
    violations = _validate_source(
        tmp_path,
        '''
from autoenv import SSHDefaults, register_script

@register_script(
    "bad_prompts",
    packages=("A1",),
    resources=({"name": "dut", "label": "1260网口", "protocol": "ssh"},),
)
def bad_prompts(ctx):
    host = ctx.register_ssh_host("dut", resource_label="1260网口", defaults=SSHDefaults(host="192.0.2.1"))
''',
    )
    messages = [item.message for item in violations]
    assert any("alias, description" in item for item in messages)
    assert any("package requires" in item for item in messages)


def test_contract_rejects_module_level_and_non_final_registered_funcs(tmp_path) -> None:
    violations = _validate_source(
        tmp_path,
        '''
from autoenv import register_func, register_script

@register_func("module_level")
def module_level(ctx):
    return None

@register_script("bad")
def bad(ctx):
    @register_func("check")
    def check():
        return None
    ctx.generate_sh_file("late.sh", "true")
''',
    )
    messages = [item.message for item in violations]
    assert any("must be nested" in item for item in messages)
    assert any("exactly one RunContext" in item for item in messages)
    assert any("final main-flow" in item for item in messages)
