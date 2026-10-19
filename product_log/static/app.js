'use strict';

const $ = id => document.getElementById(id);
const API = '/api/product-logs/';
const ROW_HEIGHT = 34;
const PAGE_SIZE = 200;
const TYPE_NAMES = {0: 'bios', 1: 'IMP', 2: 'IMU', 3: 'HSM'};
const state = {files: [], uploadKind: 'archive', active: null, batch: '', kind: 'firmware', groups: [], modules: [], moduleDirty: false, moduleSaving: false, moduleGeneration: 0, rows: new Map(), pages: new Set(), total: 0, generation: 0, queryController: null, uploadController: null, uploadId: null, cursor: 0, polling: false, timer: null, baseQuery: {}, contextMode: false, contextOffset: 0, matchTotal: 0, navigation: 0, storageGeneration: 0};

async function request(path, options = {}) {
  const response = await fetch(API + path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}
function post(path, value, signal) {return request(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value), signal});}
function error(id, value) {$(id).textContent = value ? String(value.message || value) : '';}
function status(value, text) { $('taskStatus').textContent = text || value; $('taskStatus').className = `badge ${value}`; }
function bytesText(value) {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(2)} GiB`;
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${bytes} B`;
}
function updateStorage(storage) {
  if (!storage) return;
  $('storageUsage').textContent = `已占用 ${bytesText(storage.usage_bytes)} · 自动清理阈值 ${bytesText(storage.limit_bytes)}`;
  const cleanup = storage.last_cleanup || storage;
  const removedBatches = cleanup.removed_batch_ids?.length || 0, removedUploads = cleanup.removed_upload_ids?.length || 0;
  $('storageNotice').textContent = (removedBatches || removedUploads ? `最近自动清理 ${removedBatches} 个批次、${removedUploads} 个上传，回收 ${bytesText(cleanup.reclaimed_bytes)}。` : '') + '达到阈值后按最旧顺序清理；当前任务、正在查看及最新结果保留。';
  const messages = [...(storage.errors || [])];
  if (storage.over_limit) messages.push('占用仍达到阈值：受保护的数据暂时保留，后续任务或检查占用时继续清理。');
  error('storageError', messages.join('；'));
}
async function refreshStorage(batchId = state.batch) {
  const generation = ++state.storageGeneration;
  const result = await request(`storage?${new URLSearchParams({batch_id: batchId})}`);
  if (generation === state.storageGeneration) updateStorage(result);
  return result;
}
$('refreshStorage').addEventListener('click', async () => {
  $('refreshStorage').disabled = true;
  try {await refreshStorage(); await refreshBatches();} catch (exception) {error('storageError', exception);} finally {$('refreshStorage').disabled = false;}
});
function selectedFiles(files, kind) {
  state.files = Array.from(files); state.uploadKind = kind;
  $('inputPath').value = '';
  $('selectedInput').textContent = kind === 'archive' ? (state.files[0]?.name || '未选择文件') : `${state.files.length} 个文件，保留相对目录层级`;
}
$('pickArchive').addEventListener('click', event => {event.stopPropagation(); $('archiveInput').click();});
$('pickDirectory').addEventListener('click', event => {event.stopPropagation(); $('directoryInput').click();});
$('archiveInput').addEventListener('change', event => selectedFiles(event.target.files, 'archive'));
$('directoryInput').addEventListener('change', event => selectedFiles(event.target.files, 'directory'));
$('inputPath').addEventListener('input', () => {if ($('inputPath').value.trim()) {state.files = []; $('selectedInput').textContent = '使用下方指定的本机路径';}});
$('dropzone').addEventListener('click', event => {if (!event.target.closest('button')) $('archiveInput').click();});
$('dropzone').addEventListener('keydown', event => {if (event.target === $('dropzone') && ['Enter', ' '].includes(event.key)) {event.preventDefault(); $('archiveInput').click();}});
for (const name of ['dragenter', 'dragover']) $('dropzone').addEventListener(name, event => {event.preventDefault(); $('dropzone').classList.add('drag');});
for (const name of ['dragleave', 'drop']) $('dropzone').addEventListener(name, event => {event.preventDefault(); $('dropzone').classList.remove('drag');});
$('dropzone').addEventListener('drop', event => {if (event.dataTransfer.files.length !== 1) {error('taskError', '请拖入一个压缩包；多个文件请使用“上传文件夹”。'); return;} selectedFiles(event.dataTransfer.files, 'archive');});

