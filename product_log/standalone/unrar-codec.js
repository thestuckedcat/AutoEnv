/* Decode RAR4/5, including solid files, with the pinned original UnRAR WASM.

   UnRAR source code may be used in any software to handle
   RAR archives without limitations free of charge, but cannot be
   used to develop RAR (WinRAR) compatible archiver and to
   re-create RAR compression algorithm, which is proprietary.
   Distribution of modified UnRAR source code in separate form
   or as a part of other software is permitted, provided that
   full text of this paragraph, starting from "UnRAR source code"
   words, is included in license, or in documentation if license
   is not available, and in source code comments of resulting package.
*/
(function (global) {
  "use strict";
  let ready;
  async function readRar(bytes, validate, check, beforeExtract = null) {
    if (!ready) {
      const binary = Uint8Array.from(atob(global.OfflineUnrarBinary), char => char.charCodeAt(0));
      ready = global.OfflineUnrarFactory({wasmBinary: binary,
        locateFile: () => "embedded-unrar.wasm", print: () => {}, printErr: () => {}});
    }
    const mod = await ready;
    check();
    const data = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    const extractor = new global.OfflineUnrarExtractor(mod, data, "");
    mod.extractor = extractor;
    const metadata = [];
    const state = status => {
      if (status.errCode !== 0) throw new Error("RAR 读取或解压失败 (" + status.errCode + ")");
    };
    try {
      const header = extractor.openArc(true);
      if (header.flags.volume) throw new Error("不支持分卷 RAR");
      if (header.flags.headerEncrypted) throw new Error("不支持加密 RAR");
      for (;;) {
        check();
        // The native header retains hostOS/fileAttr, which upstream's high-level
        // file list omits. Validate these before any extraction creates bytes.
        const raw = extractor._archive.getFileHeader();
        if (raw.state.errCode === 10) break;
        state(raw.state);
        if (raw.flags & 3) throw new Error("不支持分卷 RAR");
        if (raw.flags & 4) throw new Error("不支持加密 RAR: " + raw.name);
        const directory = !!(raw.flags & 0x20);
        const type = raw.hostOS === 3 ? raw.fileAttr & 0xf000 : directory ? 0o040000 : 0o100000;
        if (type === 0o120000) throw new Error("压缩包链接不受支持: " + raw.name);
        if (raw.hostOS === 3 && ![0, 0o040000, 0o100000].includes(type)) throw new Error("压缩包特殊文件不受支持: " + raw.name);
        if (!Number.isSafeInteger(raw.unpSize) || raw.unpSize < 0) throw new Error("压缩包成员大小无效");
        const entry = {name: raw.name, size: raw.unpSize, directory, crc: raw.crc >>> 0};
        validate(entry);
        metadata.push(entry);
        state(extractor._archive.readFile(true));
      }
      extractor.closeArc();
      if (beforeExtract) await beforeExtract();
      const result = [];
      for (const item of extractor.extract().files) {
        check();
        const entry = metadata[result.length];
        if (!entry || item.fileHeader.name !== entry.name || item.fileHeader.unpSize !== entry.size) {
          throw new Error("RAR 解压内容与头部不一致");
        }
        const payload = entry.directory ? null : item.extraction;
        if (!entry.directory && (!(payload instanceof Uint8Array) || payload.length !== entry.size)) {
          throw new Error("RAR 成员数据截断: " + entry.name);
        }
        result.push({...entry, bytes: payload});
        if (result.length % 32 === 0) await new Promise(resolve => setTimeout(resolve, 0));
      }
      if (result.length !== metadata.length) throw new Error("RAR 成员数据截断");
      return result;
    } catch (error) {
      if (error.reason === "ERAR_MISSING_PASSWORD" || error.reason === "ERAR_BAD_PASSWORD") {
        throw new Error("不支持加密 RAR");
      }
      if (error.reason) throw new Error("RAR 解压失败: " + error.message);
      throw error;
    } finally {
      // Upstream's generators close native objects only after complete iteration;
      // validation/cancellation can leave one open, so release it explicitly.
      if (extractor._archive) extractor.closeArc();
      mod.extractor = null;
    }
  }
  global.OfflineCompression.readRar = readRar;
})(globalThis);
