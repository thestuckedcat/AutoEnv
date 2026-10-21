/* Browser-only archive preparation. Codecs are supplied by the bundled page. */
(function (global) {
  "use strict";
  const MAX_FILES = 10000;
  const MAX_BYTES = 5 * 1024 * 1024 * 1024;
  const textDecoder = new TextDecoder("utf-8");
  const decoder = (encoding, bytes, fatal = true) => new TextDecoder(encoding, {fatal}).decode(bytes);
  const basename = path => path.split("/").at(-1);
  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  const below = (path, folder) => folder === "" || path === folder || path.startsWith(folder + "/");
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  function safePath(name, allowEmpty = false) {
    if (typeof name !== "string" || /\x00/.test(name)) throw new Error("无效文件路径");
    const path = name.replace(/\\/g, "/");
    const raw = path.split("/");
    if (path.startsWith("/") || /^[A-Za-z]:/.test(path) || raw.includes("..")) {
      throw new Error("压缩包包含不安全的路径: " + name);
    }
    const parts = raw.filter(part => part && part !== ".");
    if (!allowEmpty && !parts.length) throw new Error("压缩包文件路径为空");
    for (const part of parts) {
      if (/[:<>"|?*]/.test(part) || /[. ]$/.test(part) || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(part)) {
        throw new Error("压缩包包含不安全的文件名: " + name);
      }
    }
    return parts.join("/");
  }
  function glob(pattern) {
    let result = "^";
    for (let index = 0; index < pattern.length; index++) {
      const char = pattern[index];
      if (char === "*") result += ".*";
      else if (char === "?") result += ".";
      else if (char === "[") {
        let end = index + 1;
        if (pattern[end] === "!") end++;
        if (pattern[end] === "]") end++;
        while (end < pattern.length && pattern[end] !== "]") end++;
        if (end === pattern.length) result += "\\[";
        else {
          let inside = pattern.slice(index + 1, end);
          const negate = inside.startsWith("!");
          if (negate) inside = inside.slice(1);
          inside = inside.replace(/\\/g, "\\\\").replace(/\[/g, "\\[").replace(/\]/g, "\\]");
          if (inside.startsWith("^") && !negate) inside = "\\" + inside;
          result += "[" + (negate ? "^" : "") + inside + "]";
          index = end;
        }
      } else result += char.replace(/[\\^$+?.()|{}]/g, "\\$&");
    }
    try { return new RegExp(result + "$"); }
    catch (_) { throw new Error("无效通配符: " + pattern); }
  }
  function matches(name, pattern, fuzzy = true) {
    return /[*?[]/.test(pattern) ? glob(pattern).test(name) : fuzzy ? name.includes(pattern) : name === pattern;
  }
  function patterns(values) {
    if (!values) return [];
    if (typeof values === "string") values = [values];
    if (!Array.isArray(values)) throw new Error("文件通配符必须是列表");
    return [...new Set(values)].map(pattern => {
      if (typeof pattern !== "string" || /[/\\\x00]/.test(pattern)) throw new Error("文件通配符不能包含路径");
      return pattern;
    });
  }
  function selected(name, values) { return !values.length || values.some(pattern => matches(name, pattern)); }
  const legacyFilePatterns = values => patterns(values).map(pattern => /[*?[]/.test(pattern) ? pattern : "*" + pattern + "*");
  function decodeText(bytes) {
    if (bytes[0] === 0xff && bytes[1] === 0xfe) return decoder("utf-16le", bytes.subarray(2), false);
    if (bytes[0] === 0xfe && bytes[1] === 0xff) return decoder("utf-16be", bytes.subarray(2), false);
    try { return decoder("utf-8", bytes); }
    catch (_) {
      // Match the local parser's per-line fallback for mixed-encoding logs.
      const lines = [];
      let start = 0;
      const line = end => {
        const payload = bytes.subarray(start, end);
        try { lines.push(decoder("utf-8", payload)); }
        catch (_) {
          try { lines.push(decoder("gb18030", payload)); }
          catch (_) { lines.push(decoder("utf-8", payload, false)); }
        }
        start = end;
      };
      for (let index = 0; index < bytes.length; index++) if (bytes[index] === 10) line(index + 1);
      if (start < bytes.length) line(bytes.length);
      return lines.join("");
    }
  }
  function view(bytes) { return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); }
  function need(bytes, offset, length, label) {
    if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(length) || offset < 0 || length < 0 || offset + length > bytes.length) {
      throw new Error("压缩包数据截断: " + label);
    }
  }
  function uint64(data, offset) {
    const number = data.getUint32(offset, true) + data.getUint32(offset + 4, true) * 4294967296;
    if (!Number.isSafeInteger(number)) throw new Error("压缩包数值超出浏览器支持范围");
    return number;
  }
  const crcTable = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let value = n;
    for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    crcTable[n] = value >>> 0;
  }
  function crc32(bytes) {
    let value = 0xffffffff;
    for (const byte of bytes) value = crcTable[(value ^ byte) & 255] ^ (value >>> 8);
    return (value ^ 0xffffffff) >>> 0;
  }
  function memberValidator(budget, check) {
    const members = new Map();
    let count = 0;
    return (name, directory, size = 0) => {
      check();
      if (++count > Math.max(1024, budget.max_files * 8)) throw new Error("压缩包成员过多");
      const path = safePath(name, directory);
      if (!path) return path;
      const parts = path.split("/");
      for (let length = 1; length < parts.length; length++) {
        const parent = parts.slice(0, length).join("/");
        const found = members.get(parent.toLowerCase());
        if (found && (!found.directory || found.path !== parent)) throw new Error("压缩包路径冲突: " + name);
        if (!found) members.set(parent.toLowerCase(), {path: parent, directory: true, explicit: false});
      }
      const found = members.get(path.toLowerCase());
      if (found && (found.explicit || !directory || found.path !== path)) throw new Error("压缩包重复文件或路径冲突: " + name);
      members.set(path.toLowerCase(), {path, directory, explicit: true});
      if (!directory) {
        if (!Number.isSafeInteger(size) || size < 0) throw new Error("压缩包成员大小无效");
        budget.files++;
        budget.bytes += size;
        if (budget.files > budget.max_files || budget.bytes > budget.max_bytes) throw new Error("解压数量或大小超过限制");
      }
      return path;
    };
  }
  function tarNumber(bytes) {
    if (bytes[0] & 0x80) {
      if (bytes[0] & 0x40) throw new Error("TAR 包含负数大小");
      let value = bytes[0] & 0x7f;
      for (let index = 1; index < bytes.length; index++) value = value * 256 + bytes[index];
      if (!Number.isSafeInteger(value)) throw new Error("TAR 数值超过支持范围");
      return value;
    }
    const text = textDecoder.decode(bytes).replace(/\x00.*$/, "").trim();
    if (!text) return 0;
    if (!/^[0-7]+$/.test(text)) throw new Error("TAR 数值格式错误");
    const value = parseInt(text, 8);
    if (!Number.isSafeInteger(value)) throw new Error("TAR 数值超过支持范围");
    return value;
  }
  function tarHeader(bytes, offset) {
    need(bytes, offset, 512, "TAR header");
    const block = bytes.subarray(offset, offset + 512);
    if (block.every(byte => !byte)) return null;
    const expected = tarNumber(block.subarray(148, 156));
    let unsigned = 0, signed = 0;
    for (let index = 0; index < 512; index++) {
      const byte = index >= 148 && index < 156 ? 32 : block[index];
      unsigned += byte;
      signed += byte > 127 ? byte - 256 : byte;
    }
    if (expected !== unsigned && expected !== signed) throw new Error("TAR 头部校验失败");
    const string = (start, end) => textDecoder.decode(block.subarray(start, end)).replace(/\x00.*$/, "");
    let name = string(0, 100);
    const prefix = string(345, 500);
    if (string(257, 263).startsWith("ustar") && prefix) name = prefix + "/" + name;
    return {name, size: tarNumber(block.subarray(124, 136)), type: String.fromCharCode(block[156])};
  }
  function isTar(bytes) {
    if (bytes.length < 512) return false;
    try { tarHeader(bytes, 0); return true; }
    catch (_) { return false; }
  }
  function paxRecords(bytes) {
    const result = {};
    for (let cursor = 0; cursor < bytes.length;) {
      let space = cursor;
      while (space < bytes.length && bytes[space] !== 32) space++;
      const lengthText = textDecoder.decode(bytes.subarray(cursor, space));
      if (!/^\d+$/.test(lengthText)) throw new Error("TAR PAX 记录格式错误");
      const length = Number(lengthText);
      if (!Number.isSafeInteger(length) || length <= space - cursor + 1 || cursor + length > bytes.length || bytes[cursor + length - 1] !== 10) {
        throw new Error("TAR PAX 记录截断");
      }
      const text = textDecoder.decode(bytes.subarray(space + 1, cursor + length - 1));
      const equal = text.indexOf("=");
      if (equal <= 0) throw new Error("TAR PAX 记录格式错误");
      const key = text.slice(0, equal);
      if (key.startsWith("GNU.sparse") || key.startsWith("SCHILY.realsize")) throw new Error("不支持稀疏 TAR 文件");
      result[key] = text.slice(equal + 1);
      cursor += length;
    }
    return result;
  }
  async function extractTar(bytes, budget, check) {
    need(bytes, 0, 512, "TAR header");
    const add = memberValidator(budget, check);
    const result = [], directories = [];
    let cursor = 0, globalPax = {}, nextPax = {}, longName = null, metadataBytes = 0, entries = 0;
    const metadataLimit = Math.max(1024, budget.max_files * 8) * 512 + 8 * 1024 * 1024;
    while (cursor + 512 <= bytes.length) {
      check();
      const header = tarHeader(bytes, cursor);
      if (!header) break;
      cursor += 512;
      if (["x", "X", "g", "L", "K"].includes(header.type)) {
        need(bytes, cursor, header.size, "TAR metadata");
        metadataBytes += header.size + 512;
        if (metadataBytes > metadataLimit) throw new Error("TAR 元数据超过限制");
        const payload = bytes.subarray(cursor, cursor + header.size);
        if (header.type === "g") globalPax = {...globalPax, ...paxRecords(payload)};
        else if (header.type === "x" || header.type === "X") nextPax = {...nextPax, ...paxRecords(payload)};
        else if (header.type === "L") longName = textDecoder.decode(payload).replace(/\x00.*$/, "");
        else throw new Error("压缩包链接不受支持");
        cursor += Math.ceil(header.size / 512) * 512;
        continue;
      }
      const pax = {...globalPax, ...nextPax};
      nextPax = {};
      const name = pax.path === undefined ? longName === null ? header.name : longName : pax.path;
      longName = null;
      let size = header.size;
      if (pax.size !== undefined) {
        if (!/^\d+$/.test(pax.size)) throw new Error("TAR PAX 文件大小无效");
        size = Number(pax.size);
      }
      if (["1", "2"].includes(header.type)) throw new Error("压缩包链接不受支持: " + name);
      if (!["0", "\x00", "7", "5"].includes(header.type)) throw new Error("压缩包特殊文件不受支持: " + name);
      const directory = header.type === "5";
      const path = add(name, directory, size);
      need(bytes, cursor, size, name);
      if (directory) directories.push(path);
      else result.push({path, bytes: bytes.subarray(cursor, cursor + size)});
      cursor += Math.ceil(size / 512) * 512;
      if (++entries % 128 === 0) await tick();
    }
    if (cursor > bytes.length || (cursor < bytes.length && bytes.length - cursor < 512)) throw new Error("TAR 文件尾部截断");
    if (longName !== null || Object.keys(nextPax).length) throw new Error("TAR 元数据缺少对应文件");
    return {files: result, directories};
  }
  function zipEnd(bytes) {
    if (bytes.length < 22) return -1;
    const data = view(bytes);
    for (let index = bytes.length - 22; index >= Math.max(0, bytes.length - 65557); index--) {
      if (data.getUint32(index, true) === 0x06054b50 && index + 22 + data.getUint16(index + 20, true) === bytes.length) return index;
    }
    return -1;
  }
  const cp437 = "ÇüéâäàåçêëèïîìÄÅÉæÆôöòûùÿÖÜ¢£¥₧ƒáíóúñÑªº¿⌐¬½¼¡«»░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀αßΓπΣσµτΦΘΩδ∞φε∩≡±≥≤⌠⌡÷≈°∙·√ⁿ²■ ";
  function zipName(bytes, utf8) {
    if (utf8) return decoder("utf-8", bytes);
    return Array.from(bytes, byte => byte < 128 ? String.fromCharCode(byte) : cp437[byte - 128]).join("");
  }
  async function extractZip(bytes, codecs, budget, check) {
    const end = zipEnd(bytes);
    if (end < 0) throw new Error("ZIP 中央目录缺失或截断");
    const data = view(bytes), add = memberValidator(budget, check), checked = [], directories = [];
    let count = data.getUint16(end + 10, true), centralSize = data.getUint32(end + 12, true), offset = data.getUint32(end + 16, true);
    if (data.getUint16(end + 4, true) || data.getUint16(end + 6, true) || data.getUint16(end + 8, true) !== count) throw new Error("不支持分卷 ZIP");
    if (count === 0xffff || offset === 0xffffffff || centralSize === 0xffffffff) {
      need(bytes, end - 20, 20, "ZIP64 locator");
      if (data.getUint32(end - 20, true) !== 0x07064b50 || data.getUint32(end - 16, true) || data.getUint32(end - 4, true) !== 1) throw new Error("ZIP64 定位信息错误");
      const zip64 = uint64(data, end - 12);
      need(bytes, zip64, 56, "ZIP64 end");
      if (data.getUint32(zip64, true) !== 0x06064b50 || data.getUint32(zip64 + 16, true) || data.getUint32(zip64 + 20, true)) throw new Error("ZIP64 中央目录错误");
      count = uint64(data, zip64 + 32);
      if (uint64(data, zip64 + 24) !== count) throw new Error("不支持分卷 ZIP64");
      centralSize = uint64(data, zip64 + 40);
      offset = uint64(data, zip64 + 48);
    }
    need(bytes, offset, centralSize, "ZIP central directory");
    if (count > Math.max(1024, budget.max_files * 8)) throw new Error("ZIP 成员过多");
    const centralEnd = offset + centralSize;
    for (let index = 0; index < count; index++) {
      check(); need(bytes, offset, 46, "ZIP central entry");
      if (data.getUint32(offset, true) !== 0x02014b50) throw new Error("ZIP 中央目录记录错误");
      const flags = data.getUint16(offset + 8, true), method = data.getUint16(offset + 10, true);
      if (flags & 0x41) throw new Error("不支持加密 ZIP");
      if (![0, 8, 12, 14].includes(method)) throw new Error("不支持 ZIP 压缩算法 " + method);
      const expectedCrc = data.getUint32(offset + 16, true), nameLength = data.getUint16(offset + 28, true), extraLength = data.getUint16(offset + 30, true), commentLength = data.getUint16(offset + 32, true);
      need(bytes, offset + 46, nameLength + extraLength + commentLength, "ZIP member metadata");
      const rawName = bytes.subarray(offset + 46, offset + 46 + nameLength);
      let name = zipName(rawName, flags & 0x800), compressed = data.getUint32(offset + 20, true), size = data.getUint32(offset + 24, true), localOffset = data.getUint32(offset + 42, true), disk = data.getUint16(offset + 34, true);
      let extra = offset + 46 + nameLength, extraEnd = extra + extraLength;
      while (extra < extraEnd) {
        need(bytes, extra, 4, "ZIP extra");
        const id = data.getUint16(extra, true), length = data.getUint16(extra + 2, true), payload = extra + 4;
        if (payload + length > extraEnd) throw new Error("ZIP 扩展信息截断");
        if (id === 1) {
          let cursor = payload;
          const read = () => { if (cursor + 8 > payload + length) throw new Error("ZIP64 成员扩展信息缺失"); const value = uint64(data, cursor); cursor += 8; return value; };
          if (size === 0xffffffff) size = read();
          if (compressed === 0xffffffff) compressed = read();
          if (localOffset === 0xffffffff) localOffset = read();
          if (disk === 0xffff) { if (cursor + 4 > payload + length) throw new Error("ZIP64 分卷信息缺失"); disk = data.getUint32(cursor, true); }
        } else if (id === 0x7075 && length >= 5 && bytes[payload] === 1 && data.getUint32(payload + 1, true) === crc32(rawName)) {
          name = decoder("utf-8", bytes.subarray(payload + 5, payload + length));
        }
        extra = payload + length;
      }
      if (disk) throw new Error("不支持分卷 ZIP");
      if ([size, compressed, localOffset].includes(0xffffffff)) throw new Error("ZIP64 成员信息缺失");
      const fileType = (data.getUint32(offset + 38, true) >>> 16) & 0xf000;
      if (fileType === 0xa000) throw new Error("压缩包链接不受支持: " + name);
      if (![0, 0x8000, 0x4000].includes(fileType)) throw new Error("压缩包特殊文件不受支持: " + name);
      const directory = name.endsWith("/") || fileType === 0x4000;
      const path = add(name, directory, size);
      need(bytes, localOffset, 30, "ZIP local header");
      if (data.getUint32(localOffset, true) !== 0x04034b50 || data.getUint16(localOffset + 8, true) !== method || data.getUint16(localOffset + 6, true) !== flags) throw new Error("ZIP 本地头部与中央目录不一致");
      const localNameLength = data.getUint16(localOffset + 26, true), localExtraLength = data.getUint16(localOffset + 28, true);
      need(bytes, localOffset + 30, localNameLength + localExtraLength, "ZIP local metadata");
      const localName = bytes.subarray(localOffset + 30, localOffset + 30 + localNameLength);
      if (localName.length !== rawName.length || localName.some((value, at) => value !== rawName[at])) throw new Error("ZIP 本地文件名与中央目录不一致");
      const start = localOffset + 30 + localNameLength + localExtraLength;
      need(bytes, start, compressed, name);
      if (start + compressed > (centralEnd - centralSize)) throw new Error("ZIP 成员与中央目录重叠");
      if (directory) directories.push(path);
      else checked.push({path, start, compressed, size, method, flags, expectedCrc});
      offset += 46 + nameLength + extraLength + commentLength;
    }
    if (offset > centralEnd) throw new Error("ZIP 中央目录大小错误");
    const result = [];
    for (const member of checked) {
      check();
      const compressed = bytes.subarray(member.start, member.start + member.compressed);
      const output = member.method === 0 ? compressed : member.method === 14 ? await codecs.unlzma(compressed, member.size, member.flags)
        : await codecs[member.method === 8 ? "inflateRaw" : "bunzip"](compressed);
      const payload = asBytes(output);
      if (payload.length !== member.size || crc32(payload) !== member.expectedCrc) throw new Error("ZIP 文件长度或 CRC 校验失败: " + member.path);
      result.push({path: member.path, bytes: payload});
      if (result.length % 64 === 0) await tick();
    }
    return {files: result, directories};
  }
  async function extractRar(bytes, codecs, budget, check) {
    if (typeof codecs.readRar !== "function") throw new Error("页面缺少内嵌 RAR 解压器");
    const add = memberValidator(budget, check), paths = new Map();
    // UnRAR's JS bridge does not expose RAR5 redirection records (hard links).
    // Its native header pass validates the general metadata; libarchive adds
    // link validation for RAR5 before any member is actually decoded.
    const beforeExtract = bytes[6] === 1 ? () => codecs.readArchive(bytes, () => false, check) : null;
    const entries = await codecs.readRar(bytes, entry => {
      const path = add(entry.name, entry.directory, entry.size);
      paths.set(entry.name, path);
      return true;
    }, check, beforeExtract);
    return {files: entries.filter(entry => !entry.directory).map(entry => {
      const payload = asBytes(entry.bytes);
      // UnRAR validates the actual archive checksum (CRC32 or RAR5 BLAKE2).
      // RAR5's optional CRC field is zero for BLAKE2 entries, so it cannot be
      // compared to a newly computed CRC32 as if every archive stored one.
      return {path: paths.get(entry.name), bytes: payload};
    }),
      directories: entries.filter(entry => entry.directory).map(entry => paths.get(entry.name)), format: "rar"};
  }
  function asBytes(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    throw new Error("解压器返回了无效数据");
  }
  function archiveKind(file) {
    const bytes = file.bytes;
    if (bytes[0] === 0x52 && bytes[1] === 0x61 && bytes[2] === 0x72 && bytes[3] === 0x21
      && bytes[4] === 0x1a && bytes[5] === 0x07 && (bytes[6] === 0 || bytes[6] === 1 && bytes[7] === 0)) return "rar";
    if ((bytes[0] === 0x50 && bytes[1] === 0x4b && [[3, 4], [5, 6], [7, 8]].some(pair => bytes[2] === pair[0] && bytes[3] === pair[1])) || zipEnd(bytes) >= 0) return "zip";
    if (bytes[0] === 0x1f && bytes[1] === 0x8b) return "gz";
    if (bytes[0] === 0x42 && bytes[1] === 0x5a && bytes[2] === 0x68) return "bz2";
    if (bytes[0] === 0xfd && bytes[1] === 0x37 && bytes[2] === 0x7a && bytes[3] === 0x58 && bytes[4] === 0x5a && bytes[5] === 0) return "xz";
    if (isTar(bytes) || /(?:\.tar|\.tar\.gz|\.tgz|\.tar\.bz2|\.tar\.xz)$/i.test(file.path)) return "tar";
    return null;
  }
  async function extract(file, kind, codecs, budget, check) {
    check();
    if (kind === "zip") return {...await extractZip(file.bytes, codecs, budget, check), format: "zip"};
    if (kind === "rar") return extractRar(file.bytes, codecs, budget, check);
    let bytes = file.bytes;
    if (kind !== "tar") {
      const fn = codecs[{gz: "ungzip", bz2: "bunzip", xz: "unxz"}[kind]];
      if (typeof fn !== "function") throw new Error("页面缺少内嵌解压器: " + kind);
      bytes = asBytes(await fn(bytes));
      check();
      if (bytes.length > budget.max_bytes - budget.bytes + Math.max(1024, budget.max_files * 8) * 512 + 8 * 1024 * 1024) throw new Error("解压大小超过限制");
    }
    if (kind === "tar" || isTar(bytes) || /(?:\.tar(?:\.gz|\.bz2|\.xz)?|\.tgz)$/i.test(file.path)) {
      return {...await extractTar(bytes, budget, check), format: kind === "tar" ? "tar" : "tar." + kind};
    }
    // The optional gzip FNAME is deliberately ignored, matching the local SDK.
    const name = basename(file.path).replace(/\.(?:gz|bz2|xz)$/i, "");
    const path = memberValidator(budget, check)(name === basename(file.path) ? "data" : name, false, bytes.length);
    return {files: [{path, bytes}], directories: [], format: kind};
  }
  function inferredDirectories(files, explicit = []) {
    const result = new Set(explicit);
    for (const file of files) {
      const parts = file.path.split("/");
      for (let length = 1; length < parts.length; length++) result.add(parts.slice(0, length).join("/"));
    }
    return [...result].sort(compare);
  }
  function findDirectory(directories, pattern, exactDefault = false, recursive = true) {
    if (typeof pattern !== "string" || !pattern || /(^\/|[:\x00])/.test(pattern)) throw new Error("目录规则必须是安全的相对路径");
    const parts = pattern.replace(/\\/g, "/").split("/");
    if (parts.some(part => !part || part === "." || part === ".." || part.includes("**"))) throw new Error("目录规则包含无效路径组件");
    const found = directories.filter(directory => {
      const pathParts = directory.logical_path.split("/");
      if (!recursive && pathParts.length > 1) return false;
      if (parts.length === 1) return matches(pathParts.at(-1), parts[0], !exactDefault);
      return pathParts.length >= parts.length && parts.every((pattern, index) => matches(pathParts[pathParts.length - parts.length + index], pattern, false));
    });
    // A top-level uploaded archive is already the input baseline. Nested
    // archive directories compete only when no baseline directory matches.
    const baseline = found.filter(directory => directory.provenance_parts.length === 1);
    const candidates = baseline.length ? baseline : found;
    const ancestors = candidates.filter(candidate => candidates.every(other => relativeDirectoryDepth(other, candidate) !== null));
    if (ancestors.length === 1) return ancestors[0];
    if (candidates.length > 1) throw new Error("目录匹配不唯一: " + pattern + " → " + candidates.map(directory => directory.path).join(", "));
    return candidates[0];
  }
  function relativeDirectoryDepth(file, directory) {
    const ancestry = directory.provenance_parts, parts = file.provenance_parts;
    if (parts.length < ancestry.length || ancestry.slice(0, -1).some((part, index) => parts[index] !== part)) return null;
    const target = ancestry.at(-1), first = parts[ancestry.length - 1];
    if (!below(first, target)) return null;
    const relative = target ? first.slice(target.length + 1) : first;
    // Count ordinary parent directories in every member component. The archive
    // filename marks a packing boundary, and contributes no directory level.
    return [relative, ...parts.slice(ancestry.length)].reduce((depth, part) => depth + part.split("/").length - 1, 0);
  }
  async function prepare(inputFiles, rules = {}, progress = null, cancelled = null) {
    const explicitConfig = Object.prototype.hasOwnProperty.call(rules, "analysis_config");
    if (explicitConfig && rules.analysis_config == null) throw new Error("analysis_config must be an object");
    let analysisConfig = global.ProductLogConfig.normalize(rules.analysis_config);
    const check = () => { if (cancelled && cancelled()) throw new Error("解析已取消"); };
    const emit = (stage, count, message) => { check(); if (progress) progress({stage, count, message}); };
    const codecs = global.OfflineCompression || {};
    const budget = {files: 0, bytes: 0, max_files: rules.max_files ?? MAX_FILES, max_bytes: rules.max_bytes ?? MAX_BYTES};
    for (const value of [budget.max_files, budget.max_bytes]) if (!Number.isSafeInteger(value) || value < 0) throw new Error("解压数量或大小限制无效");
    if (!Array.isArray(inputFiles) || !inputFiles.length) throw new Error("请拖入压缩包或选择日志文件夹");
    let files = inputFiles.map(file => ({path: safePath(file.path), bytes: asBytes(file.bytes)}));
    // Uploads are immutable inputs. Every collected path below is virtual.
    const inputPaths = new Set();
    for (const file of files) { if (inputPaths.has(file.path)) throw new Error("输入文件路径重复: " + file.path); inputPaths.add(file.path); }
    const archives = [], sources = [], targets = Object.create(null), sourceTargets = Object.create(null), targetDirectories = Object.create(null);
    let directories = inferredDirectories(files), topArchive = "";
    emit("preparing", 0, "在浏览器内准备日志输入");
    if (files.length === 1 && rules.upload_kind !== "directory") {
      const kind = archiveKind(files[0]);
      if (kind) {
        topArchive = files[0].path;
        emit("extracting", 0, "解压输入: " + topArchive);
        const expanded = await extract(files[0], kind, codecs, budget, check);
        archives.push({path: topArchive, origin_path: topArchive, format: expanded.format, level: 0});
        files = expanded.files;
        directories = inferredDirectories(files, expanded.directories);
      }
    }
    files = files.map(file => ({...file, logical_path: file.path, relative_path: file.path, provenance_parts: [file.path],
      origin_path: topArchive ? topArchive + "!/" + file.path : file.path,
      archive_path: topArchive, member_path: topArchive ? file.path : "", container_path: "", level: 1}));
    // Prepare the entire forest before choosing directories. Archive boundaries
    // are separate provenance components, even if an input name contains "!/".
    const allDirectories = new Map(directories.map(path => [JSON.stringify([path]), {path, logical_path: path, provenance_parts: [path]}])), queue = [];
    function enqueue(file) {
      file.kind = archiveKind(file);
      if (file.kind) queue.push(file);
    }
    files.forEach(enqueue);
    for (let cursor = 0; cursor < queue.length; cursor++) {
      const original = queue[cursor];
      emit("extracting", archives.length, "解压: " + original.relative_path);
      const expanded = await extract(original, original.kind, codecs, budget, check);
      archives.push({path: original.relative_path, origin_path: original.origin_path,
        format: expanded.format, level: original.level, provenance_parts: original.provenance_parts});
      const prefix = original.relative_path + "!/";
      const parentParts = original.logical_path.split("/").slice(0, -1);
      const logicalMember = path => [...parentParts, path].join("/");
      const children = expanded.files.map(file => ({...file, path: prefix + file.path,
        logical_path: logicalMember(file.path),
        relative_path: prefix + file.path, provenance_parts: [...original.provenance_parts, file.path],
        origin_path: original.origin_path + "!/" + file.path, archive_path: original.relative_path,
        member_path: file.path, container_path: prefix, level: original.level + 1}));
      for (const directory of inferredDirectories(expanded.files, expanded.directories)) {
        const parts = [...original.provenance_parts, directory];
        allDirectories.set(JSON.stringify(parts), {path: prefix + directory, logical_path: logicalMember(directory), provenance_parts: parts});
      }
      files.push(...children);
      children.forEach(enqueue);
      await tick();
    }
    directories = [...allDirectories.values()].sort((a, b) => compare(a.path, b.path) || compare(JSON.stringify(a.provenance_parts), JSON.stringify(b.provenance_parts)));
    const requestedMode = rules.collection_mode || "product";
    if (!["auto", "product", "generic"].includes(requestedMode)) throw new Error("无效的日志收集模式");
    const recursive = rules.recursive !== false;
    const profiles = new Map(analysisConfig.parsers.map(profile => [profile.id, profile]));
    if (!explicitConfig) {
      // Old SDK arguments edit the explicit initial template; they never
      // override a user-supplied configuration or revive removed rules.
      for (const source of analysisConfig.sources) {
        const engine = profiles.get(source.parser_id).engine;
        const directory = engine === "diag" ? rules.diag_directory_pattern : rules.var_directory_pattern;
        if (directory) {
          const pattern = !directory.includes("/") && !directory.includes("\\") && !/[*?[]/.test(directory) && !["diag", "varlog", "var"].includes(directory) ? "*" + directory + "*" : directory;
          source.directory_patterns = engine === "kernel" && directory === "varlog" ? [directory, "var"] : [pattern];
        }
        if (engine === "diag") {
          if (patterns(rules.file_patterns).length) { source.file_patterns = legacyFilePatterns(rules.file_patterns); source.file_regex = ""; }
          source.recursive = recursive;
        }
      }
    }
    let configuredSources = global.ProductLogConfig.activeSources(analysisConfig);
    let mode = requestedMode;
    const bind = (source, directory) => {
      targetDirectories[source.id] = directory;
      sourceTargets[source.id] = directory.path;
      const domain = profiles.get(source.parser_id).engine === "kernel" ? "kernel" : "user";
      if (targets[domain] === undefined) targets[domain] = directory.path;
    };
    if (explicitConfig || mode !== "generic" && !(mode === "auto" && rules.directory_pattern)) {
      for (const source of configuredSources) {
        for (const pattern of source.directory_patterns) {
          const directory = findDirectory(directories, pattern, true, true);
          if (directory !== undefined) { bind(source, directory); break; }
        }
      }
      if (!Object.keys(targetDirectories).length && configuredSources.length && !explicitConfig) {
        if (mode === "product") throw new Error("找不到符合条件的日志来源文件夹");
        mode = "generic";
      } else mode = "product";
    } else mode = "generic";
    if (mode === "generic") {
      const template = configuredSources.find(source => profiles.get(source.parser_id).engine === "diag");
      if (template) {
        const source = {...template, id: "generic", directory_patterns: [rules.directory_pattern || "input"], file_patterns: legacyFilePatterns(rules.file_patterns).length ? legacyFilePatterns(rules.file_patterns) : ["*"], file_regex: "", recursive,
          order: {type: "name", current: "", pattern: ""}};
        analysisConfig.sources = [source];
        analysisConfig.rules = analysisConfig.rules.filter(rule => rule.source_ids.includes(template.id)).map(rule => ({...rule, source_ids: [source.id]}));
        const target = rules.directory_pattern ? findDirectory(directories, rules.directory_pattern, false, recursive) : {path: "", logical_path: "", provenance_parts: [""]};
        if (target === undefined) throw new Error("找不到指定文件夹: " + rules.directory_pattern);
        configuredSources = [source]; bind(source, target);
      }
    }
    if (!explicitConfig && targets.user !== undefined && targets.user === targets.kernel) throw new Error("diag 与 varlog 必须是不同文件夹");
    // Retain validation of old requests; archive filters no longer suppress
    // preparation because target folders can be hidden in any archive layer.
    patterns(rules.archive_patterns);
    const copyPatterns = patterns(rules.copy_patterns);
    const renameMap = rules.rename_map || {};
    if (!renameMap || typeof renameMap !== "object" || Array.isArray(renameMap)) throw new Error("重命名规则必须是对象");
    for (const value of Object.values(renameMap)) if (safePath(value) !== value || value.includes("/")) throw new Error("新文件名必须是安全的单一名称");
    const candidates = new Map(), copies = new Map();
    const identity = source => JSON.stringify(source.provenance_parts);
    let sequence = 0;
    const profileKey = profile => JSON.stringify(canonical(Object.fromEntries(Object.entries(profile).filter(([key]) => key !== "id"))));
    for (const selectedSource of configuredSources) {
      check();
      const directory = targetDirectories[selectedSource.id];
      if (!directory) continue;
      const profile = profiles.get(selectedSource.parser_id), domain = profile.engine === "kernel" ? "kernel" : "user";
      const otherTargets = explicitConfig ? [] : configuredSources.filter(other => other.id !== selectedSource.id && targetDirectories[other.id] && profiles.get(other.parser_id).engine !== profile.engine &&
        JSON.stringify(targetDirectories[other.id].provenance_parts) !== JSON.stringify(directory.provenance_parts) && relativeDirectoryDepth(targetDirectories[other.id], directory) !== null).map(other => targetDirectories[other.id]);
      const entries = files.filter(file => relativeDirectoryDepth(file, directory) !== null && !otherTargets.some(other => relativeDirectoryDepth(file, other) !== null));
      for (const file of entries) {
        check();
        if (file.kind) continue;
        if (!selectedSource.recursive && relativeDirectoryDepth(file, directory) > 0) continue;
        const name = basename(file.path);
        const source = {path: file.relative_path, relative_path: file.relative_path,
          provenance_parts: file.provenance_parts, origin_path: file.origin_path, archive_path: file.archive_path,
          member_path: file.member_path, device_source: "target:" + directory.path, log_domain: domain,
          configured_source_ids: [selectedSource.id], parser_profile: profile, analysis_config: analysisConfig,
          source_order: selectedSource.order, configured_source_rank: analysisConfig.sources.findIndex(item => item.id === selectedSource.id),
          original_name: name, bytes: file.bytes};
        const isSelected = selectedSource.file_patterns.some(pattern => matches(name, pattern, false)) && (!selectedSource.file_regex || global.ProductLogConfig.compileRegex("^(?:" + selectedSource.file_regex + ")$").test(name));
        const copy = (!explicitConfig || isSelected) && domain !== "kernel" && copyPatterns.length && selected(name, copyPatterns);
        if (isSelected || copy) {
          const existing = candidates.get(identity(source)) || copies.get(identity(source));
          if (existing) {
            if (profileKey(existing.parser_profile) !== profileKey(profile)) throw new Error("同一文件绑定了不兼容的解析器: " + source.relative_path);
            source.configured_source_ids = [...new Set([...existing.configured_source_ids, selectedSource.id])];
            source.source_order = existing.source_order; source.configured_source_rank = existing.configured_source_rank;
            source.device_source = existing.device_source;
          }
          if (isSelected) candidates.set(identity(source), source);
          if (copy) copies.set(identity(source), source);
        }
      }
    }
    const applied = new Set();
    for (const renameKey of Object.keys(renameMap)) {
      if (renameKey.includes("/")) {
        const matching = new Set([...candidates, ...copies].filter(([, source]) => source.relative_path === renameKey).map(([key]) => key));
        if (matching.size > 1) throw new Error("重命名来源路径不唯一: " + renameKey);
      }
    }
    for (const [key, source] of candidates) if (Object.hasOwn(renameMap, source.original_name) || Object.hasOwn(renameMap, source.relative_path)) copies.set(key, source);
    for (const [key, source] of copies) {
      const renameKey = Object.hasOwn(renameMap, source.relative_path) ? source.relative_path : source.original_name;
      const name = Object.hasOwn(renameMap, renameKey) ? renameMap[renameKey] : source.original_name;
      if (Object.hasOwn(renameMap, renameKey)) applied.add(renameKey);
      // Retain provenance and original_name: display copies never change read order.
      candidates.set(key, {...source, path: "selected/" + (++sequence) + "/" + name});
    }
    const unmatched = Object.keys(renameMap).filter(key => !applied.has(key));
    if (unmatched.length) throw new Error("重命名规则未匹配文件: " + unmatched.join(", "));
    const ordered = [...candidates.values()];
    for (const source of ordered) {
      if (source.source_order.type !== "numeric_rotation") continue;
      const match = global.ProductLogConfig.compileRegex("^(?:" + source.source_order.pattern + ")$").exec(source.original_name);
      if (!match || match[1] !== undefined && !/^\d+$/.test(match[1])) throw new Error("numeric_rotation requires a numeric capture for " + source.original_name);
      source.rotation_number = match[1];
    }
    ordered.sort((a, b) => {
      if (a.configured_source_rank !== b.configured_source_rank) return a.configured_source_rank - b.configured_source_rank;
      const order = a.source_order;
      if (order.type === "numeric_rotation") {
        const aa = a.rotation_number, bb = b.rotation_number;
        if ((aa === undefined) !== (bb === undefined)) return aa === undefined ? 1 : -1;
        if (aa !== undefined && BigInt(aa) !== BigInt(bb)) return BigInt(aa) < BigInt(bb) ? -1 : 1;
      } else {
        if (order.type === "current_last" && (a.original_name === order.current) !== (b.original_name === order.current)) return a.original_name === order.current ? 1 : -1;
        const nameOrder = compare(a.original_name, b.original_name);
        if (nameOrder) return nameOrder;
      }
      return compare(a.relative_path, b.relative_path) || compare(identity(a), identity(b));
    });
    emit("collecting", ordered.length, "解码选中的日志文件");
    for (let rank = 0; rank < ordered.length; rank++) {
      check();
      const source = {...ordered[rank], source_rank: rank, source_id: "source-" + rank, text: decodeText(ordered[rank].bytes)};
      sources.push(source);
      if (rank % 32 === 31) await tick();
    }
    return {sources, archives, targets, source_targets: sourceTargets, analysis_config: analysisConfig, collection_mode: mode,
      counts: {files: sources.length, archives: archives.length, expanded_files: budget.files, expanded_bytes: budget.bytes},
      input_type: topArchive ? "archive" : "directory", input_path: topArchive || "浏览器选择的文件夹"};
  }
  global.ProductLogOfflineArchives = Object.freeze({prepare, decodeText, archiveKind});
})(globalThis);
