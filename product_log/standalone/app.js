'use strict';

const $ = id => document.getElementById(id);
const ROW_HEIGHT = 34;
const PAGE_SIZE = 200;
const TYPE_NAMES = {0: 'bios', 1: 'IMP', 2: 'IMU', 3: 'HSM'};
const state = {
  files: [], inputKind: 'archive', worker: null, workerURL: '', requestId: 0,
  pending: new Map(), busy: false, ready: false, run: 0, manifest: null,
  kind: 'firmware', groups: [], modules: [], moduleDirty: false, moduleSaving: false,
  rows: new Map(), pages: new Set(), total: 0, generation: 0, navigation: 0,
  timer: null, baseQuery: {}, contextMode: false, contextOffset: 0,
};

function error(id, value) {$(id).textContent = value ? String(value.message || value) : '';}
function status(value, text) {$('taskStatus').textContent = text; $('taskStatus').className = `badge ${value}`;}
function bytesText(value) {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(2)} GiB`;
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${bytes} B`;
}
function cancelError() {const value = new Error('任务已停止。'); value.name = 'AbortError'; return value;}
function disposeWorker(reason = cancelError()) {
  state.worker?.terminate(); state.worker = null;
  if (state.workerURL) URL.revokeObjectURL(state.workerURL);
  state.workerURL = '';
  for (const pending of state.pending.values()) pending.reject(reason);
  state.pending.clear();
}
function createWorker() {
  if (state.worker) return;
  const embedded = $('offline-worker');
  if (!embedded) throw new Error('此文件尚未打包。请运行 tools/build_product_log_page.py 后打开生成的 HTML 文件。');
  if (!window.Worker) throw new Error('当前浏览器不支持 Web Worker，请使用现代 Edge、Chrome 或 Firefox。');
  const binary = atob(embedded.textContent.trim());
  const source = new TextDecoder().decode(Uint8Array.from(binary, char => char.charCodeAt(0)));
  state.workerURL = URL.createObjectURL(new Blob([source], {type: 'text/javascript'}));
  state.worker = new Worker(state.workerURL);
  state.worker.onmessage = event => {
    const message = event.data || {};
    if (message.progress !== undefined) {if (state.busy) progressEvent(message.progress); return;}
    const pending = state.pending.get(message.id);
    if (!pending) return;
    state.pending.delete(message.id);
    if (message.error) pending.reject(new Error(typeof message.error === 'string' ? message.error : message.error.message || JSON.stringify(message.error)));
    else pending.resolve(message.result);
  };
  state.worker.onerror = event => {
    event.preventDefault();
    const reason = new Error(event.message || '浏览器解析线程运行失败。');
    disposeWorker(reason);
    if (state.busy) return; // The pending start call displays its failure.
    state.ready = false; state.manifest = null;
    status('failed', '解析线程已停止'); error('taskError', reason);
    clearModules(); resetReader(); setBusy(false);
  };
}
function rpc(action, payload = {}, transfer = []) {
  createWorker();
  const id = ++state.requestId;
  return new Promise((resolve, reject) => {
    state.pending.set(id, {resolve, reject});
    try {state.worker.postMessage({id, action, payload}, transfer);}
    catch (exception) {state.pending.delete(id); reject(exception);}
  });
}
function progressEvent(value) {
  const event = typeof value === 'string' ? {message: value} : value || {};
  const stage = event.stage || event.phase || '';
  const names = {reading: '读取输入', preparing: '准备输入', extracting: '解压文件', collecting: '收集日志', parsing: '解析日志', indexing: '整理索引', ready: '解析完成'};
  $('taskMessage').textContent = event.message || names[stage] || '正在处理…';
  const stages = ['reading', 'extracting', 'collecting', 'parsing', 'ready'];
  const aliases = {preparing: 'reading', indexing: 'parsing'};
  const stageIndex = stages.indexOf(aliases[stage] || stage);
  if (stageIndex >= 0) $('stageTrack').querySelectorAll('span').forEach((span, index) => span.classList.toggle('active', index <= stageIndex));
  const detail = Object.entries(event).filter(([key]) => !['type', 'message', 'stage', 'phase'].includes(key)).map(([key, item]) => `${key}=${typeof item === 'object' ? JSON.stringify(item) : item}`).join(' · ');
  $('progressLog').textContent += `${event.message || names[stage] || stage}${detail ? ' · ' + detail : ''}\n`;
  if ($('progressLog').textContent.length > 40000) $('progressLog').textContent = $('progressLog').textContent.slice(-40000);
}
function updateManifest(manifest) {
  const counts = manifest?.counts || {};
  for (const [id, field] of [['fileCount', 'files'], ['userCount', 'user_records'], ['kernelCount', 'kernel_records'], ['firmwareCount', 'firmware_records']]) $(id).textContent = counts[field] == null ? '—' : Number(counts[field]).toLocaleString();
  $('inputRoot').textContent = manifest?.input_root || (state.files.length ? state.inputKind === 'archive' ? state.files[0].name : `${state.files.length} 个已选择文件` : '—');
  const targets = manifest?.target_paths || manifest?.targets;
  $('workingPath').textContent = targets ? Object.entries(targets).map(([key, path]) => `${key === 'user' || key === 'diag' ? 'diag' : key === 'kernel' || key === 'var' ? 'var' : key}: ${path || '未找到'}`).join('\n') : '—';
  const details = [];
  if (manifest?.duration_seconds != null) details.push(`解析用时 ${Number(manifest.duration_seconds).toFixed(2)} 秒`);
  if (manifest?.archives) details.push(`已展开 ${Array.isArray(manifest.archives) ? manifest.archives.length : manifest.archives} 个压缩包`);
  if (counts.expanded_bytes != null) details.push(`解压内容 ${bytesText(counts.expanded_bytes)}`);
  if (counts.diagnostics != null || counts.diagnostic_records != null) details.push(`诊断 / 告警 ${counts.diagnostics ?? counts.diagnostic_records} 条`);
  $('elapsed').textContent = details.join(' · ');
}
function setBusy(busy) {
  state.busy = busy;
  for (const control of $('importForm').querySelectorAll('input,select,textarea,button')) control.disabled = busy;
  $('stopTask').disabled = !busy;
  $('clearData').disabled = !state.ready && !busy;
  moduleControls(); applyViewControls();
}
function selectedFiles(files, kind) {
  if (state.busy) return;
  state.files = Array.from(files); state.inputKind = kind;
  $('selectedInput').textContent = kind === 'archive' ? state.files[0]?.name || '未选择文件' : `${state.files.length} 个文件 · 保留相对目录层级`;
  error('taskError', '');
}
$('pickArchive').addEventListener('click', event => {event.stopPropagation(); $('archiveInput').click();});
$('pickDirectory').addEventListener('click', event => {event.stopPropagation(); $('directoryInput').click();});
$('archiveInput').addEventListener('change', event => selectedFiles(event.target.files, 'archive'));
$('directoryInput').addEventListener('change', event => selectedFiles(event.target.files, 'directory'));
$('dropzone').addEventListener('click', event => {if (!state.busy && !event.target.closest('button')) $('archiveInput').click();});
$('dropzone').addEventListener('keydown', event => {if (!state.busy && event.target === $('dropzone') && ['Enter', ' '].includes(event.key)) {event.preventDefault(); $('archiveInput').click();}});
for (const name of ['dragenter', 'dragover']) $('dropzone').addEventListener(name, event => {event.preventDefault(); if (!state.busy) $('dropzone').classList.add('drag');});
for (const name of ['dragleave', 'drop']) $('dropzone').addEventListener(name, event => {event.preventDefault(); $('dropzone').classList.remove('drag');});
$('dropzone').addEventListener('drop', event => {
  if (state.busy) return;
  if (event.dataTransfer.files.length !== 1) {error('taskError', '请拖入一个压缩包；读取目录请使用“选择文件夹”。'); return;}
  selectedFiles(event.dataTransfer.files, 'archive');
});
$('importForm').addEventListener('submit', async event => {
  event.preventDefault(); error('taskError', '');
  if (state.busy) return;
  if (!state.files.length) {error('taskError', '请选择一个压缩包或文件夹。'); return;}
  let renameMap;
  try {
    renameMap = $('renameMap').value.trim() ? JSON.parse($('renameMap').value) : {};
    if (!renameMap || typeof renameMap !== 'object' || Array.isArray(renameMap) || Object.values(renameMap).some(value => typeof value !== 'string')) throw new Error();
  } catch {error('taskError', '重命名映射需要有效的 JSON 对象，文件名与新名称都填写字符串。'); return;}
  const rules = {collection_mode: 'product', diag_directory_pattern: $('directoryPattern').value.trim() || 'diag', var_directory_pattern: $('varDirectoryPattern').value.trim() || 'var', file_patterns: $('filePatterns').value, archive_patterns: $('archivePatterns').value, copy_patterns: $('copyPatterns').value, rename_map: renameMap, recursive: $('recursive').checked};
  const run = ++state.run;
  disposeWorker(); state.ready = false; state.manifest = null; state.navigation++;
  state.groups = []; $('groupSelect').replaceChildren(new Option('全部分块', '')); fieldOptions();
  clearModules(); resetReader(); updateManifest(null); setBusy(true);
  $('progressLog').textContent = ''; $('stageTrack').querySelectorAll('span').forEach(span => span.classList.remove('active'));
  status('working', '正在读取');
  try {
    const files = [];
    for (const file of state.files) {
      const path = state.inputKind === 'directory' ? file.webkitRelativePath || file.name : file.name;
      progressEvent({stage: 'reading', message: `读取 ${path}`, bytes: file.size});
      const bytes = await file.arrayBuffer();
      if (run !== state.run) return;
      files.push({path, bytes});
    }
    status('working', '正在解析');
    const manifest = await rpc('start', {files, rules, upload_kind: state.inputKind}, files.map(file => file.bytes));
    if (run !== state.run) return;
    state.ready = true; state.manifest = manifest;
    updateManifest(manifest); progressEvent({stage: 'ready', message: '解析完成。原始文件未修改，解析结果保存在此页内存中。'});
    await loadModules(); await loadGroups(); await query();
    if (run === state.run) status('ready', '解析完成');
  } catch (exception) {
    if (run !== state.run || exception.name === 'AbortError') return;
    state.ready = false; disposeWorker(); clearModules(); resetReader();
    status('failed', '解析失败'); error('taskError', exception);
    $('taskMessage').textContent = '解析未完成。检查目录规则与压缩包格式后可重新选择输入。';
  } finally {if (run === state.run) setBusy(false);}
});
function clearCurrent(cancelled = false) {
  ++state.run; ++state.navigation; disposeWorker(); clearTimeout(state.timer);
  state.ready = false; state.manifest = null; state.groups = [];
  $('groupSelect').replaceChildren(new Option('全部分块', '')); fieldOptions();
  clearModules(); resetReader(); updateManifest(null); setBusy(false);
  $('sourceDialog').close(); $('sourceContent').textContent = ''; $('sourceLocation').textContent = '';
  $('stageTrack').querySelectorAll('span').forEach(span => span.classList.remove('active'));
  error('taskError', ''); error('browserError', '');
  status(cancelled ? 'cancelled' : '', cancelled ? '已停止' : '数据已清除');
  $('taskMessage').textContent = cancelled ? '解析线程已停止，处理中的数据已释放；可重新解析。' : '当前解析数据已释放，原始文件未修改。';
  $('progressLog').textContent += cancelled ? '用户停止任务。\n' : '当前内存数据已清除。\n';
}
$('stopTask').addEventListener('click', () => clearCurrent(true));
$('clearData').addEventListener('click', () => clearCurrent(false));

