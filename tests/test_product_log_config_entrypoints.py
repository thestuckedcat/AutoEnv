"""Real HTTP, CLI and bundle boundaries for explicit analysis settings."""
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from product_log.__main__ import main
from product_log.configuration import default_config
from test_product_log_web import product_http, _completed
from test_product_log_standalone_build import builder_module


def test_http_configuration_and_shared_javascript_are_available(product_http):
    _, request, _, _ = product_http
    assert request("/api/product-logs/configuration")["analysis_config"] == default_config()
    assert "ProductLogConfig" in request("/product-logs/configuration.js", raw=True)


def test_http_empty_rules_are_used_and_saved_without_hidden_defaults(product_http):
    root, request, _, _ = product_http
    source = root / "input" / "varlog"
    source.mkdir(parents=True)
    (source / "cpdt_dmesg_current.log").write_text("udk text\n")
    config = default_config()
    config["rules"] = []
    task = request("/api/product-logs/start", {"input_path": str(source.parent), "analysis_config": config})
    result = _completed(request, task["batch_id"])
    assert result["status"] == "ready", result
    assert result["manifest"]["analysis_config"]["rules"] == []
    assert result["manifest"]["matched_files"] == 0


def test_http_explicit_null_config_does_not_restore_defaults(product_http):
    root, request, _, _ = product_http
    source = root / "input"
    source.mkdir()
    with pytest.raises(HTTPError) as error:
        request("/api/product-logs/start", {"input_path": str(source), "analysis_config": None})
    assert error.value.code == 400


def test_cli_accepts_config_file_and_preserves_empty_source_selection(tmp_path, capsys):
    source = tmp_path / "input"
    source.mkdir()
    settings = default_config()
    settings["rules"] = []
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps(settings), encoding="utf-8")
    assert main([str(source), "--output", str(tmp_path / "batch"), "--config", str(config_path)]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["matched_files"] == 0
    assert manifest["analysis_config"] == settings


def test_cli_rejects_null_config_file(tmp_path, capsys):
    config_path = tmp_path / "settings.json"
    config_path.write_text("null", encoding="utf-8")
    assert main([str(tmp_path), "--config", str(config_path)]) == 2
    assert "configuration" in capsys.readouterr().err.lower()


def test_offline_worker_and_app_embed_same_default_snapshot(tmp_path):
    module = builder_module()
    source = module.worker_source()
    assert "globalThis.ProductLogDefaultConfig=" in source
    assert source.index("globalThis.ProductLogDefaultConfig=") < source.index("global.ProductLogConfig =")
    page = tmp_path / "page.html"
    module.build_page(page)
    html = page.read_text(encoding="utf-8")
    assert "globalThis.ProductLogDefaultConfig=" in html