async function uploadFiles(signal) {
  const files = state.files.map(file => ({path: state.uploadKind === 'directory' ? (file.webkitRelativePath || file.name) : file.name, size: file.size, file}));
  const created = await post('uploads/create', {kind: state.uploadKind, files: files.map(({path, size}) => ({path, size}))}, signal);
  state.uploadId = created.upload_id;
  let received = 0;
  for (const entry of files) {
    for (let offset = 0; offset < entry.size; offset += created.chunk_bytes) {
      const chunk = entry.file.slice(offset, Math.min(offset + created.chunk_bytes, entry.size));
      const query = new URLSearchParams({upload_id: created.upload_id, path: entry.path, offset: String(offset)});
      await request(`uploads/chunk?${query}`, {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: chunk, signal});
      received += chunk.size;
      $('taskMessage').textContent = `上传 ${entry.path} · ${(received / 1048576).toFixed(1)} / ${(created.total_bytes / 1048576).toFixed(1)} MiB`;
    }
  }
  await post('uploads/finish', {upload_id: created.upload_id}, signal);
  return created.upload_id;
}

$('importForm').addEventListener('submit', async event => {
  event.preventDefault(); error('taskError', '');
  if (state.active || state.uploadController) return;
  let renames;
  try {renames = $('renameMap').value.trim() ? JSON.parse($('renameMap').value) : null;} catch {error('taskError', '重命名映射需要有效的 JSON 对象。'); return;}
  if (!state.files.length && !$('inputPath').value.trim()) {error('taskError', '请选择压缩包、上传文件夹，或输入本机绝对路径。'); return;}
  $('startTask').disabled = true; $('stopTask').disabled = false; $('progressLog').textContent = ''; state.cursor = 0;
  updateManifest({counts: {}, input_root: '正在准备输入…', product_log_path: '任务创建后显示'});
  state.uploadController = new AbortController();
  try {
    status('uploading', state.files.length ? '正在上传' : '正在准备');
    const uploadId = state.files.length ? await uploadFiles(state.uploadController.signal) : null;
    // Once submitted, the task must be cancelled by its server ID. Do not abort
    // the start response and lose the identity of a running server-side task.
    state.uploadController = null; $('stopTask').disabled = true;
    const result = await post('start', {input_path: $('inputPath').value.trim(), upload_id: uploadId, collection_mode: 'product', diag_directory_pattern: $('directoryPattern').value.trim() || 'diag', var_directory_pattern: $('varDirectoryPattern').value.trim() || 'varlog', archive_patterns: $('archivePatterns').value, file_patterns: $('filePatterns').value, copy_patterns: $('copyPatterns').value, rename_map: renames, recursive: $('recursive').checked, workers: Number($('workers').value)});
    state.active = result.batch_id; state.uploadId = null;
    $('workingPath').textContent = result.batch_dir + (result.batch_dir.includes('\\') ? '\\product_log' : '/product_log');
    $('stopTask').disabled = false; poll();
  } catch (exception) {
    status(exception.name === 'AbortError' ? 'cancelled' : 'failed', exception.name === 'AbortError' ? '上传已取消' : '准备失败');
    if (exception.name !== 'AbortError') error('taskError', exception);
    if (state.uploadId) {post('uploads/cancel', {upload_id: state.uploadId}).catch(() => {}); state.uploadId = null;}
    state.uploadController = null; $('startTask').disabled = false; $('stopTask').disabled = true;
  }
});
$('stopTask').addEventListener('click', async () => {
  if (state.uploadController) {state.uploadController.abort(); return;}
  if (!state.active) return;
  try {await post('stop', {batch_id: state.active}); status('cancelling', '正在停止'); $('stopTask').disabled = true;} catch (exception) {error('taskError', exception);}
});
function updateManifest(manifest) {
  if (!manifest) return;
  $('inputRoot').textContent = manifest.input_root || '—'; $('workingPath').textContent = manifest.product_log_path || manifest.batch_dir || '—';
  const counts = manifest.counts || {};
  for (const [id, key] of [['fileCount','files'],['driverCount','driver_records'],['firmwareCount','firmware_records'],['diagnosticCount','diagnostics']]) $(id).textContent = counts[key] ?? '—';
  $('elapsed').textContent = manifest.duration_seconds != null ? `耗时 ${Number(manifest.duration_seconds).toFixed(2)} 秒 · 固件读取批次 ${counts.dumps ?? 0} · 压缩包 ${manifest.archives?.length ?? 0}` : '';
  if (manifest.collection_mode === 'product') {
    const paths = manifest.target_paths || {};
    $('taskMessage').textContent = `用户态目录：${paths.user || '不存在'}；内核态目录：${paths.kernel || '不存在'}`;
  } else if (manifest.target_path) $('taskMessage').textContent = `唯一目标目录：${manifest.target_path}`;
  if (manifest.archives || manifest.sources) {
    const detail = [`目标目录：${manifest.target_path || manifest.input_root || ''}`, ...(manifest.archives || []).map(item => `解压：${item.path} → ${item.destination}`), ...(manifest.sources || []).slice(0, 100).map(item => `来源：${item.path || item.source_path || JSON.stringify(item)}`)].join('\n');
    $('progressLog').textContent = detail + (manifest.sources?.length > 100 ? '\n仅显示前 100 个来源；全部来源保存在任务 manifest.json。' : '');
  }
  if (manifest.error) error('taskError', manifest.error);
}
function progressEvent(event) {
  if (event.type === 'complete') return;
  const names = {preparing:'准备',extracting:'解压',collecting:'收集',parsing:'解析',indexing:'索引',cancelling:'正在停止'};
  status(event.stage || 'parsing', names[event.stage] || event.stage);
  $('taskMessage').textContent = event.message || names[event.stage] || '正在处理…';
  const stages = ['preparing','extracting','collecting','parsing','indexing'];
  $('stageTrack').querySelectorAll('span').forEach((span, index) => span.classList.toggle('active', index <= stages.indexOf(event.stage)));
  if (event.input_root || event.input_path) $('inputRoot').textContent = event.input_root || event.input_path;
  if (event.product_log_path) $('workingPath').textContent = event.product_log_path;
  const detail = Object.entries(event).filter(([key]) => !['type','message'].includes(key)).map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : value}`).join(' · ');
  $('progressLog').textContent += `${event.message || ''}${detail ? ' · ' + detail : ''}\n`;
  if ($('progressLog').textContent.length > 40000) $('progressLog').textContent = $('progressLog').textContent.slice(-40000);
}
async function poll() {
  if (!state.active || state.polling) return;
  state.polling = true;
  const active = state.active;
  try {
    const result = await request(`events?${new URLSearchParams({batch_id: active, cursor: String(state.cursor)})}`);
    if (state.active !== active) return;
    state.cursor = result.next; result.events.forEach(progressEvent);
    updateManifest(result.manifest);
    if (['ready','failed','cancelled'].includes(result.status)) {
      status(result.status, {ready:'解析完成',failed:'解析失败',cancelled:'已停止'}[result.status]);
      $('taskMessage').textContent = result.status === 'ready' ? '整理完成。选择分块或输入关键词查看日志。' : '任务已结束，已保留本次诊断信息。';
      const complete = result.events.find(event => event.type === 'complete'); if (complete?.error) error('taskError', complete.error);
      state.active = null; $('startTask').disabled = false; $('stopTask').disabled = true;
      await refreshBatches(); if (result.status === 'ready') await selectBatch(active);
    }
  } catch (exception) {error('taskError', exception);} finally {state.polling = false; if (state.active) setTimeout(poll, 600);}
}
async function refreshBatches() {
  const result = await request('batches');
  updateStorage(result.storage);
  const selected = state.batch;
  $('batchSelect').replaceChildren(new Option('选择已完成批次', ''));
  for (const batch of result.batches) {
    const counts = batch.manifest?.counts || {};
    const name = `${batch.batch_id.slice(0,8)} · ${batch.status} · ${counts.files ?? 0} 文件`;
    const option = new Option(name, batch.batch_id); option.disabled = batch.status !== 'ready'; $('batchSelect').append(option);
  }
  $('batchSelect').value = selected;
  if (result.active && !state.active) {state.active = result.active; $('startTask').disabled = true; $('stopTask').disabled = false; state.cursor = 0; setTimeout(poll, 0);}
  return result;
}
async function selectBatch(batchId) {
  const navigation = ++state.navigation; resetReader();
  state.batch = batchId; $('batchSelect').value = batchId; error('browserError', '');
  clearModules();
  await refreshStorage(batchId);
  if (navigation !== state.navigation) return;
  const result = await refreshBatches();
  if (navigation !== state.navigation) return;
  if (!batchId) {resetReader(); return;}
  const batch = result.batches.find(item => item.batch_id === batchId); updateManifest(batch?.manifest);
  if (batch && !state.active) status(batch.status, {ready:'解析完成',failed:'解析失败',cancelled:'已停止'}[batch.status] || batch.status);
  await loadModules(); if (navigation !== state.navigation) return;
  await loadGroups(); if (navigation === state.navigation) await query();
}
$('batchSelect').addEventListener('change', () => selectBatch($('batchSelect').value).catch(exception => error('browserError', exception)));
function moduleControls() {
  $('addModule').disabled = !state.batch || state.moduleSaving;
  $('saveModules').disabled = !state.batch || !state.moduleDirty || state.moduleSaving;
  for (const control of $('moduleTableBody').querySelectorAll('input,button')) control.disabled = state.moduleSaving;
}
function updateModuleSelect() {
  const previous = $('moduleSelect').value;
  $('moduleSelect').replaceChildren(new Option('全部模块', ''));
  state.modules.forEach(row => $('moduleSelect').append(new Option(row.module + (!row.user_exists ? ' · 用户态不存在' : ''), row.module)));
  if ([...$('moduleSelect').options].some(option => option.value === previous)) $('moduleSelect').value = previous;
}
function clearModules() {
  state.moduleGeneration++; state.modules = []; state.moduleDirty = false; state.moduleSaving = false;
  $('moduleTableBody').replaceChildren();
  const row = document.createElement('tr'), cell = document.createElement('td');
  cell.colSpan = 6; cell.className = 'hint'; cell.textContent = state.batch ? '正在读取模块对应表…' : '请选择已完成批次。'; row.append(cell); $('moduleTableBody').append(row);
  $('moduleMessage').textContent = ''; error('moduleError', ''); updateModuleSelect(); moduleControls();
}
function moduleChanged() {
  state.moduleDirty = true;
  $('moduleMessage').textContent = '有未保存的修改。保存并应用后，匹配数量与日志筛选一起更新。'; moduleControls();
}
function moduleRow(value, index) {
  const row = document.createElement('tr'); row.dataset.moduleIndex = String(index);
  for (const [field, label] of [['module','模块'],['user_module_name','用户态 ModuleName'],['kernel_keyword','内核态关键词']]) {
    const cell = document.createElement('td'), input = document.createElement('input');
    input.type = 'text'; input.value = value[field] || ''; input.dataset.field = field; input.setAttribute('aria-label', `第 ${index + 1} 行${label}`);
    input.addEventListener('input', moduleChanged); cell.append(input); row.append(cell);
  }
  const user = document.createElement('td'); user.className = 'module-count'; user.textContent = value.user_exists === undefined ? '待应用' : value.user_exists ? `${Number(value.user_count || 0).toLocaleString()} 条` : '不存在'; row.append(user);
  const kernel = document.createElement('td'); kernel.className = 'module-count'; kernel.textContent = value.kernel_configured === undefined ? '待应用' : value.kernel_configured ? `${Number(value.kernel_count || 0).toLocaleString()} 条` : '未配置'; row.append(kernel);
  const action = document.createElement('td'), remove = document.createElement('button');
  remove.type = 'button'; remove.className = 'secondary module-remove'; remove.textContent = '删除'; remove.setAttribute('aria-label', `删除第 ${index + 1} 行模块`);
  remove.addEventListener('click', () => {row.remove(); moduleChanged();}); action.append(remove); row.append(action); return row;
}
function renderModules(rows) {
  state.modules = rows; state.moduleDirty = false;
  $('moduleTableBody').replaceChildren(...rows.map(moduleRow));
  if (!rows.length) {const row = document.createElement('tr'), cell = document.createElement('td'); cell.colSpan = 6; cell.className = 'hint'; cell.textContent = '暂无模块，点击“添加模块”配置对应关系。'; row.append(cell); $('moduleTableBody').append(row);}
  updateModuleSelect(); moduleControls();
}
async function loadModules() {
  const batch = state.batch, generation = ++state.moduleGeneration;
  if (!batch) return;
  const result = await request(`modules?${new URLSearchParams({batch_id: batch})}`);
  if (batch !== state.batch || generation !== state.moduleGeneration) return;
  renderModules(result.modules || []);
  $('moduleMessage').textContent = '对应关系保存在当前批次。内核一行可匹配多个模块，全部模块视图中只显示一次。';
}
$('addModule').addEventListener('click', () => {
  if (!state.batch || state.moduleSaving) return;
  if (!$('moduleTableBody').querySelector('input')) $('moduleTableBody').replaceChildren();
  const index = $('moduleTableBody').querySelectorAll('tr').length;
  const row = moduleRow({module:'', user_module_name:'', kernel_keyword:''}, index); $('moduleTableBody').append(row); moduleChanged(); row.querySelector('input').focus();
});
$('saveModules').addEventListener('click', async () => {
  if (!state.batch || state.moduleSaving) return;
  const batch = state.batch, generation = ++state.moduleGeneration;
  const modules = [...$('moduleTableBody').querySelectorAll('tr')].filter(row => row.querySelector('input')).map(row => Object.fromEntries([...row.querySelectorAll('input')].map(input => [input.dataset.field, input.value.trim()])));
  state.moduleSaving = true; moduleControls(); error('moduleError', '');
  try {
    const result = await post('modules', {batch_id: batch, modules});
    if (batch !== state.batch || generation !== state.moduleGeneration) return;
    renderModules(result.modules || []); $('moduleMessage').textContent = '对应表已保存并应用，无需重新解压。';
    await loadGroups(); if (batch === state.batch) await query();
  } catch (exception) {if (batch === state.batch && generation === state.moduleGeneration) error('moduleError', exception);}
  finally {if (batch === state.batch && generation === state.moduleGeneration) {state.moduleSaving = false; moduleControls();}}
});
function groupLabel(group) {
  const kernel = group.driver_domain === 'kernel', firmware = group.kind === 'firmware' || state.kind === 'firmware';
  const parts = [firmware ? '固件' : kernel ? '内核态' : '用户态', `Slot ${group.slot_id || '未知'}`, `CPU ${group.cpu_id || '未知'}`];
  if (firmware) parts.push(`Dev ${group.device_id ?? '?'}`, `${group.log_type_name || TYPE_NAMES[group.log_type] || group.log_type || '?'} (Type ${group.log_type ?? '?'})`);
  else if (group.module_name) parts.push(group.module_name);
  parts.push(`${group.count} 条`); if (group.dump_count) parts.push(`${group.dump_count} 次读取`); if (group.source_count > 1) parts.push(`${group.source_count} 来源`);
  return parts.join(' · ');
}
function fillOptions(id, values, labelFor = value => value) {
  const previous = $(id).value; $(id).replaceChildren(new Option('全部', ''));
  [...new Set(values.filter(value => value !== null && value !== undefined && value !== '').map(String))].sort().forEach(value => $(id).append(new Option(labelFor(value), value)));
  if ([...$(id).options].some(option => option.value === previous)) $(id).value = previous;
}
function fieldOptions() {
  const fieldPairs = [['slotFilter','slot_id'],['cpuFilter','cpu_id'],['deviceFilter','device_id'],['logTypeFilter','log_type'],['moduleFilter','module_name']];
  const selected = $('groupSelect').value;
  const groups = selected && state.kind !== 'all' ? state.groups.filter(group => String(group.group_id) === selected) : state.groups;
  for (const [id, field] of fieldPairs) {
    const eligible = groups.filter(group => fieldPairs.every(([otherId, otherField]) => {
      if (otherId === id || !$(otherId).value) return true;
      if (group.driver_domain === 'kernel') return true;
      if (['device_id','log_type'].includes(otherField) && group.kind !== 'firmware' && state.kind !== 'firmware') return true;
      return otherField === 'module_name' && group.module_names ? group.module_names.map(String).includes($(otherId).value) : String(group[otherField]) === $(otherId).value;
    }));
    const values = eligible.flatMap(group => group.driver_domain === 'kernel' ? [] : field === 'module_name' && group.module_names ? group.module_names : [group[field]]);
    fillOptions(id, values, field === 'log_type' ? value => `${TYPE_NAMES[value] || value} (Type ${value})` : undefined);
  }
}
function applyViewControls() {
  const drivers = ['driver','all'].includes(state.kind), firmware = ['firmware','all'].includes(state.kind), diagnostic = state.kind === 'diagnostic';
  document.querySelectorAll('.firmware-field').forEach(field => {field.hidden = !firmware;});
  $('viewField').hidden = state.kind !== 'firmware'; $('modeField').hidden = !drivers;
  $('driverDomainField').hidden = !drivers; $('moduleSelectField').hidden = !drivers; $('domainHint').hidden = !drivers;
  $('moduleFilterLabel').textContent = state.kind === 'driver' ? '用户态 ModuleName' : 'ModuleName';
  document.querySelectorAll('.filter-fields select,.search-fields input,.search-fields select,#rawSearch,#contextLines').forEach(control => {control.disabled = diagnostic;});
  $('groupSelect').disabled = diagnostic || state.kind === 'all';
  const kernelOnly = state.kind === 'driver' && $('driverDomain').value === 'kernel';
  for (const id of ['slotFilter','cpuFilter','moduleFilter']) {$(id).disabled = diagnostic || kernelOnly; if (kernelOnly) $(id).value = '';}
  if (state.kind === 'all') $('groupSelect').value = '';
  $('domainHint').textContent = kernelOnly ? '内核态按对应表模块筛选；SlotId / CpuId 未知，点击日志行可查看来源路径。' : '模块映射筛选两态驱动；SlotId / CpuId 仅筛用户态与固件，内核设备显示未知；DevId / 类型仅筛固件。';
}
async function loadGroups() {
  const batch = state.batch, kind = state.kind, domain = $('driverDomain').value;
  $('groupSelect').replaceChildren(new Option('全部分块', '')); state.groups = [];
  if (!state.batch) return;
  if (['driver','firmware','all'].includes(state.kind)) {
    const result = await request(`groups?${new URLSearchParams({batch_id: state.batch, kind: state.kind, driver_domain: ['driver','all'].includes(kind) ? domain : ''})}`);
    if (batch !== state.batch || kind !== state.kind || domain !== $('driverDomain').value) return;
    state.groups = result.groups;
    if (kind !== 'all') result.groups.forEach(group => $('groupSelect').append(new Option(groupLabel(group), String(group.group_id))));
  }
  for (const id of ['slotFilter','cpuFilter','deviceFilter','logTypeFilter','moduleFilter']) $(id).value = '';
  fieldOptions(); applyViewControls();
}
for (const button of document.querySelectorAll('[data-kind]')) button.addEventListener('click', async () => {
  const navigation = ++state.navigation; resetReader();
  state.kind = button.dataset.kind;
  document.querySelectorAll('[data-kind]').forEach(tab => {const active = tab === button; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active));});
  applyViewControls();
  try {await loadGroups(); if (navigation === state.navigation) await query();} catch (exception) {error('browserError', exception);}
});
function filters() {
  const drivers = ['driver','all'].includes(state.kind), firmware = ['firmware','all'].includes(state.kind);
  return {batch_id: state.batch, kind: state.kind, group_id: state.kind === 'all' ? '' : $('groupSelect').value, driver_domain: drivers ? $('driverDomain').value : '', module: drivers ? $('moduleSelect').value : '', slot_id: $('slotFilter').value, cpu_id: $('cpuFilter').value, module_name: $('moduleFilter').value, device_id: firmware ? $('deviceFilter').value : '', log_type: firmware ? $('logTypeFilter').value : '', keyword: $('keyword').value, raw_search: String($('rawSearch').checked), start_time: $('startTime').value.trim(), end_time: $('endTime').value.trim(), view: state.kind === 'firmware' ? $('view').value : 'time', mode: drivers ? $('mode').value : 'concise', context_lines: $('contextLines').value};
}
function resetReader() {
  state.queryController?.abort(); state.generation++; state.rows.clear(); state.pages.clear(); state.total = 0;
  $('logRows').replaceChildren(); $('logSpacer').style.height = '0px'; $('emptyState').hidden = false;
  $('contextNavigation').hidden = true;
  $('exportButton').disabled = !state.batch || state.kind === 'diagnostic'; $('resultCount').textContent = state.batch ? '查询中…' : '请先解析日志';
}
async function query() {
  resetReader(); $('logViewport').scrollTop = 0; error('browserError', '');
  if (!state.batch) return;
  state.baseQuery = filters(); state.queryController = new AbortController();
  state.contextMode = state.kind !== 'diagnostic' && Number(state.baseQuery.context_lines) > 0 && Boolean(state.baseQuery.keyword);
  state.contextOffset = 0;
  await loadPage(0, state.generation);
}
async function loadPage(offset, generation) {
  if (!state.batch || state.pages.has(offset)) return;
  state.pages.add(offset);
  try {
    const value = await request(`${state.kind === 'diagnostic' ? 'diagnostics' : 'query'}?${new URLSearchParams({...state.baseQuery, offset: String(offset), limit: String(PAGE_SIZE)})}`, {signal: state.queryController.signal});
    if (generation !== state.generation) return;
    state.matchTotal = value.total;
    if (state.contextMode) {
      state.rows.clear(); state.total = value.records.length; state.contextOffset = offset;
      value.records.forEach((record, index) => state.rows.set(index, record));
      $('contextNavigation').hidden = false; $('contextPrevious').disabled = offset === 0; $('contextNext').disabled = !value.has_more;
    } else {
      state.total = value.total; value.records.forEach((record, index) => state.rows.set(offset + index, record));
    }
    // Keep a bounded number of loaded pages as well as a bounded DOM window.
    const centerPage = Math.floor($('logViewport').scrollTop / ROW_HEIGHT / PAGE_SIZE) * PAGE_SIZE;
    if (!state.contextMode) for (const page of [...state.pages]) if (Math.abs(page - centerPage) > PAGE_SIZE * 4) {state.pages.delete(page); for (let row = page; row < page + PAGE_SIZE; row++) state.rows.delete(row);}
    $('logSpacer').style.height = `${state.total * ROW_HEIGHT}px`; $('emptyState').hidden = state.total > 0;
    $('emptyState').textContent = '没有符合当前条件的日志。'; $('resultCount').textContent = state.contextMode ? `${value.total.toLocaleString()} 条命中 · 当前页 ${value.records.length} 条（含上下文）` : `${state.total.toLocaleString()} 条记录`; renderRows();
  } catch (exception) {if (exception.name !== 'AbortError' && generation === state.generation) {state.pages.delete(offset); error('browserError', exception); $('resultCount').textContent = '查询失败';}}
}
function highlighted(text, keyword) {
  const fragment = document.createDocumentFragment();
  if (!keyword) {fragment.append(document.createTextNode(text)); return fragment;}
  let start = 0, next;
  while ((next = text.indexOf(keyword, start)) >= 0) {fragment.append(document.createTextNode(text.slice(start, next))); const mark = document.createElement('mark'); mark.textContent = keyword; fragment.append(mark); start = next + keyword.length;}
  fragment.append(document.createTextNode(text.slice(start))); return fragment;
}
function renderRows() {
  const viewport = $('logViewport'); const first = Math.max(0, Math.floor(viewport.scrollTop / ROW_HEIGHT) - 8); const last = Math.min(state.total, first + Math.ceil(viewport.clientHeight / ROW_HEIGHT) + 20);
  const fragment = document.createDocumentFragment();
  for (let index = first; index < last; index++) {
    const record = state.rows.get(index); const row = document.createElement('div'); row.className = `log-row${record?.is_context ? ' context' : ''}`;
    const number = document.createElement('span'); number.className = 'log-line'; number.textContent = record?.line_number ?? '…'; row.append(number);
    const domain = record?.kind === 'firmware' ? 'firmware' : record?.kind === 'driver' ? record.driver_domain || 'user' : state.kind === 'diagnostic' ? 'diagnostic' : 'unclassified';
    const tag = document.createElement('span'); tag.className = `log-domain ${domain}`; tag.textContent = record ? {firmware:'固件',user:'用户态',kernel:'内核态',diagnostic:'诊断',unclassified:'未分类'}[domain] || domain : '…'; row.append(tag);
    let shown = String(record?.text ?? record?.message ?? '正在读取…');
    if (record?.kind === 'driver' && record.time_value === null && !shown.includes('时间未知')) shown = `[时间未知] ${shown}`;
    const text = document.createElement('span'); text.className = 'log-text'; text.append(highlighted(shown, state.baseQuery.keyword || '')); row.append(text);
    if (record) {row.title = `${record.source_path || ''}:${record.line_number || ''}`; if (record.source_id != null) row.addEventListener('click', () => showSource(record));}
    fragment.append(row);
  }
  $('logRows').style.transform = `translateY(${first * ROW_HEIGHT}px)`; $('logRows').replaceChildren(fragment);
  if (state.total && !state.contextMode) for (const offset of new Set([Math.floor(first / PAGE_SIZE) * PAGE_SIZE, Math.floor(Math.max(first, last - 1) / PAGE_SIZE) * PAGE_SIZE])) loadPage(offset, state.generation);
}
async function contextPage(offset) {
  resetReader(); $('logViewport').scrollTop = 0; state.queryController = new AbortController();
  await loadPage(offset, state.generation);
}
$('contextPrevious').addEventListener('click', () => contextPage(Math.max(0, state.contextOffset - PAGE_SIZE)));
$('contextNext').addEventListener('click', () => contextPage(state.contextOffset + PAGE_SIZE));
$('logViewport').addEventListener('scroll', () => requestAnimationFrame(renderRows));
window.addEventListener('resize', renderRows);
function scheduleQuery() {clearTimeout(state.timer); state.queryController?.abort(); state.timer = setTimeout(() => query().catch(exception => error('browserError', exception)), 280);}
for (const id of ['keyword','startTime','endTime']) $(id).addEventListener('input', scheduleQuery);
for (const id of ['groupSelect','slotFilter','cpuFilter','deviceFilter','logTypeFilter','moduleFilter']) $(id).addEventListener('change', () => {fieldOptions(); scheduleQuery();});
for (const id of ['driverDomain']) $(id).addEventListener('change', async () => {
  const navigation = ++state.navigation; resetReader(); applyViewControls();
  try {await loadGroups(); if (navigation === state.navigation) await query();} catch (exception) {error('browserError', exception);}
});
$('moduleSelect').addEventListener('change', () => { $('groupSelect').value = ''; fieldOptions(); scheduleQuery();});
for (const id of ['rawSearch','view','mode','contextLines']) $(id).addEventListener('change', scheduleQuery);
$('resetFilters').addEventListener('click', async () => {
  for (const id of ['groupSelect','slotFilter','cpuFilter','deviceFilter','logTypeFilter','moduleFilter','moduleSelect','driverDomain','keyword','startTime','endTime']) $(id).value = '';
  $('rawSearch').checked = false; $('contextLines').value = '0'; applyViewControls();
  try {await loadGroups(); scheduleQuery();} catch (exception) {error('browserError', exception);}
});
$('exportButton').addEventListener('click', () => {if (state.batch) window.location.href = API + 'export?' + new URLSearchParams(filters());});
async function showSource(record) {
  try {
    const value = await request(`source?${new URLSearchParams({batch_id: state.batch, source_id: String(record.source_id), offset: String(Math.max(0, Number(record.line_number) - 11)), limit: '200'})}`);
    $('sourceLocation').textContent = `${record.source_path || value.source_path || ''} · 行 ${record.line_number}`;
    $('sourceContent').textContent = (value.lines || value.records || []).map(line => `${String(line.line_number).padStart(6)}  ${line.text}`).join('\n');
    if (!$('sourceDialog').open) $('sourceDialog').showModal();
  } catch (exception) {error('browserError', exception);}
}
$('closeSource').addEventListener('click', () => $('sourceDialog').close());
applyViewControls();
refreshBatches().then(async result => {const ready = result.batches.find(batch => batch.status === 'ready'); if (ready && !state.active) await selectBatch(ready.batch_id);}).catch(exception => error('taskError', exception));