function moduleControls() {
  const unavailable = !state.ready || state.busy || state.moduleSaving;
  for (const id of ['addModule', 'importModules', 'downloadModules']) $(id).disabled = unavailable;
  $('saveModules').disabled = unavailable || !state.moduleDirty;
  for (const control of $('moduleTableBody').querySelectorAll('input,button')) control.disabled = unavailable;
}
function updateModuleSelect() {
  const previous = $('moduleSelect').value;
  $('moduleSelect').replaceChildren(new Option('全部模块', ''));
  state.modules.forEach(row => $('moduleSelect').append(new Option(row.module + (!row.user_exists ? ' · 用户态不存在' : ''), row.module)));
  if ([...$('moduleSelect').options].some(option => option.value === previous)) $('moduleSelect').value = previous;
}
function emptyModuleTable(message) {
  const row = document.createElement('tr'), cell = document.createElement('td');
  cell.colSpan = 6; cell.className = 'hint'; cell.textContent = message; row.append(cell); $('moduleTableBody').replaceChildren(row);
}
function clearModules() {
  state.modules = []; state.moduleDirty = false; state.moduleSaving = false;
  emptyModuleTable('请先解析日志。'); $('moduleMessage').textContent = ''; error('moduleError', '');
  updateModuleSelect(); moduleControls();
}
function moduleChanged() {
  state.moduleDirty = true; $('moduleMessage').textContent = '有未应用的修改。点击“保存并应用”更新匹配数量和筛选。'; moduleControls();
}
function reindexModuleLabels() {
  [...$('moduleTableBody').querySelectorAll('tr')].forEach((row, index) => {
    for (const input of row.querySelectorAll('input')) input.setAttribute('aria-label', `第 ${index + 1} 行${{module: '模块', user_module_name: '用户态 ModuleName', kernel_keyword: '内核态关键词'}[input.dataset.field]}`);
    row.querySelector('button')?.setAttribute('aria-label', `删除第 ${index + 1} 行模块`);
  });
}
function moduleRow(value) {
  const row = document.createElement('tr');
  for (const field of ['module', 'user_module_name', 'kernel_keyword']) {
    const cell = document.createElement('td'), input = document.createElement('input');
    input.type = 'text'; input.value = value[field] || ''; input.dataset.field = field; input.addEventListener('input', moduleChanged);
    cell.append(input); row.append(cell);
  }
  const user = document.createElement('td'); user.className = 'module-count'; user.textContent = value.user_exists === undefined ? '待应用' : value.user_exists ? `${Number(value.user_count || 0).toLocaleString()} 条` : '不存在'; row.append(user);
  const kernel = document.createElement('td'); kernel.className = 'module-count'; kernel.textContent = value.kernel_configured === undefined ? '待应用' : value.kernel_configured ? `${Number(value.kernel_count || 0).toLocaleString()} 条` : '未配置'; row.append(kernel);
  const action = document.createElement('td'), remove = document.createElement('button');
  remove.type = 'button'; remove.className = 'secondary module-remove'; remove.textContent = '删除';
  remove.addEventListener('click', () => {row.remove(); reindexModuleLabels(); moduleChanged();});
  action.append(remove); row.append(action); return row;
}
function readModuleRows() {
  return [...$('moduleTableBody').querySelectorAll('tr')].filter(row => row.querySelector('input')).map(row => Object.fromEntries([...row.querySelectorAll('input')].map(input => [input.dataset.field, input.value.trim()])));
}
function renderModules(rows) {
  state.modules = rows; state.moduleDirty = false;
  if (rows.length) $('moduleTableBody').replaceChildren(...rows.map(moduleRow));
  else emptyModuleTable('暂无对应关系，可点击“添加模块”。');
  reindexModuleLabels(); updateModuleSelect(); moduleControls();
}
async function loadModules() {
  const run = state.run;
  const result = await rpc('mappings');
  if (run !== state.run || !state.ready) return;
  renderModules(result.modules || []);
  $('moduleMessage').textContent = '对应表仅保留在当前页面；下载 JSON 可下次复用。同一内核行匹配多个模块时，全部模块视图只显示一次。';
}
$('addModule').addEventListener('click', () => {
  if (!state.ready || state.moduleSaving) return;
  if (!$('moduleTableBody').querySelector('input')) $('moduleTableBody').replaceChildren();
  const row = moduleRow({}); $('moduleTableBody').append(row); reindexModuleLabels(); moduleChanged(); row.querySelector('input').focus();
});
async function saveModules(modules = readModuleRows()) {
  if (!state.ready || state.moduleSaving) return;
  const run = state.run;
  state.moduleSaving = true; moduleControls(); error('moduleError', '');
  try {
    const result = await rpc('mappings', {modules});
    if (run !== state.run || !state.ready) return;
    renderModules(result.modules || []); $('moduleMessage').textContent = '对应表已应用，筛选已更新，无需重新解压。下载 JSON 后可在下次复用。';
    await loadGroups(); await query();
  } catch (exception) {if (run === state.run && exception.name !== 'AbortError') error('moduleError', exception);}
  finally {if (run === state.run) {state.moduleSaving = false; moduleControls();}}
}
$('saveModules').addEventListener('click', () => saveModules());
function download(text, filename, type = 'text/plain;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const link = document.createElement('a'); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('downloadModules').addEventListener('click', () => {
  if (!state.ready) return;
  download(JSON.stringify(readModuleRows(), null, 2) + '\n', 'product-log-modules.json', 'application/json;charset=utf-8');
  $('moduleMessage').textContent = '已下载当前表格，包括尚未应用的修改。';
});
$('importModules').addEventListener('click', () => {if (state.ready) $('mappingInput').click();});
$('mappingInput').addEventListener('change', async event => {
  const file = event.target.files[0], run = state.run;
  event.target.value = '';
  if (!file || !state.ready) return;
  error('moduleError', '');
  try {
    const parsed = JSON.parse(await file.text());
    if (run !== state.run || !state.ready) return;
    const modules = Array.isArray(parsed) ? parsed : parsed.modules;
    if (!Array.isArray(modules)) throw new Error('JSON 需要为模块行数组，或包含 modules 数组的对象。');
    await saveModules(modules);
  } catch (exception) {if (run === state.run) error('moduleError', exception);}
});

