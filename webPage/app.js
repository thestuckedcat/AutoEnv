"use strict";
const $ = id => document.getElementById(id);
const state = {catalog: {}, scripts: [], generation: 0, cursor: 0, running: false, preview: 0};
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}
function toast(message) { $("toast").textContent = message; $("toast").style.display = "block"; clearTimeout(toast.timer); toast.timer = setTimeout(() => {$("toast").style.display = "none";}, 3500); }
function option(select, value, label = value) { const node = document.createElement("option"); node.value = value; node.textContent = label; select.append(node); }
function currentScript() { return state.scripts.find(item => item.script === $("script").value); }
function addField(container, text, element) { const label = document.createElement("label"); label.append(document.createTextNode(text), element); container.append(label); return element; }
function section(container, title) { const node = document.createElement("p"); node.className = "input-section"; node.textContent = title; container.append(node); }
async function loadCatalog() { state.catalog = (await api("/api/environments")).topologies; renderCatalog(); }
function renderCatalog() {
  const list = $("environmentList"); list.replaceChildren();
  for (const [topology, connections] of Object.entries(state.catalog)) {
    const heading = document.createElement("h3"); heading.textContent = topology; list.append(heading);
    for (const config of Object.values(connections)) {
      const button = document.createElement("button"); button.type = "button"; button.className = "connection";
      const name = document.createElement("strong"); name.textContent = config.name;
      const detail = document.createElement("small"); detail.textContent = `${config.type.toUpperCase()} · ${config.host}:${config.port}${config.enabled ? "" : " · 已停用"}`;
      button.append(name, detail); button.onclick = () => { for (const item of list.querySelectorAll("button")) item.classList.remove("active"); button.classList.add("active"); editConnection(topology, config); }; list.append(button);
    }
  }
  if (!list.childNodes.length) { const text = document.createElement("p"); text.className = "empty"; text.textContent = "先创建一个组网和连接，再由脚本白名单引用它们。"; list.append(text); }
}
function connectionTypeChanged(defaultPort = false) {
  const form = $("environmentForm"), kind = form.elements.type.value;
  for (const type of ["ssh", "telnet", "ftp"]) $(`${type}Options`).hidden = type !== kind;
  if (defaultPort) {form.elements.port.value = {ssh: 22, telnet: 23, ftp: 21}[kind]; form.elements.username.value = {ssh: "root", telnet: "", ftp: "anonymous"}[kind];}
}
function editConnection(topology, config) {
  const form = $("environmentForm"); form.reset();
  for (const [key, value] of Object.entries({...config, topology})) { const input = form.elements.namedItem(key); if (!input) continue; if (input.type === "checkbox") input.checked = value; else input.value = value; }
  if (config.type === "telnet") form.elements.telnet_timeout.value = config.timeout;
  if (config.type === "ftp") form.elements.ftp_timeout.value = config.timeout;
  connectionTypeChanged(); $("saveState").textContent = "保存将更新同一组网中同名连接。";
}
function blankConnection() { $("environmentForm").reset(); connectionTypeChanged(true); $("saveState").textContent = "保存后，脚本创建对象时才会连接并自检。"; for (const item of $("environmentList").querySelectorAll("button")) item.classList.remove("active"); }
async function saveConnection(event) {
  event.preventDefault(); const form = event.currentTarget, kind = form.elements.type.value;
  const connection = {name: form.elements.name.value.trim(), type: kind, host: form.elements.host.value.trim(), port: Number(form.elements.port.value), username: form.elements.username.value, password: form.elements.password.value, enabled: form.elements.enabled.checked};
  if (kind === "ssh") {connection.upload_type = form.elements.upload_type.value; connection.connect_timeout = Number(form.elements.connect_timeout.value);}
  if (kind === "telnet") {connection.shell_mode = form.elements.shell_mode.value; connection.timeout = Number(form.elements.telnet_timeout.value); for (const key of ["probe_command", "probe_keyword", "login_prompt", "password_prompt"]) if (form.elements[key].value) connection[key] = form.elements[key].value;}
  if (kind === "ftp") {connection.passive = form.elements.passive.value === "true"; connection.timeout = Number(form.elements.ftp_timeout.value);}
  try { await api("/api/environments", {topology: form.elements.topology.value.trim(), connection}); await loadCatalog(); await loadScripts(); $("saveState").textContent = "连接已保存。"; toast("连接已保存"); } catch (error) { $("saveState").textContent = `保存失败：${error.message}`; }
}
async function loadScripts() {
  const previous = $("script").value; state.scripts = (await api("/api/scripts")).scripts; $("script").replaceChildren();
  for (const item of state.scripts) option($("script"), item.script, item.script);
  if (state.scripts.some(item => item.script === previous)) $("script").value = previous;
  renderScript();
}
function renderScript() {
  const script = currentScript(); $("topology").replaceChildren(); $("packageFields").replaceChildren(); $("argumentFields").replaceChildren(); $("formError").textContent = script?.error || "";
  $("scriptDescription").textContent = script?.description || script?.path || "将包含 init_script() 的 Python 文件放入 scripts/。";
  $("startRun").disabled = !script || !!script.error || state.running;
  $("topologyField").hidden = !(script?.slots?.length); $("topology").required = !!script?.slots?.length;
  for (const topology of script?.topologies || []) option($("topology"), topology);
  if (script?.packages?.length) section($("packageFields"), "HDFS 下载路径（可留空沿用 config.json）");
  for (const name of script?.packages || []) { const input = document.createElement("input"); input.dataset.package = name; input.placeholder = "例如 /projects/releases/build-123"; addField($("packageFields"), name, input); }
  const schemas = Array.isArray(script?.arguments) ? script.arguments : Object.entries(script?.arguments || {}).map(([name, schema]) => ({name, ...schema}));
  if (schemas.length) section($("argumentFields"), "脚本参数");
  for (const schema of schemas) {
    const input = document.createElement(schema.type === "bool" ? "select" : "input"); input.dataset.argument = schema.name; input.dataset.valueType = schema.type || "str"; input.required = !!schema.required;
    if (schema.type === "bool") {option(input, "", "请选择"); option(input, "true", "true"); option(input, "false", "false");}
    if (["int", "float"].includes(schema.type)) {input.type = "number"; input.step = schema.type === "int" ? "1" : "any";}
    if (schema.default !== undefined) input.value = typeof schema.default === "object" ? JSON.stringify(schema.default) : String(schema.default);
    input.placeholder = schema.description || (["list", "dict"].includes(schema.type) ? "JSON" : ""); addField($("argumentFields"), schema.name, input);
  }
  renderBindings();
}
function renderBindings() {
  $("bindingFields").replaceChildren(); const script = currentScript(), topology = $("topology").value;
  for (const slot of script?.slots || []) {
    const select = document.createElement("select"); select.dataset.role = slot.role; select.required = true;
    for (const [group, name] of slot.allowed_envs) { const config = state.catalog[group]?.[name]; if (group === topology && config?.enabled && config.type === slot.required_type) option(select, name); }
    addField($("bindingFields"), `${slot.role} · ${slot.required_type.toUpperCase()}`, select);
  }
  previewCommand();
}
function requestValue() {
  const data = {script: $("script").value, selected_topology: $("topology").value, bindings: {}, packages: {}, arguments: {}};
  for (const input of $("bindingFields").querySelectorAll("[data-role]")) data.bindings[input.dataset.role] = input.value;
  for (const input of $("packageFields").querySelectorAll("[data-package]")) if (input.value) data.packages[input.dataset.package] = input.value;
  for (const input of $("argumentFields").querySelectorAll("[data-argument]")) {
    if (input.value === "" && !input.required) continue;
    const type = input.dataset.valueType; let value = input.value;
    if (["list", "dict"].includes(type)) value = JSON.parse(value);
    if (["int", "float"].includes(type)) value = Number(value);
    if (type === "bool") value = value === "true";
    data.arguments[input.dataset.argument] = value;
  }
  return data;
}
async function previewCommand() {
  const token = ++state.preview;
  if (!currentScript() || currentScript().error) {$("command").textContent = "先完成环境配置与脚本选择"; return;}
  try { const value = await api("/api/run/command", requestValue()); if (token === state.preview) $("command").textContent = value.command; } catch (error) { if (token === state.preview) $("command").textContent = error.message; }
}
function runStatus(text, kind = "") { $("runStatus").textContent = text; $("runStatus").className = `badge ${kind}`; }
async function startRun(event) {
  event.preventDefault();
  try { const value = await api("/api/run/start", requestValue()); state.generation = value.generation; state.cursor = 0; state.running = true; $("startRun").disabled = true; $("output").textContent = ""; $("command").textContent = value.command; $("logPath").textContent = "正在创建日志…"; $("openLog").disabled = $("openRun").disabled = true; runStatus("运行中", "running"); pollEvents(); } catch (error) { $("formError").textContent = error.message; }
}
async function pollEvents() {
  try {
    const result = await api(`/api/run/events?cursor=${state.cursor}`);
    if (state.generation && result.generation !== state.generation) return;
    state.cursor = result.next;
    for (const event of result.events) {
      if (event.type === "output") {$("output").textContent += event.text + "\n"; $("output").scrollTop = $("output").scrollHeight;}
      if (event.log_path) {$("logPath").textContent = event.log_path; $("openLog").disabled = $("openRun").disabled = false;}
      if (event.type === "complete") {state.running = false; $("startRun").disabled = false; runStatus(event.success ? "成功" : `失败 · 退出码 ${event.exit_code}`, event.success ? "success" : "failed");}
    }
  } catch (error) {toast(`读取输出失败：${error.message}`);}
  if (state.running) setTimeout(pollEvents, 250);
}
for (const button of document.querySelectorAll("nav button")) button.onclick = () => { for (const item of document.querySelectorAll("nav button")) item.classList.toggle("active", item === button); for (const page of document.querySelectorAll(".page")) page.classList.toggle("active", page.id === `page-${button.dataset.page}`); };
$("environmentForm").onsubmit = saveConnection;
$("environmentForm").elements.type.onchange = () => connectionTypeChanged(true);
$("newConnection").onclick = blankConnection;
$("script").onchange = renderScript; $("topology").onchange = renderBindings;
$("runForm").addEventListener("input", previewCommand); $("runForm").onsubmit = startRun;
$("refreshScripts").onclick = async () => {try {await loadCatalog(); await loadScripts(); toast("脚本与环境已刷新");} catch (error) {toast(error.message);}};
$("stopRun").onclick = async () => {try {await api("/api/run/stop", {}); toast("已请求停止");} catch (error) {toast(error.message);}};
$("copyCommand").onclick = async () => {try {await navigator.clipboard.writeText($("command").textContent); toast("命令已复制");} catch (error) {toast("复制失败，可直接选中命令复制");}};
for (const [id, target] of [["openLog", "log"], ["openRun", "run"]]) $(id).onclick = async () => {try {await api("/api/open", {target});} catch (error) {toast(error.message);}};
(async () => {try {await api("/api/health"); $("health").textContent = "本机服务已连接"; await loadCatalog(); await loadScripts();} catch (error) {$("health").textContent = "服务不可用"; toast(error.message);}})();
