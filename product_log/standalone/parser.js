/* Browser-only equivalent of the product-log parser and its query semantics. */
(function (global) {
  "use strict";
  const DATE = "\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?";
  const HEADER = new RegExp("^(" + DATE + ")\\s+(\\S+)\\s+%%[^\\r\\n]+");
  const OUTER_SHAPE = /^\S+\s+\S+\s+\S+\s+%%/;
  const FIRMWARE = /Unit\s*\[\s*(\d+)\s*\]\s+firmware\s+logType\s*\[\s*(\d+)\s*\]\s+seg\s*\[\s*(\d+)\s*\]\s*:\s*(.*)/i;
  const KERNEL_DATE = /^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?\s+(\d{4})\]\s*/;
  const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  const FIELDS = {driver: ["slot_id", "cpu_id", "module_name"], firmware: ["slot_id", "cpu_id", "device_id", "log_type"], unclassified: []};
  const DEFAULT_MAPPINGS = [{module: "udk", user_module_name: "udk", kernel_keyword: "udk"}, {module: "UBE", user_module_name: "UBE", kernel_keyword: "mami"}];
  const normalizeModuleName = value => /^udk(?:\s|$)/i.test(String(value).trim()) ? "udk" : String(value).trim();
  const firmwareTypeName = value => ({"0": "bios", "1": "IMP", "2": "IMU", "3": "HSM"}[String(value)] || String(value));
  const field = (pattern, text) => (text.match(pattern) || ["", ""])[1];
  const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  // Number suffices for normal device dates; decimal strings preserve exact
  // microseconds outside JavaScript's safe-integer range without JSON BigInts.
  function timeValue(value) {
    const match = String(value).trim().replace(/\s+/g, " ").replace("T", " ").match(/^(\d{4})-(\d{2})-(\d{2})(?: (\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?$/);
    if (!match) throw new Error("日期格式无效: " + value);
    const [year, month, day, hour, minute, second] = match.slice(1, 7).map(value => Number(value || 0));
    const date = new Date(0);
    date.setUTCFullYear(year, month - 1, day);
    date.setUTCHours(hour, minute, second, 0);
    if (year < 1 || year > 9999 || date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day || date.getUTCHours() !== hour || date.getUTCMinutes() !== minute || date.getUTCSeconds() !== second) throw new Error("日期值无效: " + value);
    const result = BigInt(date.getTime()) * 1000n + BigInt((match[7] || "").padEnd(6, "0"));
    return result <= BigInt(Number.MAX_SAFE_INTEGER) && result >= BigInt(Number.MIN_SAFE_INTEGER) ? Number(result) : result.toString();
  }
  function kernelTimestamp(line) {
    const match = line.match(KERNEL_DATE);
    if (!match) return ["", null, line];
    const month = MONTHS.indexOf(match[1].toLowerCase()) + 1;
    const stamp = `${match[5]}-${String(month).padStart(2, "0")}-${match[2].padStart(2, "0")} ${match[3]}${match[4] ? "." + match[4] : ""}`;
    try { return [stamp, timeValue(stamp), line.slice(match[0].length)]; }
    catch (_) { return ["", null, line]; }
  }
  function identity(prefix, values) { return prefix + "-" + encodeURIComponent(JSON.stringify(values)); }
  function parseSources(input) {
    if (!Array.isArray(input)) throw new Error("日志来源必须是数组");
    const batch = {sources: [], records: [], segments: [], dumps: [], diagnostics: [], mappings: DEFAULT_MAPPINGS.map(row => ({...row}))};
    const diagnostic = (source, line, code, message, dump_id = null) => batch.diagnostics.push({id: batch.diagnostics.length + 1, source_id: source.source_id, source_path: source.path, line_number: line, code, message, dump_id});
    const addRecord = (source, kind, raw, line_number, metadata = {}, body = raw) => {
      const record = {id: batch.records.length + 1, kind, source_id: source.source_id, source_rank: source.source_rank, line_number,
        time: "", time_value: null, slot_id: "", cpu_id: "", module_name: "", raw_module_name: "", driver_domain: "", device_id: "", log_type: "", dump_id: null, segment_id: null, seg: null, firmware_time: "", ...metadata,
        source_path: source.path, relative_path: source.relative_path, origin_path: source.origin_path, archive_path: source.archive_path, member_path: source.member_path,
        raw_text: raw, search_text: body};
      record.text = kind === "firmware" ? `[${record.time}]${body}` : kind === "driver" ? record.driver_domain === "kernel" ? `${record.time || "[时间未知]"} ${body}` : `${record.time} Info=${body}` : raw;
      if (kind === "firmware") record.firmware_time = field(/^\[(\d+(?:\.\d+)?)\]/, body);
      batch.records.push(record);
    };
    const sourceIds = new Set();
    input.forEach((item, source_rank) => {
      if (!item || typeof item.text !== "string") throw new Error("每个来源必须包含 text 字符串");
      const domain = item.log_domain || "user";
      if (!["user", "kernel"].includes(domain)) throw new Error("log_domain 必须是 user 或 kernel");
      const source = {...item, source_id: item.source_id || "source-" + source_rank, source_rank, log_domain: domain,
        path: item.path || item.relative_path || item.original_name || "source-" + source_rank,
        relative_path: item.relative_path || item.path || item.original_name || "source-" + source_rank,
        origin_path: item.origin_path || item.path || item.relative_path || "", archive_path: item.archive_path || "", member_path: item.member_path || "", device_source: item.device_source || "", encoding: item.encoding || "utf-8"};
      if (sourceIds.has(source.source_id)) throw new Error("来源 source_id 重复");
      sourceIds.add(source.source_id);
      const lines = source.text ? source.text.split("\n") : [];
      if (source.text.endsWith("\n")) lines.pop();
      source.lines = lines.map((text, index) => ({line_number: index + 1, text: (index === 0 ? text.replace(/^\uFEFF/, "") : text).replace(/[\r\n]+$/, "")}));
      source.line_count = source.lines.length;
      if (source.text.startsWith("\uFEFF")) source.encoding = "utf-8-sig";
      batch.sources.push(source);
      if (source.text.slice(0, 8192).includes("\0")) {
        source.encoding = "binary";
        source.line_count = 0;
        diagnostic(source, 0, "binary_file", "NUL bytes: source is not a supported text log");
        return;
      }
      let current = null;
      source.lines.forEach(({line_number, text: line}) => {
        if (domain === "kernel") {
          if (line.trim()) {
            const [time, time_value, body] = kernelTimestamp(line);
            addRecord(source, "driver", line, line_number, {time, time_value, driver_domain: "kernel"}, body);
          }
          return;
        }
        const header = line.match(HEADER);
        if (header || OUTER_SHAPE.test(line)) {
          current = null;
          const time = header ? header[1].replace(/\s+/g, " ") : "";
          let time_value;
          try { if (!header) throw new Error(); time_value = timeValue(time); }
          catch (_) { diagnostic(source, line_number, "invalid_time", "Invalid outer record date/time"); addRecord(source, "unclassified", line, line_number); return; }
          const raw_module_name = field(/\bModuleName\s*=\s*([^,\r\n)]+)/i, line).trim();
          const metadata = {time, time_value, slot_id: field(/\bSlotId\s*=\s*(\d+)/i, line), cpu_id: field(/\bCpuId\s*=\s*(\d+)/i, line), module_name: raw_module_name, raw_module_name};
          if (!metadata.slot_id || !metadata.cpu_id || !raw_module_name) {
            diagnostic(source, line_number, "missing_fields", "Outer record missing SlotId/CpuId/ModuleName");
            addRecord(source, "unclassified", line, line_number, metadata); return;
          }
          const firmware = line.match(FIRMWARE);
          if (firmware) {
            if (firmware[3].length > 10 || Number(firmware[3]) > 2147483647) {
              diagnostic(source, line_number, "invalid_segment", "Firmware seg number is outside supported integer range");
              addRecord(source, "unclassified", line, line_number, metadata); return;
            }
            current = {...metadata, device_id: firmware[1], log_type: firmware[2], seg: Number(firmware[3]), segment_id: source.source_id + ":" + line_number};
            batch.segments.push({...current, source_id: source.source_id, source_rank, line_number, host: header[2], service: field(/\bService\s*=\s*([^;\s,]+)/i, line), device_source: source.device_source, raw_header: line, dump_id: null});
            let inline = firmware[4];
            if (line.slice(0, firmware.index).includes("(") && inline.endsWith(")")) inline = inline.slice(0, -1);
            if (inline) addRecord(source, "firmware", line, line_number, current, inline);
          } else if (/\bfirmware\s+logType/i.test(line)) {
            diagnostic(source, line_number, "invalid_segment", "Malformed firmware Unit/logType/seg metadata");
            addRecord(source, "unclassified", line, line_number, metadata);
          } else {
            const info = line.match(/\bInfo\s*=\s*(.*)/i);
            let body = info ? info[1] : line;
            if (info && line.slice(0, info.index).includes("(") && body.endsWith(")")) body = body.slice(0, -1);
            if (!info) diagnostic(source, line_number, "missing_info", "Driver record missing Info; full source retained");
            addRecord(source, "driver", line, line_number, {...metadata, driver_domain: "user", module_name: normalizeModuleName(raw_module_name)}, body);
          }
        } else if (current) addRecord(source, "firmware", line, line_number, current);
        else if (line.trim()) { diagnostic(source, line_number, "orphan_line", "Line outside a recognized driver/firmware record"); addRecord(source, "unclassified", line, line_number); }
      });
    });
    const sourceById = new Map(batch.sources.map(source => [source.source_id, source]));
    const active = new Map();
    const dumpById = new Map();
    const recordsBySegment = new Map();
    batch.records.forEach(row => {
      if (!row.segment_id) return;
      if (!recordsBySegment.has(row.segment_id)) recordsBySegment.set(row.segment_id, []);
      recordsBySegment.get(row.segment_id).push(row);
    });
    batch.segments.forEach(segment => {
      const stream = JSON.stringify(["device_source", "host", "service", "slot_id", "cpu_id", "module_name", "device_id", "log_type"].map(key => segment[key]));
      let previous = active.get(stream), dump;
      const source = sourceById.get(segment.source_id);
      if (segment.seg === 0 || !previous) {
        dump = {dump_id: "dump-" + (batch.dumps.length + 1), stream_key: stream, anchor_time: segment.time, anchor_value: segment.time_value, first_value: segment.time_value, source_rank: segment.source_rank, line_number: segment.line_number, missing_start: Number(segment.seg !== 0), segments: []};
        batch.dumps.push(dump); dumpById.set(dump.dump_id, dump);
        if (segment.seg !== 0) diagnostic(source, segment.line_number, "missing_start", `Partial firmware dump starts at seg[${segment.seg}]; seg[0] was not found`, dump.dump_id);
      } else {
        dump = previous.dump;
        if (dump.segments.some(item => item.seg === segment.seg)) diagnostic(source, segment.line_number, "duplicate_segment", `Repeated seg[${segment.seg}] retained`, dump.dump_id);
        if (BigInt(segment.time_value) < BigInt(previous.time_value)) diagnostic(source, segment.line_number, "segment_time_reversed", "Segment read time goes backwards in source order; real times retained", dump.dump_id);
      }
      active.set(stream, {dump, time_value: segment.time_value});
      dump.segments.push(segment); segment.dump_id = dump.dump_id;
      if (BigInt(segment.time_value) < BigInt(dump.first_value)) dump.first_value = segment.time_value;
      for (const row of recordsBySegment.get(segment.segment_id) || []) row.dump_id = dump.dump_id;
    });
    for (const dump of batch.dumps) {
      const firstBySeg = new Map();
      for (const segment of dump.segments) {
        const previous = firstBySeg.get(segment.seg);
        firstBySeg.set(segment.seg, previous ? {...previous, source_id: compareText(previous.source_id, segment.source_id) < 0 ? previous.source_id : segment.source_id, line_number: Math.min(previous.line_number, segment.line_number)} : segment);
      }
      let previous = -1;
      [...firstBySeg.keys()].sort((a, b) => a - b).forEach(seg => {
        const item = firstBySeg.get(seg);
        if (previous >= 0 && seg > previous + 1) diagnostic(sourceById.get(item.source_id), item.line_number, "missing_segments", `Missing seg[${previous + 1}..${seg - 1}]`, dump.dump_id);
        previous = seg;
      });
    }
    batch.records.forEach(row => {
      const dump = dumpById.get(row.dump_id);
      row.dump_anchor = dump ? dump.anchor_value : row.time_value;
      row.dump_rank = dump ? dump.source_rank : row.source_rank;
      row.dump_line = dump ? dump.line_number : row.line_number;
    });
    const count = predicate => batch.records.filter(predicate).length;
    batch.counts = {files: batch.sources.length, driver_records: count(row => row.kind === "driver"), firmware_records: count(row => row.kind === "firmware"), unclassified_records: count(row => row.kind === "unclassified"), user_records: count(row => row.driver_domain === "user"), kernel_records: count(row => row.driver_domain === "kernel"), dumps: batch.dumps.length, diagnostics: batch.diagnostics.length};
    return batch;
  }
  function validateMappings(rows) {
    if (!Array.isArray(rows)) throw new Error("模块对应表必须是数组");
    const seen = new Set();
    return rows.map(row => {
      if (!row || typeof row !== "object" || Array.isArray(row)) throw new Error("模块对应表每行必须是对象");
      const result = {};
      for (const key of ["module", "user_module_name", "kernel_keyword"]) {
        const value = row[key] === undefined ? "" : row[key];
        if (typeof value !== "string" || value.includes("\0")) throw new Error("模块表字段必须是文本且不包含 NUL");
        result[key] = value.trim();
      }
      if (!result.module || seen.has(result.module)) throw new Error("模块名称必须非空且不能重复");
      seen.add(result.module); result.user_module_name = normalizeModuleName(result.user_module_name);
      return result;
    });
  }
  function mappingRows(batch) {
    const rows = validateMappings(batch.mappings || DEFAULT_MAPPINGS), mapped = new Set(rows.map(row => row.user_module_name)), names = new Set(rows.map(row => row.module));
    const modules = [...new Set(batch.records.filter(row => row.kind === "driver" && row.driver_domain === "user").map(row => row.module_name))].sort(compareText);
    for (const module of modules) {
      if (!module || mapped.has(module)) continue;
      let label = module, suffix = 2;
      while (names.has(label)) label = module + " (" + suffix++ + ")";
      rows.push({module: label, user_module_name: module, kernel_keyword: ""}); mapped.add(module); names.add(label);
    }
    return rows;
  }
  // Full-fold differences after toLowerCase (Unicode 14, Python's reference
  // table) keep keyword matching deterministic and independent of locale.
  const FULL_FOLD = {"µ":"μ","ß":"ss","ŉ":"ʼn","ſ":"s","ǰ":"ǰ","ͅ":"ι","ΐ":"ΐ","ΰ":"ΰ","ς":"σ","ϐ":"β","ϑ":"θ","ϕ":"φ","ϖ":"π","ϰ":"κ","ϱ":"ρ","ϵ":"ε","և":"եւ","ꭰ":"Ꭰ","ꭱ":"Ꭱ","ꭲ":"Ꭲ","ꭳ":"Ꭳ","ꭴ":"Ꭴ","ꭵ":"Ꭵ","ꭶ":"Ꭶ","ꭷ":"Ꭷ","ꭸ":"Ꭸ","ꭹ":"Ꭹ","ꭺ":"Ꭺ","ꭻ":"Ꭻ","ꭼ":"Ꭼ","ꭽ":"Ꭽ","ꭾ":"Ꭾ","ꭿ":"Ꭿ","ꮀ":"Ꮀ","ꮁ":"Ꮁ","ꮂ":"Ꮂ","ꮃ":"Ꮃ","ꮄ":"Ꮄ","ꮅ":"Ꮅ","ꮆ":"Ꮆ","ꮇ":"Ꮇ","ꮈ":"Ꮈ","ꮉ":"Ꮉ","ꮊ":"Ꮊ","ꮋ":"Ꮋ","ꮌ":"Ꮌ","ꮍ":"Ꮍ","ꮎ":"Ꮎ","ꮏ":"Ꮏ","ꮐ":"Ꮐ","ꮑ":"Ꮑ","ꮒ":"Ꮒ","ꮓ":"Ꮓ","ꮔ":"Ꮔ","ꮕ":"Ꮕ","ꮖ":"Ꮖ","ꮗ":"Ꮗ","ꮘ":"Ꮘ","ꮙ":"Ꮙ","ꮚ":"Ꮚ","ꮛ":"Ꮛ","ꮜ":"Ꮜ","ꮝ":"Ꮝ","ꮞ":"Ꮞ","ꮟ":"Ꮟ","ꮠ":"Ꮠ","ꮡ":"Ꮡ","ꮢ":"Ꮢ","ꮣ":"Ꮣ","ꮤ":"Ꮤ","ꮥ":"Ꮥ","ꮦ":"Ꮦ","ꮧ":"Ꮧ","ꮨ":"Ꮨ","ꮩ":"Ꮩ","ꮪ":"Ꮪ","ꮫ":"Ꮫ","ꮬ":"Ꮬ","ꮭ":"Ꮭ","ꮮ":"Ꮮ","ꮯ":"Ꮯ","ꮰ":"Ꮰ","ꮱ":"Ꮱ","ꮲ":"Ꮲ","ꮳ":"Ꮳ","ꮴ":"Ꮴ","ꮵ":"Ꮵ","ꮶ":"Ꮶ","ꮷ":"Ꮷ","ꮸ":"Ꮸ","ꮹ":"Ꮹ","ꮺ":"Ꮺ","ꮻ":"Ꮻ","ꮼ":"Ꮼ","ꮽ":"Ꮽ","ꮾ":"Ꮾ","ꮿ":"Ꮿ","ᏸ":"Ᏸ","ᏹ":"Ᏹ","ᏺ":"Ᏺ","ᏻ":"Ᏻ","ᏼ":"Ᏼ","ᏽ":"Ᏽ","ᲀ":"в","ᲁ":"д","ᲂ":"о","ᲃ":"с","ᲄ":"т","ᲅ":"т","ᲆ":"ъ","ᲇ":"ѣ","ᲈ":"ꙋ","ẖ":"ẖ","ẗ":"ẗ","ẘ":"ẘ","ẙ":"ẙ","ẚ":"aʾ","ẛ":"ṡ","ὐ":"ὐ","ὒ":"ὒ","ὔ":"ὔ","ὖ":"ὖ","ᾀ":"ἀι","ᾁ":"ἁι","ᾂ":"ἂι","ᾃ":"ἃι","ᾄ":"ἄι","ᾅ":"ἅι","ᾆ":"ἆι","ᾇ":"ἇι","ᾐ":"ἠι","ᾑ":"ἡι","ᾒ":"ἢι","ᾓ":"ἣι","ᾔ":"ἤι","ᾕ":"ἥι","ᾖ":"ἦι","ᾗ":"ἧι","ᾠ":"ὠι","ᾡ":"ὡι","ᾢ":"ὢι","ᾣ":"ὣι","ᾤ":"ὤι","ᾥ":"ὥι","ᾦ":"ὦι","ᾧ":"ὧι","ᾲ":"ὰι","ᾳ":"αι","ᾴ":"άι","ᾶ":"ᾶ","ᾷ":"ᾶι","ι":"ι","ῂ":"ὴι","ῃ":"ηι","ῄ":"ήι","ῆ":"ῆ","ῇ":"ῆι","ῒ":"ῒ","ΐ":"ΐ","ῖ":"ῖ","ῗ":"ῗ","ῢ":"ῢ","ΰ":"ΰ","ῤ":"ῤ","ῦ":"ῦ","ῧ":"ῧ","ῲ":"ὼι","ῳ":"ωι","ῴ":"ώι","ῶ":"ῶ","ῷ":"ῶι","ﬀ":"ff","ﬁ":"fi","ﬂ":"fl","ﬃ":"ffi","ﬄ":"ffl","ﬅ":"st","ﬆ":"st","ﬓ":"մն","ﬔ":"մե","ﬕ":"մի","ﬖ":"վն","ﬗ":"մխ"};
  function casefold(text) { return [...String(text).toLowerCase()].map(value => FULL_FOLD[value] || value).join(""); }
  function kernelMatches(row, keyword) { return row.kind === "driver" && row.driver_domain === "kernel" && !!keyword && casefold(row.raw_text).includes(casefold(keyword)); }
  function listMappings(batch) {
    return mappingRows(batch).map(row => {
      const user_count = batch.records.filter(record => record.driver_domain === "user" && record.module_name === row.user_module_name).length;
      return {...row, user_count, user_exists: !!user_count, kernel_configured: !!row.kernel_keyword, kernel_count: batch.records.filter(record => kernelMatches(record, row.kernel_keyword)).length};
    });
  }
  function saveMappings(batch, rows) { batch.mappings = validateMappings(rows); return listMappings(batch); }
  function listGroups(batch, kind = "all", driver_domain = "") {
    if (!["driver", "firmware", "unclassified", "all"].includes(kind)) throw new Error("日志类型无效");
    if (!["", "user", "kernel"].includes(driver_domain)) throw new Error("驱动来源无效");
    const groups = new Map();
    function groupFor(row, groupKind) {
      const values = FIELDS[groupKind].map(key => row[key]);
      const group_id = identity("group", [groupKind, ...values]);
      if (!groups.has(group_id)) groups.set(group_id, {group_id, kind: groupKind, driver_domain: groupKind === "driver" ? "user" : "", slot_id: "", cpu_id: "", module_name: "", device_id: "", log_type: "", count: 0, dump_count: 0, source_count: 0, module_names: [], sources: new Set(), dumps: new Set(), modules: new Set(), ...Object.fromEntries(FIELDS[groupKind].map(key => [key, row[key]]))});
      return groups.get(group_id);
    }
    if (kind === "firmware" || kind === "all") for (const segment of batch.segments) {
      const group = groupFor(segment, "firmware");
      group.sources.add(segment.source_id); group.dumps.add(segment.dump_id); group.modules.add(segment.module_name);
    }
    for (const row of batch.records) {
      if (!(row.kind === kind || kind === "all" && ["driver", "firmware"].includes(row.kind))) continue;
      if (row.driver_domain === "kernel" || row.kind === "driver" && driver_domain === "kernel") continue;
      const group = groupFor(row, row.kind);
      group.count++; group.sources.add(row.source_id); if (row.dump_id) group.dumps.add(row.dump_id); group.modules.add(row.module_name);
    }
    const result = [...groups.values()].map(group => {
      const {sources, dumps, modules, ...item} = group;
      item.module_names = [...modules].sort(compareText); item.source_count = sources.size; item.dump_count = dumps.size;
      if (item.module_names.length === 1) item.module_name = item.module_names[0];
      if (item.kind === "firmware") item.log_type_name = firmwareTypeName(item.log_type);
      return item;
    }).sort((a, b) => { for (const key of ["slot_id", "cpu_id", "device_id", "log_type", "module_name"]) { const value = compareText(a[key], b[key]); if (value) return value; } return 0; });
    if (["driver", "all"].includes(kind) && driver_domain !== "user") for (const mapping of mappingRows(batch)) {
      const rows = batch.records.filter(row => kernelMatches(row, mapping.kernel_keyword));
      if (rows.length) result.push({group_id: identity("kernel", [mapping.module]), kind: "driver", driver_domain: "kernel", module: mapping.module, module_name: mapping.module, slot_id: "", cpu_id: "", device_id: "", log_type: "", count: rows.length, dump_count: 0, source_count: new Set(rows.map(row => row.source_id)).size, module_names: [mapping.module]});
    }
    return result;
  }
  function pageValues(offset = 0, limit = 200) {
    if (typeof offset === "boolean" || typeof limit === "boolean" || !Number.isInteger(Number(offset)) || !Number.isInteger(Number(limit)) || Number(offset) < 0 || Number(limit) < 1 || Number(limit) > 500) throw new Error("offset 必须非负，limit 必须为 1..500");
    return [Number(offset), Number(limit)];
  }
  function predicateFor(batch, options) {
    const kind = options.kind || "all", domain = options.driver_domain || "";
    if (!["all", "driver", "firmware", "unclassified"].includes(kind)) throw new Error("日志类型无效");
    if (!["", "user", "kernel"].includes(domain)) throw new Error("驱动来源无效");
    const mappings = mappingRows(batch);
    const mapping = options.module && ["all", "driver"].includes(kind) ? mappings.find(row => row.module === options.module) : null;
    if (options.module && ["all", "driver"].includes(kind) && !mapping) throw new Error("模块不属于当前日志批次");
    let group = null, kernelGroup = null;
    if (options.group_id) {
      if (options.group_id.startsWith("kernel-")) {
        kernelGroup = mappings.find(row => row.kernel_keyword && identity("kernel", [row.module]) === options.group_id);
        if (!["all", "driver"].includes(kind) || !kernelGroup) throw new Error("日志分组不存在");
      } else {
        group = listGroups(batch, kind).find(row => row.group_id === options.group_id);
        if (!group) throw new Error("日志分组不存在");
      }
    }
    const start = options.start_time ? BigInt(timeValue(options.start_time)) : null, end = options.end_time ? BigInt(timeValue(options.end_time)) : null;
    if (start !== null && end !== null && start > end) throw new Error("开始时间不能晚于结束时间");
    const headers = options.keyword && options.raw_search ? new Map(batch.segments.map(segment => [segment.segment_id, segment.raw_header])) : null;
    const containsKeyword = text => options.case_sensitive === false ? casefold(text).includes(casefold(options.keyword)) : text.includes(String(options.keyword));
    return row => {
      if (!(row.kind === kind || kind === "all" && ["driver", "firmware"].includes(row.kind))) return false;
      if (domain && row.kind === "driver" && row.driver_domain !== domain) return false;
      if (kernelGroup && !kernelMatches(row, kernelGroup.kernel_keyword)) return false;
      if (group && (row.kind !== group.kind || row.kind === "driver" && row.driver_domain !== "user" || FIELDS[group.kind].some(key => row[key] !== group[key]))) return false;
      for (const key of ["slot_id", "cpu_id", "module_name", "device_id", "log_type"]) {
        const value = options[key];
        if (value === undefined || value === null || value === "") continue;
        if (["device_id", "log_type"].includes(key)) { if (row.kind === "firmware" && row[key] !== String(value)) return false; }
        else if (row.driver_domain !== "kernel" && row[key] !== (key === "module_name" && row.kind !== "firmware" ? normalizeModuleName(value) : String(value))) return false;
      }
      if (mapping && row.kind !== "firmware" && !(row.driver_domain === "user" && row.module_name === mapping.user_module_name || kernelMatches(row, mapping.kernel_keyword))) return false;
      if (options.keyword && !(options.raw_search ? containsKeyword(row.raw_text) || containsKeyword(headers.get(row.segment_id) || "") : containsKeyword(row.search_text))) return false;
      if ((start !== null || end !== null) && row.time_value === null) return false;
      if (start !== null && BigInt(row.time_value) < start || end !== null && BigInt(row.time_value) > end) return false;
      return true;
    };
  }
  function orderFor(view) {
    if (!["time", "stitched"].includes(view)) throw new Error("排序视图必须是 time 或 stitched");
    const keys = view === "time" ? ["time_value", "source_rank", "line_number", "id"] : ["dump_anchor", "dump_rank", "dump_line", "seg", "source_rank", "line_number", "id"];
    return (a, b) => {
      for (const key of keys) {
        const av = a[key], bv = b[key];
        if (av === null || bv === null) { if (av !== bv) return av === null ? 1 : -1; }
        else if (["time_value", "dump_anchor"].includes(key)) { const difference = BigInt(av) - BigInt(bv); if (difference) return difference < 0n ? -1 : 1; }
        else if (av !== bv) return av < bv ? -1 : 1;
      }
      return 0;
    };
  }
  function displayRecord(row, mode, is_context = false, preserveFirmwareTime = false) {
    const {search_text, dump_anchor, dump_rank, dump_line, ...result} = row;
    result.is_context = is_context;
    if (result.kind === "firmware") result.log_type_name = firmwareTypeName(result.log_type);
    if (mode === "raw" && !(preserveFirmwareTime && result.kind === "firmware") || mode === "original" && result.kind !== "firmware") result.text = result.raw_text;
    return result;
  }
  function query(batch, options = {}) {
    const [offset, limit] = pageValues(options.offset, options.limit);
    const kind = options.kind || "all", mode = options.mode || "concise", view = kind === "all" && options.view === "stitched" ? "time" : options.view || "time";
    const context = Number(options.context_lines || 0);
    if (!Number.isInteger(context) || context < 0 || context > 10) throw new Error("上下文行数必须为 0..10");
    if (!["concise", "original", "raw"].includes(mode)) throw new Error("显示模式无效");
    if (kind === "diagnostic") {
      const records = batch.diagnostics.slice(offset, offset + limit).map(row => ({...row, kind: "diagnostic", text: `[${row.code}] ${row.message}`, time: "", time_value: null, is_context: false}));
      return {records, total: batch.diagnostics.length, has_more: offset + records.length < batch.diagnostics.length, offset, limit};
    }
    const sort = orderFor(view), all = batch.records.filter(predicateFor(batch, options)).sort(sort);
    const matches = all.slice(offset, offset + limit), hitIds = new Set(matches.map(row => row.id));
    let records = matches;
    if (context && options.keyword && matches.length) {
      const structural = predicateFor(batch, {...options, keyword: "", start_time: "", end_time: ""});
      records = batch.records.filter(row => structural(row) && matches.some(match => row.source_id === match.source_id && Math.abs(row.line_number - match.line_number) <= context)).sort(sort);
    }
    return {records: records.map(row => displayRecord(row, mode, !hitIds.has(row.id), kind === "all")), total: all.length, has_more: offset + matches.length < all.length, offset, limit};
  }
  function exportText(batch, options = {}) {
    const kind = options.kind || "all", mode = options.mode || "concise", view = kind === "all" && options.view === "stitched" ? "time" : options.view || "time";
    if (!["concise", "original", "raw"].includes(mode)) throw new Error("显示模式无效");
    if (kind === "diagnostic") return batch.diagnostics.map(row => `[${row.code}] ${row.message}\n`).join("");
    return batch.records.filter(predicateFor(batch, options)).sort(orderFor(view)).map(row => displayRecord(row, mode, false, kind === "all").text + "\n").join("");
  }
  function sourceContext(batch, source_id, options = {}) {
    const [offset, limit] = pageValues(options.offset, options.limit);
    const source = batch.sources.find(row => row.source_id === source_id);
    if (!source) throw new Error("日志来源不存在");
    const {text, bytes, lines: allLines, ...metadata} = source, lines = allLines.slice(offset, offset + limit).map(row => ({...row}));
    return {source: metadata, source_id, source_path: source.path, lines, records: lines, total: source.line_count, has_more: offset + lines.length < source.line_count, offset, limit};
  }
  global.ProductLogOfflineParser = {parseSources, query, listGroups, listMappings, saveMappings, sourceContext, exportText, timeValue, normalizeModuleName, firmwareTypeName};
})(globalThis);