function groupLabel(group) {
  const kernel = group.driver_domain === 'kernel', firmware = group.kind === 'firmware' || state.kind === 'firmware';
  const known = value => value === null || value === undefined || value === '' ? '未知' : value;
  const parts = [firmware ? '固件' : kernel ? '内核态' : '用户态', `Slot ${known(group.slot_id)}`, `CPU ${known(group.cpu_id)}`];
  if (firmware) parts.push(`Dev ${group.device_id ?? '?'}`, `${group.log_type_name || TYPE_NAMES[group.log_type] || group.log_type || '?'} (Type ${group.log_type ?? '?'})`);
  else if (group.module_name) parts.push(group.module_name);
  parts.push(`${Number(group.count || 0).toLocaleString()} 条`);
  if (group.dump_count) parts.push(`${group.dump_count} 次读取`);
  if (group.source_count > 1) parts.push(`${group.source_count} 来源`);
  return parts.join(' · ');
}
function fillOptions(id, values, labelFor = value => value) {
  const previous = $(id).value; $(id).replaceChildren(new Option('全部', ''));
  [...new Set(values.filter(value => value !== null && value !== undefined && value !== '').map(String))].sort().forEach(value => $(id).append(new Option(labelFor(value), value)));
  if ([...$(id).options].some(option => option.value === previous)) $(id).value = previous;
}
function fieldOptions() {
  const pairs = [['slotFilter', 'slot_id'], ['cpuFilter', 'cpu_id'], ['deviceFilter', 'device_id'], ['logTypeFilter', 'log_type'], ['moduleFilter', 'module_name']];
  const selected = $('groupSelect').value;
  const groups = selected && state.kind !== 'all' ? state.groups.filter(group => String(group.group_id) === selected) : state.groups;
  for (const [id, field] of pairs) {
    const eligible = groups.filter(group => pairs.every(([otherId, otherField]) => {
      if (otherId === id || !$(otherId).value || group.driver_domain === 'kernel') return true;
      if (['device_id', 'log_type'].includes(otherField) && group.kind !== 'firmware' && state.kind !== 'firmware') return true;
      return otherField === 'module_name' && group.module_names ? group.module_names.map(String).includes($(otherId).value) : String(group[otherField]) === $(otherId).value;
    }));
    const values = eligible.flatMap(group => group.driver_domain === 'kernel' ? [] : field === 'module_name' && group.module_names ? group.module_names : [group[field]]);
    fillOptions(id, values, field === 'log_type' ? value => `${TYPE_NAMES[value] || value} (Type ${value})` : undefined);
  }
}
function applyViewControls() {
  const drivers = ['driver', 'all'].includes(state.kind), firmware = ['firmware', 'all'].includes(state.kind), diagnostic = state.kind === 'diagnostic';
  const unavailable = !state.ready || state.busy;
  document.querySelectorAll('.firmware-field').forEach(field => {field.hidden = !firmware;});
  $('viewField').hidden = state.kind !== 'firmware'; $('modeField').hidden = !drivers;
  $('driverDomainField').hidden = !drivers; $('moduleSelectField').hidden = !drivers; $('domainHint').hidden = !drivers;
  $('moduleFilterLabel').textContent = state.kind === 'driver' ? '用户态 ModuleName' : 'ModuleName';
  for (const control of document.querySelectorAll('.filter-fields select,.search-fields input,.search-fields select,#rawSearch,#caseSensitive,#contextLines,#driverDomain,#moduleSelect')) control.disabled = unavailable || diagnostic;
  $('groupSelect').disabled = unavailable || diagnostic || state.kind === 'all';
  const kernelOnly = state.kind === 'driver' && $('driverDomain').value === 'kernel';
  for (const id of ['slotFilter', 'cpuFilter', 'moduleFilter']) {$(id).disabled = unavailable || diagnostic || kernelOnly; if (kernelOnly) $(id).value = '';}
  if (state.kind === 'all') $('groupSelect').value = '';
  $('exportButton').disabled = unavailable || diagnostic;
  $('resetFilters').disabled = unavailable;
  $('domainHint').textContent = kernelOnly ? '内核态只按模块筛选；SlotId / CpuId 未知，点击日志行可查看相对来源路径。' : '对应表模块筛选两态驱动；SlotId / CpuId 仅筛用户态和固件，DevId / 类型仅筛固件。';
}
async function loadGroups() {
  const run = state.run, kind = state.kind, domain = $('driverDomain').value;
  const previous = $('groupSelect').value;
  $('groupSelect').replaceChildren(new Option('全部分块', '')); state.groups = [];
  if (!state.ready) {fieldOptions(); applyViewControls(); return;}
  if (['driver', 'firmware', 'all'].includes(kind)) {
    const result = await rpc('groups', {kind, driver_domain: ['driver', 'all'].includes(kind) ? domain : ''});
    if (run !== state.run || kind !== state.kind || domain !== $('driverDomain').value) return;
    state.groups = result.groups || [];
    if (kind !== 'all') state.groups.forEach(group => $('groupSelect').append(new Option(groupLabel(group), String(group.group_id))));
    if ([...$('groupSelect').options].some(option => option.value === previous)) $('groupSelect').value = previous;
  }
  fieldOptions(); applyViewControls();
}
for (const button of document.querySelectorAll('[data-kind]')) button.addEventListener('click', async () => {
  const navigation = ++state.navigation; resetReader(); state.kind = button.dataset.kind;
  document.querySelectorAll('[data-kind]').forEach(tab => {const active = tab === button; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active));});
  for (const id of ['groupSelect', 'slotFilter', 'cpuFilter', 'deviceFilter', 'logTypeFilter', 'moduleFilter']) $(id).value = '';
  applyViewControls();
  try {await loadGroups(); if (navigation === state.navigation) await query();} catch (exception) {if (exception.name !== 'AbortError') error('browserError', exception);}
});
function filters() {
  const drivers = ['driver', 'all'].includes(state.kind), firmware = ['firmware', 'all'].includes(state.kind);
  return {kind: state.kind, group_id: state.kind === 'all' ? '' : $('groupSelect').value, driver_domain: drivers ? $('driverDomain').value : '', module: drivers ? $('moduleSelect').value : '', slot_id: $('slotFilter').value, cpu_id: $('cpuFilter').value, module_name: $('moduleFilter').value, device_id: firmware ? $('deviceFilter').value : '', log_type: firmware ? $('logTypeFilter').value : '', keyword: $('keyword').value, raw_search: $('rawSearch').checked, case_sensitive: $('caseSensitive').checked, start_time: $('startTime').value.trim(), end_time: $('endTime').value.trim(), view: state.kind === 'firmware' ? $('view').value : 'time', mode: drivers ? $('mode').value : 'concise', context_lines: Number($('contextLines').value)};
}
function resetReader() {
  state.generation++; state.rows.clear(); state.pages.clear(); state.total = 0;
  $('logRows').replaceChildren(); $('logSpacer').style.height = '0px'; $('emptyState').hidden = false;
  $('contextNavigation').hidden = true; $('exportButton').disabled = !state.ready || state.busy || state.kind === 'diagnostic';
  $('resultCount').textContent = state.ready ? '查询中…' : '请先解析日志';
  $('emptyState').textContent = state.ready ? '正在查询…' : '解析完成后，这里会显示日志。';
}
async function query() {
  resetReader(); $('logViewport').scrollTop = 0; error('browserError', '');
  if (!state.ready) return;
  state.baseQuery = filters(); state.contextMode = state.kind !== 'diagnostic' && Number(state.baseQuery.context_lines) > 0 && Boolean(state.baseQuery.keyword);
  state.contextOffset = 0; await loadPage(0, state.generation);
}
async function loadPage(offset, generation) {
  if (!state.ready || state.pages.has(offset)) return;
  state.pages.add(offset);
  try {
    const value = await rpc('query', {...state.baseQuery, offset, limit: PAGE_SIZE});
    if (generation !== state.generation || !state.ready) return;
    const records = value.records || [];
    if (state.contextMode) {
      state.rows.clear(); state.total = records.length; state.contextOffset = offset;
      records.forEach((record, index) => state.rows.set(index, record));
      $('contextNavigation').hidden = false; $('contextPrevious').disabled = offset === 0; $('contextNext').disabled = !value.has_more;
    } else {
      state.total = value.total || 0; records.forEach((record, index) => state.rows.set(offset + index, record));
    }
    const center = Math.floor($('logViewport').scrollTop / ROW_HEIGHT / PAGE_SIZE) * PAGE_SIZE;
    if (!state.contextMode) for (const page of [...state.pages]) if (Math.abs(page - center) > PAGE_SIZE * 4) {state.pages.delete(page); for (let row = page; row < page + PAGE_SIZE; row++) state.rows.delete(row);}
    $('logSpacer').style.height = `${state.total * ROW_HEIGHT}px`; $('emptyState').hidden = state.total > 0;
    $('emptyState').textContent = '没有符合当前条件的日志。';
    $('resultCount').textContent = state.contextMode ? `${Number(value.total || 0).toLocaleString()} 条命中 · 当前页 ${records.length} 条（含上下文）` : `${state.total.toLocaleString()} 条记录`;
    renderRows();
  } catch (exception) {
    if (exception.name !== 'AbortError' && generation === state.generation) {state.pages.delete(offset); error('browserError', exception); $('resultCount').textContent = '查询失败'; $('emptyState').textContent = '查询失败，请检查筛选条件。';}
  }
}
// Keep this Unicode 14 full-fold table equal to parser.js. Offset projection
// preserves original characters when folding expands them (e.g. ß -> ss).
const HIGHLIGHT_FOLD = {"µ":"μ","ß":"ss","ŉ":"ʼn","ſ":"s","ǰ":"ǰ","ͅ":"ι","ΐ":"ΐ","ΰ":"ΰ","ς":"σ","ϐ":"β","ϑ":"θ","ϕ":"φ","ϖ":"π","ϰ":"κ","ϱ":"ρ","ϵ":"ε","և":"եւ","ꭰ":"Ꭰ","ꭱ":"Ꭱ","ꭲ":"Ꭲ","ꭳ":"Ꭳ","ꭴ":"Ꭴ","ꭵ":"Ꭵ","ꭶ":"Ꭶ","ꭷ":"Ꭷ","ꭸ":"Ꭸ","ꭹ":"Ꭹ","ꭺ":"Ꭺ","ꭻ":"Ꭻ","ꭼ":"Ꭼ","ꭽ":"Ꭽ","ꭾ":"Ꭾ","ꭿ":"Ꭿ","ꮀ":"Ꮀ","ꮁ":"Ꮁ","ꮂ":"Ꮂ","ꮃ":"Ꮃ","ꮄ":"Ꮄ","ꮅ":"Ꮅ","ꮆ":"Ꮆ","ꮇ":"Ꮇ","ꮈ":"Ꮈ","ꮉ":"Ꮉ","ꮊ":"Ꮊ","ꮋ":"Ꮋ","ꮌ":"Ꮌ","ꮍ":"Ꮍ","ꮎ":"Ꮎ","ꮏ":"Ꮏ","ꮐ":"Ꮐ","ꮑ":"Ꮑ","ꮒ":"Ꮒ","ꮓ":"Ꮓ","ꮔ":"Ꮔ","ꮕ":"Ꮕ","ꮖ":"Ꮖ","ꮗ":"Ꮗ","ꮘ":"Ꮘ","ꮙ":"Ꮙ","ꮚ":"Ꮚ","ꮛ":"Ꮛ","ꮜ":"Ꮜ","ꮝ":"Ꮝ","ꮞ":"Ꮞ","ꮟ":"Ꮟ","ꮠ":"Ꮠ","ꮡ":"Ꮡ","ꮢ":"Ꮢ","ꮣ":"Ꮣ","ꮤ":"Ꮤ","ꮥ":"Ꮥ","ꮦ":"Ꮦ","ꮧ":"Ꮧ","ꮨ":"Ꮨ","ꮩ":"Ꮩ","ꮪ":"Ꮪ","ꮫ":"Ꮫ","ꮬ":"Ꮬ","ꮭ":"Ꮭ","ꮮ":"Ꮮ","ꮯ":"Ꮯ","ꮰ":"Ꮰ","ꮱ":"Ꮱ","ꮲ":"Ꮲ","ꮳ":"Ꮳ","ꮴ":"Ꮴ","ꮵ":"Ꮵ","ꮶ":"Ꮶ","ꮷ":"Ꮷ","ꮸ":"Ꮸ","ꮹ":"Ꮹ","ꮺ":"Ꮺ","ꮻ":"Ꮻ","ꮼ":"Ꮼ","ꮽ":"Ꮽ","ꮾ":"Ꮾ","ꮿ":"Ꮿ","ᏸ":"Ᏸ","ᏹ":"Ᏹ","ᏺ":"Ᏺ","ᏻ":"Ᏻ","ᏼ":"Ᏼ","ᏽ":"Ᏽ","ᲀ":"в","ᲁ":"д","ᲂ":"о","ᲃ":"с","ᲄ":"т","ᲅ":"т","ᲆ":"ъ","ᲇ":"ѣ","ᲈ":"ꙋ","ẖ":"ẖ","ẗ":"ẗ","ẘ":"ẘ","ẙ":"ẙ","ẚ":"aʾ","ẛ":"ṡ","ὐ":"ὐ","ὒ":"ὒ","ὔ":"ὔ","ὖ":"ὖ","ᾀ":"ἀι","ᾁ":"ἁι","ᾂ":"ἂι","ᾃ":"ἃι","ᾄ":"ἄι","ᾅ":"ἅι","ᾆ":"ἆι","ᾇ":"ἇι","ᾐ":"ἠι","ᾑ":"ἡι","ᾒ":"ἢι","ᾓ":"ἣι","ᾔ":"ἤι","ᾕ":"ἥι","ᾖ":"ἦι","ᾗ":"ἧι","ᾠ":"ὠι","ᾡ":"ὡι","ᾢ":"ὢι","ᾣ":"ὣι","ᾤ":"ὤι","ᾥ":"ὥι","ᾦ":"ὦι","ᾧ":"ὧι","ᾲ":"ὰι","ᾳ":"αι","ᾴ":"άι","ᾶ":"ᾶ","ᾷ":"ᾶι","ι":"ι","ῂ":"ὴι","ῃ":"ηι","ῄ":"ήι","ῆ":"ῆ","ῇ":"ῆι","ῒ":"ῒ","ΐ":"ΐ","ῖ":"ῖ","ῗ":"ῗ","ῢ":"ῢ","ΰ":"ΰ","ῤ":"ῤ","ῦ":"ῦ","ῧ":"ῧ","ῲ":"ὼι","ῳ":"ωι","ῴ":"ώι","ῶ":"ῶ","ῷ":"ῶι","ﬀ":"ff","ﬁ":"fi","ﬂ":"fl","ﬃ":"ffi","ﬄ":"ffl","ﬅ":"st","ﬆ":"st","ﬓ":"մն","ﬔ":"մե","ﬕ":"մի","ﬖ":"վն","ﬗ":"մխ"};
function foldForHighlight(text, offsets = false) {
  let value = '', offset = 0;
  const starts = [], ends = [];
  for (const character of text) {
    const folded = [...character.toLowerCase()].map(part => HIGHLIGHT_FOLD[part] || part).join('');
    value += folded;
    if (offsets) for (let index = 0; index < folded.length; index++) {starts.push(offset); ends.push(offset + character.length);}
    offset += character.length;
  }
  return {value, starts, ends};
}
function highlightRanges(text, keyword, sensitive) {
  if (!keyword) return [];
  const projection = sensitive ? null : foldForHighlight(text, true);
  const haystack = projection ? projection.value : text, needle = sensitive ? keyword : foldForHighlight(keyword).value;
  if (!needle) return [];
  const ranges = [];
  let cursor = 0, found;
  while ((found = haystack.indexOf(needle, cursor)) >= 0) {
    const start = projection ? projection.starts[found] : found;
    const end = projection ? projection.ends[found + needle.length - 1] : found + needle.length;
    const previous = ranges[ranges.length - 1];
    if (previous && start <= previous.end) previous.end = Math.max(previous.end, end);
    else ranges.push({start, end});
    cursor = found + needle.length;
  }
  return ranges;
}
function highlighted(text, keyword) {
  const fragment = document.createDocumentFragment();
  let start = 0;
  for (const range of highlightRanges(text, keyword, state.baseQuery.case_sensitive)) {
    fragment.append(document.createTextNode(text.slice(start, range.start)));
    const mark = document.createElement('mark'); mark.textContent = text.slice(range.start, range.end); fragment.append(mark); start = range.end;
  }
  fragment.append(document.createTextNode(text.slice(start))); return fragment;
}
function renderRows() {
  const viewport = $('logViewport'), first = Math.max(0, Math.floor(viewport.scrollTop / ROW_HEIGHT) - 8);
  const last = Math.min(state.total, first + Math.ceil(viewport.clientHeight / ROW_HEIGHT) + 20);
  const fragment = document.createDocumentFragment();
  for (let index = first; index < last; index++) {
    const record = state.rows.get(index), row = document.createElement('div'); row.className = `log-row${record?.is_context ? ' context' : ''}`;
    const line = document.createElement('span'); line.className = 'log-line'; line.textContent = record?.line_number ?? '…'; row.append(line);
    const domain = record?.kind === 'firmware' ? 'firmware' : record?.kind === 'driver' ? record.driver_domain || 'user' : state.kind === 'diagnostic' ? 'diagnostic' : 'unclassified';
    const tag = document.createElement('span'); tag.className = `log-domain ${domain}`; tag.textContent = record ? {firmware: '固件', user: '用户态', kernel: '内核态', diagnostic: '诊断', unclassified: '未分类'}[domain] || domain : '…'; row.append(tag);
    let shown = String(record?.text ?? record?.message ?? '正在读取…');
    if (record?.kind === 'driver' && record.time_value === null && !shown.includes('时间未知')) shown = `[时间未知] ${shown}`;
    const text = document.createElement('span'); text.className = 'log-text'; text.append(highlighted(shown, state.baseQuery.keyword || '')); row.append(text);
    if (record?.source_id != null) {
      row.title = `${record.source_path || ''}:${record.line_number || ''}`; row.tabIndex = 0; row.setAttribute('role', 'button');
      row.addEventListener('click', () => showSource(record));
      row.addEventListener('keydown', event => {if (['Enter', ' '].includes(event.key)) {event.preventDefault(); showSource(record);}});
    }
    fragment.append(row);
  }
  $('logRows').style.transform = `translateY(${first * ROW_HEIGHT}px)`; $('logRows').replaceChildren(fragment);
  if (state.total && !state.contextMode) for (const offset of new Set([Math.floor(first / PAGE_SIZE) * PAGE_SIZE, Math.floor(Math.max(first, last - 1) / PAGE_SIZE) * PAGE_SIZE])) loadPage(offset, state.generation);
}
async function contextPage(offset) {resetReader(); $('logViewport').scrollTop = 0; await loadPage(offset, state.generation);}
$('contextPrevious').addEventListener('click', () => contextPage(Math.max(0, state.contextOffset - PAGE_SIZE)));
$('contextNext').addEventListener('click', () => contextPage(state.contextOffset + PAGE_SIZE));
$('logViewport').addEventListener('scroll', () => requestAnimationFrame(renderRows), {passive: true});
window.addEventListener('resize', renderRows);
function scheduleQuery() {clearTimeout(state.timer); state.generation++; state.timer = setTimeout(() => query().catch(exception => {if (exception.name !== 'AbortError') error('browserError', exception);}), 240);}
for (const id of ['keyword', 'startTime', 'endTime']) $(id).addEventListener('input', scheduleQuery);
for (const id of ['groupSelect', 'slotFilter', 'cpuFilter', 'deviceFilter', 'logTypeFilter', 'moduleFilter']) $(id).addEventListener('change', () => {fieldOptions(); scheduleQuery();});
$('driverDomain').addEventListener('change', async () => {
  const navigation = ++state.navigation; resetReader(); $('groupSelect').value = ''; applyViewControls();
  try {await loadGroups(); if (navigation === state.navigation) await query();} catch (exception) {if (exception.name !== 'AbortError') error('browserError', exception);}
});
$('moduleSelect').addEventListener('change', () => {$('groupSelect').value = ''; fieldOptions(); scheduleQuery();});
for (const id of ['rawSearch', 'caseSensitive', 'view', 'mode', 'contextLines']) $(id).addEventListener('change', scheduleQuery);
$('resetFilters').addEventListener('click', async () => {
  for (const id of ['groupSelect', 'slotFilter', 'cpuFilter', 'deviceFilter', 'logTypeFilter', 'moduleFilter', 'moduleSelect', 'driverDomain', 'keyword', 'startTime', 'endTime']) $(id).value = '';
  $('rawSearch').checked = false; $('caseSensitive').checked = true; $('contextLines').value = '0'; applyViewControls();
  try {await loadGroups(); scheduleQuery();} catch (exception) {if (exception.name !== 'AbortError') error('browserError', exception);}
});
$('exportButton').addEventListener('click', async () => {
  if (!state.ready) return;
  const run = state.run; $('exportButton').disabled = true; error('browserError', '');
  try {
    const result = await rpc('export', filters());
    if (run === state.run && state.ready) download(result.text || '', result.filename || `product-log-${state.kind}.txt`);
  } catch (exception) {if (run === state.run && exception.name !== 'AbortError') error('browserError', exception);}
  finally {if (run === state.run) applyViewControls();}
});
async function showSource(record) {
  const run = state.run;
  try {
    const result = await rpc('source', {source_id: record.source_id, line_number: record.line_number, context_lines: 20});
    if (run !== state.run || !state.ready) return;
    $('sourceLocation').textContent = `${record.source_path || result.source_path || ''} · 行 ${record.line_number}`;
    $('sourceContent').textContent = (result.lines || []).map(line => `${String(line.line_number).padStart(6)}  ${line.text}`).join('\n');
    if (!$('sourceDialog').open) $('sourceDialog').showModal();
  } catch (exception) {if (run === state.run && exception.name !== 'AbortError') error('browserError', exception);}
}
$('closeSource').addEventListener('click', () => $('sourceDialog').close());
window.addEventListener('beforeunload', () => disposeWorker());
setBusy(false);
