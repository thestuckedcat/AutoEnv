/* The pinned libarchive WASM validates RAR5 metadata entirely in worker memory. */
(function (global) {
  "use strict";
  let ready;
  function module() {
    if (!ready) {
      const binary = Uint8Array.from(atob(global.OfflineLibarchiveBinary), char => char.charCodeAt(0));
      ready = global.OfflineLibarchiveFactory({wasmBinary: binary,
        // All decoder bytes are embedded; the Emscripten URL loader is never used.
        locateFile: () => "embedded-libarchive.wasm",
        print: () => {}, printErr: () => {}});
    }
    return ready;
  }
  async function readArchive(bytes, validate, check) {
    const mod = await module();
    check();
    const string = name => mod.cwrap(name, "string", ["number"]);
    const pathname = string("archive_entry_pathname_utf8");
    const symlink = string("archive_entry_symlink_utf8"), hardlink = string("archive_entry_hardlink_utf8");
    const error = string("archive_error_string");
    let input = 0, buffer = 0, archive = 0;
    const fail = label => new Error(label + (archive && error(archive) ? ": " + error(archive) : ""));
    const call = (fn, ...args) => { if (fn(...args) !== 0) throw fail("压缩包读取失败"); };
    const result = [];
    try {
      input = mod._malloc(Math.max(1, bytes.length));
      if (!input) throw new Error("压缩包内存不足");
      mod.HEAPU8.set(bytes, input);
      archive = mod._archive_read_new();
      if (!archive) throw new Error("压缩包内存不足");
      call(mod._archive_read_support_filter_all, archive);
      call(mod._archive_read_support_format_all, archive);
      call(mod._archive_read_open_memory, archive, input, bytes.length);
      const chunkSize = 64 * 1024;
      buffer = mod._malloc(chunkSize);
      if (!buffer) throw new Error("压缩包内存不足");
      for (;;) {
        check();
        const pointer = mod._archive_read_next_entry(archive);
        if (!pointer) {
          // Upstream's convenience wrapper returns null for both EOF and errors.
          // Reject a recorded error rather than silently returning partial logs.
          if (error(archive)) throw fail("压缩包头部读取失败");
          break;
        }
        const entry = {name: pathname(pointer), type: mod._archive_entry_filetype(pointer),
          size: mod._archive_entry_size(pointer), encrypted: !!mod._archive_entry_is_encrypted(pointer),
          symlink: symlink(pointer), hardlink: hardlink(pointer)};
        entry.directory = entry.type === 0o040000;
        if (entry.encrypted) throw new Error("不支持加密压缩包: " + entry.name);
        if (entry.symlink || entry.hardlink) throw new Error("压缩包链接不受支持: " + entry.name);
        if (![0o040000, 0o100000].includes(entry.type)) throw new Error("压缩包特殊文件不受支持: " + entry.name);
        if (!Number.isSafeInteger(entry.size) || entry.size < 0) throw new Error("压缩包成员大小无效");
        if (!validate(entry) || entry.directory) {
          call(mod._archive_read_data_skip, archive);
          result.push({...entry, bytes: null});
          continue;
        }
        const chunks = [];
        let total = 0;
        for (;;) {
          check();
          const length = mod._archive_read_data(archive, buffer, chunkSize);
          if (length < 0) throw fail("压缩包解压失败: " + entry.name);
          if (!length) break;
          total += length;
          if (total > entry.size) throw new Error("压缩包成员大小与头部不一致: " + entry.name);
          chunks.push(mod.HEAPU8.slice(buffer, buffer + length));
        }
        if (total !== entry.size) throw new Error("压缩包成员数据截断: " + entry.name);
        const payload = new Uint8Array(total);
        let offset = 0;
        for (const chunk of chunks) { payload.set(chunk, offset); offset += chunk.length; }
        result.push({...entry, bytes: payload});
        if (result.length % 32 === 0) await new Promise(resolve => setTimeout(resolve, 0));
      }
      if (mod._archive_read_has_encrypted_entries(archive) > 0) throw new Error("不支持加密压缩包");
      return result;
    } finally {
      if (archive) mod._archive_read_free(archive);
      if (buffer) mod._free(buffer);
      if (input) mod._free(input);
    }
  }
  global.OfflineCompression.readArchive = readArchive;
})(globalThis);
