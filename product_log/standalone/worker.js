/* Offline worker bridge. This has no server calls or imports. */
(function () {
  'use strict';
  let batch = null, prepared = null;
  function requireReady() {
    if (!batch) throw new Error('请先解析日志');
    return batch;
  }
  function normalizeRules(values) {
    const rules = {...values};
    for (const field of ['file_patterns', 'archive_patterns', 'copy_patterns']) {
      if (typeof rules[field] === 'string') {
        rules[field] = rules[field].split(',').map(value => value.trim()).filter(Boolean);
      }
    }
    return rules;
  }
  async function dispatch(action, payload) {
    const parser = globalThis.ProductLogOfflineParser;
    if (action === 'start') {
      batch = null; prepared = null;
      const started = performance.now();
      const rules = normalizeRules(payload.rules || {});
      rules.upload_kind = payload.upload_kind;
      prepared = await globalThis.ProductLogOfflineArchives.prepare(
        payload.files.map(file => ({path:file.path, bytes:new Uint8Array(file.bytes)})), rules,
        progress => postMessage({progress})
      );
      postMessage({progress:{stage:'parsing',count:0,message:'解析用户态、内核态与固件日志'}});
      batch = parser.parseSources(prepared.sources);
      return {status:'ready',counts:batch.counts,archives:prepared.archives,
              input_root:'浏览器所选输入（保留相对来源路径）',target_paths:prepared.targets,
              duration_seconds:((performance.now()-started)/1000).toFixed(2)};
    }
    requireReady();
    if (action === 'query') return parser.query(batch, payload);
    if (action === 'groups') return {groups:parser.listGroups(batch, payload.kind || 'all', payload.driver_domain || '')};
    if (action === 'mappings') return {modules:payload.modules === undefined
      ? parser.listMappings(batch) : parser.saveMappings(batch, payload.modules)};
    if (action === 'source') {
      const offset = Math.max(0, Number(payload.line_number || 1)-1-Number(payload.context_lines || 5));
      const result = parser.sourceContext(batch,payload.source_id,{offset,limit:Math.max(11,Number(payload.context_lines||5)*2+1)});
      const source = batch.sources.find(row => row.source_id === payload.source_id);
      return {...result,source_path:source ? source.relative_path : ''};
    }
    if (action === 'export') return {text:parser.exportText(batch,payload),filename:'product-logs-'+(payload.kind || 'all')+'.log'};
    throw new Error('Unknown offline action: '+action);
  }
  globalThis.onmessage = async event => {
    const {id,action,payload={}} = event.data;
    try { postMessage({id,result:await dispatch(action,payload)}); }
    catch (error) { postMessage({id,error:String(error && error.message || error)}); }
  };
})();
