"""Settings stay out of the normal import/reader flow in both frontends."""

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1] / "product_log"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.elements[values["id"]] = (tag, values, list(self.stack))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, values.get("id")))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


@pytest.mark.parametrize("frontend", ["static", "standalone"])
def test_configuration_and_source_options_are_inside_closed_settings(frontend):
    page = Elements()
    page.feed((ROOT / frontend / "index.html").read_text(encoding="utf-8"))
    tag, attrs, _ = page.elements["settingsDialog"]
    assert tag == "dialog" and "open" not in attrs
    assert attrs["aria-labelledby"] == "settingsTitle"
    assert page.elements["openSettings"][0] == "button"
    for name in ("analysisConfig", "sourceOptions", "importConfiguration", "downloadConfiguration", "moduleTableBody", "copyPatterns", "renameMap"):
        assert ("dialog", "settingsDialog") in page.elements[name][2], name
    assert ("dialog", "settingsDialog") not in page.elements["startTask"][2]
    assert ("dialog", "settingsDialog") not in page.elements["logViewport"][2]


@pytest.mark.parametrize("frontend", ["static", "standalone"])
def test_import_uses_the_explicit_configuration_snapshot(frontend):
    script = (ROOT / frontend / "app.js").read_text(encoding="utf-8")
    assert "analysis_config: analysisConfig" in script
    assert "source_ids" in script
    assert "ProductLogConfig.normalize" in script
    assert "diag_directory_pattern: $('directoryPattern').value" not in script
    assert "file_patterns: $('filePatterns').value" not in script
    assert "showModal()" in script
    assert "settingsDialog').addEventListener('close'" in script


