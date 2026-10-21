/* Explicit product-log configuration, shared by the app and offline worker. */
(function (global) {
  "use strict";
  const TOP = ["version", "sources", "parsers", "rules", "module_aliases", "firmware_types", "modules"];
  const FIELDS = ["raw_text", "module_name", "log_type", "device_id", "slot_id", "cpu_id", "text"];
  const DIAG = {header: 2, outer_shape: 0, slot: 1, cpu: 1, module: 1, service: 1, info: 1, firmware: 4, firmware_marker: 0, uptime: 1};
  const SPACE_CLASS = "\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff";
  const NONSPACE_CLASS = "\\x00-\\x08\\x0e-\\x1f!-\\x9f\\xa1-\\u167f\\u1681-\\u1fff\\u200b-\\u2027\\u202a-\\u202e\\u2030-\\u205e\\u2060-\\u2fff\\u3001-\\ufefe\\uff00-\\uffff";
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const clone = value => JSON.parse(JSON.stringify(value));
  function object(value, label, keys, required = []) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(label + " must be an object");
    if (Object.keys(value).some(key => !keys.includes(key))) throw new Error(label + " has unknown fields");
    if (required.some(key => !own(value, key))) throw new Error(label + " is missing required fields");
    return value;
  }
  function text(value, label, empty = false) {
    if (typeof value !== "string" || value.includes("\0") || (!empty && !value.trim())) throw new Error(label + " must be a " + (empty ? "string" : "nonempty string"));
    return value;
  }
  function array(value, label) {
    if (!Array.isArray(value)) throw new Error(label + " must be an array");
    return value;
  }
  function strings(value, label, nonempty = false) {
    array(value, label);
    if (nonempty && !value.length) throw new Error(label + " must not be empty");
    value.forEach(item => text(item, label));
    if (new Set(value).size !== value.length) throw new Error(label + " must not contain duplicates");
    return value;
  }
  const cache = new Map();
  function portablePattern(pattern) {
    let output = "", inClass = false;
    for (let cursor = 0; cursor < pattern.length; cursor++) {
      const character = pattern[cursor];
      if (character === "\\" && cursor + 1 < pattern.length) {
        const escaped = pattern[++cursor];
        if (escaped === "s" || escaped === "S") output += inClass ? (escaped === "s" ? SPACE_CLASS : NONSPACE_CLASS) : "[" + (escaped === "s" ? SPACE_CLASS : "^" + SPACE_CLASS) + "]";
        else output += "\\" + escaped;
        continue;
      }
      if (character === "[") inClass = true;
      else if (character === "]") inClass = false;
      output += character;
    }
    return output;
  }
  function compiled(pattern, ignoreCase = false) {
    const key = JSON.stringify([pattern, ignoreCase]);
    if (cache.has(key)) return cache.get(key);
    if (/\(\?(?![:=!])/.test(pattern) || /\\[AZzGKNpPuU]/.test(pattern) || /(?:[*+?]|\})\+/.test(pattern)) throw new Error("Regex must use portable numbered captures and no inline flags");
    for (let cursor = 0; cursor < pattern.length; cursor++) {
      if (pattern[cursor] === "\\" && cursor + 1 < pattern.length) {
        const character = pattern[++cursor];
        if (/^[0-9]$/.test(character) || /^[a-zA-Z]$/.test(character) && !"bBdDsSwWfnrtvx".includes(character)) throw new Error("Regex contains a nonportable escape or backreference");
        if (character === "x" && !/^[0-9a-fA-F]{2}$/.test(pattern.slice(cursor + 1, cursor + 3))) throw new Error("Regex contains an invalid hexadecimal escape");
      }
    }
    let expression;
    try {expression = new RegExp(portablePattern(pattern), ignoreCase ? "i" : "");}
    catch (error) {throw new Error("Invalid regex: " + error.message);}
    if (cache.size >= 512) cache.delete(cache.keys().next().value);
    cache.set(key, expression);
    return expression;
  }
  function pattern(value, label, groups = 0, empty = false) {
    text(value, label, empty);
    if (value || !empty) {
      compiled(value);
      // A final empty alternative succeeds while retaining every numbered group.
      const captures = new RegExp("(?:" + value + ")|").exec("").length - 1;
      if (captures < groups) throw new Error(label + " requires at least " + groups + " numbered capture groups");
    }
  }
  function defaults() {
    if (!global.ProductLogDefaultConfig) throw new Error("Initial product-log configuration is missing");
    return clone(global.ProductLogDefaultConfig);
  }
  function normalize(value) {
    const config = value == null ? defaults() : clone(value);
    object(config, "analysis_config", TOP, TOP);
    if (config.version !== 1) throw new Error("analysis_config version must be 1");
    const parsers = new Map(), sources = new Map(), rules = new Set();
    for (const parser of array(config.parsers, "parsers")) {
      const keys = ["id", "engine", "time_format", "patterns", "months"];
      object(parser, "parser", keys, keys); text(parser.id, "parser.id");
      if (parsers.has(parser.id)) throw new Error("Duplicate parser id: " + parser.id);
      if (!["diag", "kernel"].includes(parser.engine)) throw new Error("parser.engine must be diag or kernel");
      if (!["iso", "dmesg", "none"].includes(parser.time_format)) throw new Error("Invalid parser time_format");
      if (parser.engine === "diag" && parser.time_format === "dmesg") throw new Error("diag parsers require iso or none time_format");
      const required = parser.engine === "diag" ? DIAG : parser.time_format === "none" ? {} : {date: parser.time_format === "dmesg" ? 5 : 1};
      object(parser.patterns, "parser.patterns", parser.engine === "diag" ? Object.keys(DIAG) : ["date"], Object.keys(required));
      for (const [key, value] of Object.entries(parser.patterns)) pattern(value, "parser.patterns." + key, required[key] || 0);
      strings(parser.months, "parser.months");
      if (parser.time_format === "dmesg" && (parser.months.length !== 12 || new Set(parser.months.map(month => month.toLowerCase())).size !== 12)) throw new Error("dmesg parsers require twelve distinct month names");
      parsers.set(parser.id, parser);
    }
    for (const source of array(config.sources, "sources")) {
      const keys = ["id", "name", "directory_patterns", "file_patterns", "file_regex", "parser_id", "recursive", "order"];
      object(source, "source", keys, keys); text(source.id, "source.id"); text(source.name, "source.name"); text(source.parser_id, "source.parser_id");
      if (sources.has(source.id)) throw new Error("Duplicate source id: " + source.id);
      if (!parsers.has(source.parser_id)) throw new Error("Unknown parser: " + source.parser_id);
      for (const directory of strings(source.directory_patterns, "source.directory_patterns", true)) {
        const normalized = directory.replace(/\\/g, "/");
        if (normalized.startsWith("/") || normalized.includes(":") || normalized.split("/").some(part => !part || part === "." || part === ".." || part.includes("**"))) throw new Error("source directory patterns must contain safe relative components");
      }
      for (const value of strings(source.file_patterns, "source.file_patterns", true)) if (/[\\/]/.test(value)) throw new Error("source file patterns must match basenames");
      pattern(source.file_regex, "source.file_regex", 0, true);
      if (typeof source.recursive !== "boolean") throw new Error("source.recursive must be boolean");
      const order = object(source.order, "source.order", ["type", "current", "pattern"], ["type"]);
      if (!["name", "current_last", "numeric_rotation"].includes(order.type)) throw new Error("Unknown source order type");
      if (own(order, "current")) text(order.current, "source.order.current", true);
      if (order.type === "current_last") text(order.current, "source.order.current");
      if (own(order, "pattern")) pattern(order.pattern, "source.order.pattern", order.type === "numeric_rotation" ? 1 : 0, order.type !== "numeric_rotation");
      else if (order.type === "numeric_rotation") throw new Error("numeric_rotation requires order.pattern");
      sources.set(source.id, source);
    }
    for (const rule of array(config.rules, "rules")) {
      const keys = ["id", "category", "component", "source_ids", "match"];
      object(rule, "rule", keys, keys); text(rule.id, "rule.id"); text(rule.component, "rule.component");
      if (rules.has(rule.id)) throw new Error("Duplicate rule id: " + rule.id);
      if (!["user_driver", "kernel_driver", "firmware"].includes(rule.category)) throw new Error("Invalid rule.category");
      for (const sourceId of strings(rule.source_ids, "rule.source_ids")) {
        if (!sources.has(sourceId)) throw new Error("Unknown source: " + sourceId);
        const engine = parsers.get(sources.get(sourceId).parser_id).engine;
        if ((engine === "kernel") !== (rule.category === "kernel_driver")) throw new Error("Source " + sourceId + " cannot produce " + rule.category);
      }
      const match = object(rule.match, "rule.match", ["field", "operator", "value", "values", "ignore_case"], ["field", "operator"]);
      if (!FIELDS.includes(match.field) || !["any", "equals", "contains", "in", "regex"].includes(match.operator)) throw new Error("Invalid rule match field or operator");
      if (own(match, "ignore_case") && typeof match.ignore_case !== "boolean") throw new Error("match.ignore_case must be boolean");
      if (["equals", "contains", "regex"].includes(match.operator)) text(match.value, "match.value");
      else if (own(match, "value")) text(match.value, "match.value", true);
      if (match.operator === "in" || own(match, "values")) strings(match.values, "match.values", match.operator === "in");
      if (match.operator === "regex") pattern(match.value, "match.value");
      rules.add(rule.id);
    }
    for (const alias of array(config.module_aliases, "module_aliases")) {
      const keys = ["pattern", "replacement", "ignore_case"];
      object(alias, "module alias", keys, keys); pattern(alias.pattern, "alias.pattern"); text(alias.replacement, "alias.replacement");
      if (typeof alias.ignore_case !== "boolean") throw new Error("alias.ignore_case must be boolean");
    }
    for (const alias of config.module_aliases) if (normalizeModule(alias.replacement, config) !== alias.replacement) throw new Error("alias.replacement must be a canonical literal name under the first matching alias");
    if (!config.firmware_types || typeof config.firmware_types !== "object" || Array.isArray(config.firmware_types)) throw new Error("firmware_types must be an object");
    for (const [key, value] of Object.entries(config.firmware_types)) {
      if (!/^\d+$/.test(key)) throw new Error("firmware_types keys must be nonnegative integer strings");
      text(value, "firmware type name");
    }
    const names = new Set();
    for (const module of array(config.modules, "modules")) {
      const keys = ["module", "user_module_name", "kernel_keyword"];
      object(module, "module", keys, keys); text(module.module, "module.module");
      if (names.has(module.module)) throw new Error("Duplicate module: " + module.module);
      names.add(module.module); text(module.user_module_name, "module.user_module_name", true); text(module.kernel_keyword, "module.kernel_keyword", true);
    }
    return config;
  }
  function activeSources(config) {
    const ids = new Set(config.rules.flatMap(rule => rule.source_ids));
    return config.sources.filter(source => ids.has(source.id));
  }
  const FULL_FOLD = {"µ":"μ","ß":"ss","ŉ":"ʼn","ſ":"s","ǰ":"ǰ","ͅ":"ι","ΐ":"ΐ","ΰ":"ΰ","ς":"σ","ϐ":"β","ϑ":"θ","ϕ":"φ","ϖ":"π","ϰ":"κ","ϱ":"ρ","ϵ":"ε","և":"եւ","ꭰ":"Ꭰ","ꭱ":"Ꭱ","ꭲ":"Ꭲ","ꭳ":"Ꭳ","ꭴ":"Ꭴ","ꭵ":"Ꭵ","ꭶ":"Ꭶ","ꭷ":"Ꭷ","ꭸ":"Ꭸ","ꭹ":"Ꭹ","ꭺ":"Ꭺ","ꭻ":"Ꭻ","ꭼ":"Ꭼ","ꭽ":"Ꭽ","ꭾ":"Ꭾ","ꭿ":"Ꭿ","ꮀ":"Ꮀ","ꮁ":"Ꮁ","ꮂ":"Ꮂ","ꮃ":"Ꮃ","ꮄ":"Ꮄ","ꮅ":"Ꮅ","ꮆ":"Ꮆ","ꮇ":"Ꮇ","ꮈ":"Ꮈ","ꮉ":"Ꮉ","ꮊ":"Ꮊ","ꮋ":"Ꮋ","ꮌ":"Ꮌ","ꮍ":"Ꮍ","ꮎ":"Ꮎ","ꮏ":"Ꮏ","ꮐ":"Ꮐ","ꮑ":"Ꮑ","ꮒ":"Ꮒ","ꮓ":"Ꮓ","ꮔ":"Ꮔ","ꮕ":"Ꮕ","ꮖ":"Ꮖ","ꮗ":"Ꮗ","ꮘ":"Ꮘ","ꮙ":"Ꮙ","ꮚ":"Ꮚ","ꮛ":"Ꮛ","ꮜ":"Ꮜ","ꮝ":"Ꮝ","ꮞ":"Ꮞ","ꮟ":"Ꮟ","ꮠ":"Ꮠ","ꮡ":"Ꮡ","ꮢ":"Ꮢ","ꮣ":"Ꮣ","ꮤ":"Ꮤ","ꮥ":"Ꮥ","ꮦ":"Ꮦ","ꮧ":"Ꮧ","ꮨ":"Ꮨ","ꮩ":"Ꮩ","ꮪ":"Ꮪ","ꮫ":"Ꮫ","ꮬ":"Ꮬ","ꮭ":"Ꮭ","ꮮ":"Ꮮ","ꮯ":"Ꮯ","ꮰ":"Ꮰ","ꮱ":"Ꮱ","ꮲ":"Ꮲ","ꮳ":"Ꮳ","ꮴ":"Ꮴ","ꮵ":"Ꮵ","ꮶ":"Ꮶ","ꮷ":"Ꮷ","ꮸ":"Ꮸ","ꮹ":"Ꮹ","ꮺ":"Ꮺ","ꮻ":"Ꮻ","ꮼ":"Ꮼ","ꮽ":"Ꮽ","ꮾ":"Ꮾ","ꮿ":"Ꮿ","ᏸ":"Ᏸ","ᏹ":"Ᏹ","ᏺ":"Ᏺ","ᏻ":"Ᏻ","ᏼ":"Ᏼ","ᏽ":"Ᏽ","ᲀ":"в","ᲁ":"д","ᲂ":"о","ᲃ":"с","ᲄ":"т","ᲅ":"т","ᲆ":"ъ","ᲇ":"ѣ","ᲈ":"ꙋ","ẖ":"ẖ","ẗ":"ẗ","ẘ":"ẘ","ẙ":"ẙ","ẚ":"aʾ","ẛ":"ṡ","ὐ":"ὐ","ὒ":"ὒ","ὔ":"ὔ","ὖ":"ὖ","ᾀ":"ἀι","ᾁ":"ἁι","ᾂ":"ἂι","ᾃ":"ἃι","ᾄ":"ἄι","ᾅ":"ἅι","ᾆ":"ἆι","ᾇ":"ἇι","ᾐ":"ἠι","ᾑ":"ἡι","ᾒ":"ἢι","ᾓ":"ἣι","ᾔ":"ἤι","ᾕ":"ἥι","ᾖ":"ἦι","ᾗ":"ἧι","ᾠ":"ὠι","ᾡ":"ὡι","ᾢ":"ὢι","ᾣ":"ὣι","ᾤ":"ὤι","ᾥ":"ὥι","ᾦ":"ὦι","ᾧ":"ὧι","ᾲ":"ὰι","ᾳ":"αι","ᾴ":"άι","ᾶ":"ᾶ","ᾷ":"ᾶι","ι":"ι","ῂ":"ὴι","ῃ":"ηι","ῄ":"ήι","ῆ":"ῆ","ῇ":"ῆι","ῒ":"ῒ","ΐ":"ΐ","ῖ":"ῖ","ῗ":"ῗ","ῢ":"ῢ","ΰ":"ΰ","ῤ":"ῤ","ῦ":"ῦ","ῧ":"ῧ","ῲ":"ὼι","ῳ":"ωι","ῴ":"ώι","ῶ":"ῶ","ῷ":"ῶι","ﬀ":"ff","ﬁ":"fi","ﬂ":"fl","ﬃ":"ffi","ﬄ":"ffl","ﬅ":"st","ﬆ":"st","ﬓ":"մն","ﬔ":"մե","ﬕ":"մի","ﬖ":"վն","ﬗ":"մխ"};
  function casefold(value) {return [...String(value).toLowerCase()].map(character => FULL_FOLD[character] || character).join("");}
  function recordEnabled(config, sourceIds, category, metadata, raw, body) {
    return config.rules.some(rule => {
      if (rule.category !== category || !rule.source_ids.some(id => sourceIds.includes(id))) return false;
      const match = rule.match;
      if (match.operator === "any") return true;
      let value = String((match.field === "raw_text" ? raw : match.field === "text" ? body : metadata[match.field]) ?? "");
      if (match.operator === "regex") return compiled(match.value, !!match.ignore_case).test(value);
      let expected = match.value || "", options = match.values || [];
      if (match.ignore_case) {value = casefold(value); expected = casefold(expected); options = options.map(casefold);}
      return match.operator === "equals" ? value === expected : match.operator === "contains" ? value.includes(expected) : options.includes(value);
    });
  }
  function normalizeModule(value, config = null) {
    value = String(value).trim();
    for (const alias of (config || defaults()).module_aliases) if (compiled(alias.pattern, alias.ignore_case).test(value)) return alias.replacement;
    return value;
  }
  function firmwareTypeName(value, config = null) {
    value = String(value);
    const types = (config || defaults()).firmware_types;
    return own(types, value) ? types[value] : value;
  }
  global.ProductLogConfig = {defaults, normalize, activeSources, recordEnabled, normalizeModule, firmwareTypeName, casefold, compileRegex: compiled};
})(globalThis);
