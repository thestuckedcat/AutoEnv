from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from autoenv import create_file, get_file
from autoenv.remote_catalog import read_catalog, save_connection
from autoenv.script_files import inspect_script
from autoenv.script_session import ExecutionSession, current_session, validate_arguments


def descriptor(root):
    script = root / "flow.py"
    script.write_text(
        'from autoenv import init_script, get_remote_info\n'
        'def main():\n'
        '    paths = init_script()\n'
        '    first = get_remote_info("ssh", [("双机","主"), ("单机","主")])\n'
        '    second = get_remote_info("ssh", [("双机","备")])\n',
        encoding="utf-8",
    )
    for topology, name, port in (("双机","主",1001),("双机","备",1002),("单机","主",1003)):
        save_connection(root, topology, {"name":name,"type":"ssh","host":"127.0.0.1","port":port})
    return inspect_script(script)


def complete(session):
    session.initialize()
    session.finish(success=True, status="success")
    session.close()


def test_topology_intersection_whitelist_and_revision_history(tmp_path):
    description = descriptor(tmp_path)
    first = ExecutionSession(tmp_path)
    first.prepare(description, {"non_interactive":True, "selected_topology":"双机", "bindings":{"first":"主","second":"备"}})
    assert first.topology == "双机"
    complete(first)
    replay = ExecutionSession(tmp_path)
    replay.prepare(description, {"non_interactive":True, "history_policy":"require"})
    assert replay.bindings == {"first":"主","second":"备"}
    complete(replay)
    config = read_catalog(tmp_path)["双机"]["备"]
    config["port"] = 2002
    save_connection(tmp_path, "双机", config)
    stale = ExecutionSession(tmp_path)
    try:
        with pytest.raises(ValueError, match="binding: second"):
            stale.prepare(description, {"non_interactive":True,"history_policy":"require"})
        assert not stale.runtime_path.exists()
    finally:
        stale.close()


def test_complete_requests_ignore_history_and_cannot_select_another_role(tmp_path):
    description = descriptor(tmp_path)
    first = ExecutionSession(tmp_path)
    first.prepare(description, {"non_interactive":True,"selected_topology":"双机","bindings":{"first":"主","second":"备"}})
    complete(first)
    for request in (
        {"non_interactive":True,"selected_topology":"双机","bindings":{"first":"主"}},
        {"non_interactive":True,"selected_topology":"双机","bindings":{"first":"备","second":"备"}},
        {"non_interactive":True,"selected_topology":"单机","bindings":{"first":"主","second":"备"}},
    ):
        session = ExecutionSession(tmp_path)
        try:
            with pytest.raises(ValueError):
                session.prepare(description, request)
            assert not session.runtime_path.exists()
            assert session.remotes == []
        finally:
            session.close()


def test_role_layout_change_invalidates_saved_bindings(tmp_path):
    description = descriptor(tmp_path)
    session = ExecutionSession(tmp_path)
    session.prepare(description, {"non_interactive":True,"selected_topology":"双机","bindings":{"first":"主","second":"备"}})
    complete(session)
    path = tmp_path / "flow.py"
    path.write_text(path.read_text(encoding="utf-8").replace("first =", "other ="), encoding="utf-8")
    changed = inspect_script(path)
    replay = ExecutionSession(tmp_path)
    try:
        with pytest.raises(ValueError, match="no valid script history"):
            replay.prepare(changed, {"non_interactive":True,"history_policy":"require"})
    finally:
        replay.close()


def test_context_ownership_isolated_for_nested_and_explicit_thread_sessions(tmp_path):
    script = tmp_path / "files.py"
    script.write_text("from autoenv import init_script\ndef main():\n    init_script()\n", encoding="utf-8")
    description = inspect_script(script)
    parent = ExecutionSession(tmp_path)
    child = ExecutionSession(tmp_path)
    for session in (parent, child):
        session.prepare(description, {"non_interactive":True})
        session.initialize()
    def work(session, text):
        with session.activate():
            return create_file(session.runtime_path, "same.txt", text).path
    try:
        with parent.activate():
            first = create_file(parent.runtime_path, "same.txt", "parent")
            with child.activate():
                second = create_file(child.runtime_path, "same.txt", "child")
            assert current_session() is parent
            assert get_file(parent.runtime_path, "same.txt").path == first.path
        with ThreadPoolExecutor(max_workers=2) as pool:
            paths = list(pool.map(lambda pair: work(*pair), ((parent,"P"),(child,"C"))))
        assert first.path != second.path and paths[0].read_text() == "P" and paths[1].read_text() == "C"
        assert current_session() is None
    finally:
        parent.close()
        child.close()


@pytest.mark.parametrize("kind,value", [("bool","yes"),("int",True),("int",1.9),("float","nan"),("dict","{}"),("str",4)])
def test_parameters_reject_ambiguous_or_invalid_types(kind, value):
    with pytest.raises((TypeError, ValueError)):
        validate_arguments([{"name":"value","type":kind,"required":True}], {"value":value}, non_interactive=True)


def test_script_paths_close_in_hosted_context_preserves_final_audit(tmp_path):
    script = tmp_path / "files.py"
    script.write_text("from autoenv import init_script\ndef main():\n    init_script()\n", encoding="utf-8")
    session = ExecutionSession(tmp_path)
    session.hosted = True
    session.prepare(inspect_script(script), {"non_interactive":True})
    with session.activate():
        paths = session.initialize()
        paths.close()
        with pytest.raises(RuntimeError, match="closed"):
            create_file(paths.runtime_path, "late.txt", "x")
        session.finish(success=True, status="success")
    session.close()
    assert json.loads(session.result_path.read_text(encoding="utf-8"))["success"] is True


def test_optional_omission_remains_valid_during_rerun(tmp_path):
    script = tmp_path / "optional.py"
    script.write_text('from autoenv import init_script\nSCRIPT_ARGUMENTS=[{"name":"note","type":"str"}]\ndef main():\n    init_script()\n', encoding="utf-8")
    description = inspect_script(script)
    first = ExecutionSession(tmp_path)
    first.prepare(description, {"non_interactive":True})
    assert first.arguments == {"note":None}
    complete(first)
    replay = ExecutionSession(tmp_path)
    replay.prepare(description, {"non_interactive":True,"history_policy":"require"})
    assert replay.arguments == {"note":None}
    complete(replay)


def test_positional_only_main_is_rejected_during_discovery(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text('from autoenv import init_script\ndef main(value, /):\n    init_script()\n', encoding="utf-8")
    with pytest.raises(ValueError, match="no parameters"):
        inspect_script(script)


def test_duplicate_remote_call_argument_is_rejected_before_any_connection(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text('from autoenv import init_script, get_remote_info\ndef main():\n    init_script()\n    dut=get_remote_info("ssh", [("lab","dut")], required_type="ssh")\n', encoding="utf-8")
    with pytest.raises(ValueError, match="supplied twice"):
        inspect_script(script)


def test_static_with_scope_has_the_same_role_contract(tmp_path):
    script = tmp_path / "with.py"
    script.write_text('from autoenv import init_script, get_remote_info\ndef main():\n    with init_script() as paths:\n        dut=get_remote_info("ssh", [("lab","dut")])\n', encoding="utf-8")
    value = inspect_script(script)
    assert value["slots"][0]["role"] == "dut" and value["slots"][0]["line"] == 4


def test_init_script_shape_is_prechecked_before_network(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text('from autoenv import init_script\ndef main():\n    init_script(42)\n', encoding="utf-8")
    with pytest.raises(ValueError, match="no arguments"):
        inspect_script(script)