@pytest.mark.parametrize("frontend", ["static", "standalone"])
def test_real_browser_source_choices_empty_config_and_settings_persistence(frontend):
    node = shutil.which("node")
    playwright = Path(os.environ.get("USERPROFILE", Path.home())) / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"
    if not node or not playwright.is_dir():
        pytest.skip("Bundled Playwright and Node are required for browser settings coverage")
    browser = next((path for path in (
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
    ) if path.is_file()), None)
    if browser is None:
        pytest.skip("A local Chromium browser is required for browser settings coverage")
    harness = r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const {chromium} = require(request.playwright);
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:request.browser});
  try {
    const page = await browser.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let html = request.html;
    if (request.frontend === 'standalone') html = html.replace('<!--OFFLINE_APP-->', '<script src="/product-logs/configuration.js"></script><script src="/product-logs/app.js"></script>');
    await page.route('http://product-log.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/configuration.js')) return route.fulfill({contentType:'text/javascript',body:'globalThis.ProductLogDefaultConfig='+JSON.stringify(request.config)+';\n'+request.configuration});
      if (path.endsWith('/app.js')) return route.fulfill({contentType:'text/javascript',body:request.app});
      if (path.endsWith('/styles.css')) return route.fulfill({contentType:'text/css',body:request.css});
      if (path.startsWith('/api/product-logs/')) {
        const value = path.endsWith('/configuration') ? {analysis_config:request.config} : path.endsWith('/batches') ? {batches:[],active:null} : {storage:{usage_bytes:0,limit_bytes:3221225472}};
        return route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
      }
      return route.fulfill({contentType:'text/html',body:html});
    });
    await page.goto('http://product-log.test/product-logs/');
    await page.waitForFunction(() => !document.getElementById('openSettings').disabled);
    assert.equal(await page.locator('#settingsDialog').isVisible(), false);
    assert.equal(await page.locator('#moduleTableBody').isVisible(), false);
    await page.locator('#openSettings').click();
    assert.equal(await page.locator('#settingsDialog').isVisible(), true);
    assert.equal(await page.locator('#sourceOptions input:checked').count(), 3);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.equal(await page.locator('#closeSettings').evaluate(element => {const range=document.createRange();range.selectNodeContents(element);return range.getClientRects().length;}), 1);
    await page.setViewportSize({width:1280,height:900});
    const config = structuredClone(request.config), alternative = structuredClone(config.sources.find(item => item.id === 'dmesg'));
    alternative.id = 'kbox'; alternative.name = 'kbox selectable fixture'; alternative.file_patterns = ['cpdt_kbox*'];
    config.sources.push(alternative);
    await page.locator('#configurationDetails').evaluate(element => element.open = true);
    await page.locator('#analysisConfig').fill(JSON.stringify(config));
    await page.locator('#analysisConfig').dispatchEvent('change');
    const group = page.locator('[data-rule-id="kernel_driver"]');
    await group.locator('input[value="kbox"]').check();
    let selected = JSON.parse(await page.locator('#analysisConfig').inputValue()).rules.find(rule => rule.id === 'kernel_driver').source_ids;
    assert.deepEqual(selected, ['dmesg','kbox']);
    await group.locator('input[value="dmesg"]').uncheck();
    await group.locator('input[value="kbox"]').uncheck();
    selected = JSON.parse(await page.locator('#analysisConfig').inputValue()).rules.find(rule => rule.id === 'kernel_driver').source_ids;
    assert.deepEqual(selected, []);
    await page.locator('#saveConfiguration').click();
    assert.equal(await page.locator('#settingsDialog').isVisible(), false);
    assert.equal(await page.locator('#openSettings').evaluate(element => element === document.activeElement), true);
    await page.reload(); await page.waitForFunction(() => !document.getElementById('openSettings').disabled);
    await page.locator('#openSettings').click();
    selected = JSON.parse(await page.locator('#analysisConfig').inputValue()).rules.find(rule => rule.id === 'kernel_driver').source_ids;
    assert.deepEqual(selected, []);
    await page.locator('#configurationDetails').evaluate(element => element.open = true);
    await page.locator('#analysisConfig').fill('null'); await page.locator('#analysisConfig').dispatchEvent('change');
    await page.locator('#saveConfiguration').click();
    assert.equal(await page.locator('#settingsDialog').isVisible(), true);
    assert.match(await page.locator('#configurationError').innerText(), /JSON/);
    assert.equal(await page.locator('#analysisConfig').inputValue(), 'null');
    const empty = {...config,rules:[]};
    await page.locator('#configurationInput').setInputFiles({name:'empty-rules.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(empty))});
    await page.waitForFunction(() => document.getElementById('sourceOptions').textContent.includes('当前没有组件规则'));
    const downloaded = page.waitForEvent('download'); await page.locator('#downloadConfiguration').click();
    assert.equal((await downloaded).suggestedFilename(),'product-log-configuration.json');
    await page.locator('#saveConfiguration').click();
    await page.reload(); await page.waitForFunction(() => !document.getElementById('openSettings').disabled);
    assert.deepEqual(JSON.parse(await page.locator('#analysisConfig').inputValue()).rules, []);
    await page.locator('#openSettings').click(); await page.keyboard.press('Escape');
    assert.equal(await page.locator('#settingsDialog').isVisible(), false);
    await page.evaluate(frontend => localStorage.setItem('autoenv.product-log.analysis-config.v1.'+frontend, 'null'), request.frontend);
    await page.reload(); await page.waitForFunction(() => !document.getElementById('openSettings').disabled);
    await page.locator('#openSettings').click();
    assert.equal(await page.locator('#analysisConfig').inputValue(), 'null');
    assert.match(await page.locator('#configurationError').innerText(), /JSON/);
    await page.locator('#resetConfiguration').click(); await page.locator('#saveConfiguration').click();
    assert.equal(JSON.parse(await page.locator('#analysisConfig').inputValue()).rules.length, request.config.rules.length);
    assert.deepEqual(errors, []);
    process.stdout.write(JSON.stringify({frontend:request.frontend,sourceSelection:true,emptyRules:true,nullRejected:true,persistence:true,download:true,escape:true}));
  } finally {await browser.close();}
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
"""
    request = {
        "playwright": str(playwright), "browser": str(browser), "frontend": frontend,
        "config": json.loads((ROOT / "default_config.json").read_text(encoding="utf-8")),
        "configuration": (ROOT / "standalone/configuration.js").read_text(encoding="utf-8"),
        "html": (ROOT / frontend / "index.html").read_text(encoding="utf-8"),
        "app": (ROOT / frontend / "app.js").read_text(encoding="utf-8"),
        "css": (ROOT / frontend / "styles.css").read_text(encoding="utf-8"),
    }
    completed = subprocess.run([node, "-e", harness], input=json.dumps(request), encoding="utf-8", capture_output=True, timeout=90)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert all(value is True for key, value in result.items() if key != "frontend")
