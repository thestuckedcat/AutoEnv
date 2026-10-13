"""Build a self-contained, offline product-log HTML using only Python stdlib.

This deliberately does not import autoenv or product_log, start a server, or
download anything. All browser code and decompression assets are vendored.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "product_log" / "standalone"


def verified_vendor() -> dict[str, str | bytes]:
    vendor = SOURCE / "vendor"
    manifest = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
    result = {}
    for name, expected in manifest["assets"].items():
        path = vendor / name
        if path.parent != vendor or not path.is_file():
            raise ValueError(f"Missing vendored asset: {name}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Vendored asset checksum differs: {name}")
        result[name] = data if name.endswith(".wasm") else data.decode("utf-8")
    return result


def compression_source() -> str:
    """Return browser-compatible codecs, also used by real Node fixture tests."""
    vendor = verified_vendor()
    pieces = []
    for name in ("fflate-0.8.3.js", "xz-decompress-0.2.3.js"):
        pieces.append("(function(){const module=undefined,exports=undefined,define=undefined;\n"
                      + vendor[name] + "\n}).call(globalThis);\n")
    pieces.append("(function(){const module=undefined,exports=undefined,define=undefined,process=undefined;\n"
                  + vendor["libarchive-wasm-1.2.0.js"]
                  + "\nglobalThis.OfflineLibarchiveFactory=libarchive;}).call(globalThis);\n")
    wasm = base64.b64encode(vendor["libarchive-wasm-1.2.0.wasm"]).decode("ascii")
    pieces.append("globalThis.OfflineLibarchiveBinary=" + json.dumps(wasm) + ";\n")
    unrar_loader = vendor["node-unrar-2.0.2.js"]
    if unrar_loader.count("export default Module;") != 1:
        raise ValueError("Unexpected pinned UnRAR module export")
    # Preserve vendored bytes; adapt the one ESM export for this classic worker bundle.
    pieces.append("(function(){const process=undefined;\n"
                  + unrar_loader.replace("export default Module;", "globalThis.OfflineUnrarFactory=Module;")
                  + "\n})();\n")
    wasm = base64.b64encode(vendor["node-unrar-2.0.2.wasm"]).decode("ascii")
    pieces.append("globalThis.OfflineUnrarBinary=" + json.dumps(wasm) + ";\n")
    pieces.append("(function(){const factories={},modules={};\n"
                  "function require(name){if(modules[name])return modules[name].exports;"
                  "const module={exports:{}};modules[name]=module;"
                  "if(!factories[name])throw new Error('Unknown embedded UnRAR module: '+name);"
                  "factories[name](require,module,module.exports);return module.exports;}\n")
    for name in ("Extractor", "ExtractorData.helper", "ExtractorData"):
        asset = vendor[f"node-unrar-{name}.js"]
        asset = "\n".join(line for line in asset.splitlines() if not line.startswith("//# sourceMappingURL="))
        pieces.append(f"factories['./{name}']=function(require,module,exports){{\n" + asset + "\n};\n")
    pieces.append("globalThis.OfflineUnrarExtractor=require('./ExtractorData').ExtractorData;})();\n")
    lzma = vendor["lzma-d-2.3.2.js"]
    raw_output = "decode($toByteArray(this$static.d.output))"
    write_output = "function $write_0(this$static, buf, off, len) {"
    if lzma.count(raw_output) != 2 or lzma.count(write_output) != 1:
        raise ValueError("Unexpected pinned LZMA decompressor output")
    # Do not let the library's automatic UTF8 conversion alter binary bytes.
    # The same validated ZIP size bounds allocation even when the size is zero.
    lzma = lzma.replace(raw_output, "$toByteArray(this$static.d.output)").replace(
        write_output, write_output + "\nif(this$static.count+len>offlineLzmaLimit)throw new Error('LZMA output exceeds ZIP member size');")
    pieces.append("(function(){const onmessage=undefined;let offlineLzmaLimit=0;\n" + lzma
                  + "\nglobalThis.OfflineLzmaBytes=function(bytes,size){offlineLzmaLimit=size;"
                  "return Uint8Array.from(LZMA.decompress(bytes));};}).call(globalThis);\n")
    pieces.append("""
(function () {
  'use strict';
  // seek-bzip's tiny Buffer surface: byte allocation, copy and hex output.
  class BrowserBuffer extends Uint8Array {
    copy(target, offset = 0, start = 0, end = this.length) {
      const bytes = this.subarray(start, end); target.set(bytes, offset); return bytes.length;
    }
    toString(encoding) {
      if (encoding !== 'hex') throw new Error('Unsupported internal byte encoding');
      return Array.from(this, byte => byte.toString(16).padStart(2, '0')).join('');
    }
  }
  const Buffer = BrowserBuffer;
  const modules = Object.create(null), factories = Object.create(null);
  function require(name) {
    if (name === '../package.json') return {name:'seek-bzip',version:'2.0.0',license:'MIT'};
    if (modules[name]) return modules[name].exports;
    if (!factories[name]) throw new Error('Unknown embedded codec module: ' + name);
    const module = {exports:{}}; modules[name] = module;
    factories[name](require, module, module.exports); return module.exports;
  }
""")
    for name in ("bitreader", "crc32", "stream", "index"):
        pieces.append(f"factories['./{name}'] = function(require,module,exports) {{\n"
                      + vendor[f"seek-bzip-{name}.js"] + "\n};\n")
    pieces.append("""
  const Bunzip = require('./index');
  const crcTable = Uint32Array.from({length:256}, (_, value) => {
    for (let bit=0;bit<8;bit++) value=(value>>>1)^((value&1)?0xedb88320:0);
    return value>>>0;
  });
  function checksum(chunks) {
    let value=0xffffffff;
    for (const chunk of chunks) for (const byte of chunk) value=(value>>>8)^crcTable[(value^byte)&255];
    return (value^0xffffffff)>>>0;
  }
  function header(bytes, start) {
    if (start+10>bytes.length || bytes[start]!==31 || bytes[start+1]!==139 || bytes[start+2]!==8) throw new Error('Invalid GZIP header');
    const flags=bytes[start+3]; if (flags&224) throw new Error('Invalid GZIP flags');
    let pos=start+10;
    if (flags&4) {
      if (pos+2>bytes.length) throw new Error('Truncated GZIP header');
      pos+=2+bytes[pos]+(bytes[pos+1]<<8);
    }
    for (const flag of [8,16]) if (flags&flag) {
      while (pos<bytes.length && bytes[pos]!==0) pos++;
      if (pos>=bytes.length) throw new Error('Truncated GZIP header'); pos++;
    }
    if (flags&2) {
      if (pos+2>bytes.length || (checksum([bytes.subarray(start,pos)])&65535)!==(bytes[pos]|bytes[pos+1]<<8)) throw new Error('Invalid GZIP header CRC');
      pos+=2;
    }
    if (pos+8>bytes.length) throw new Error('Truncated GZIP data');
    return pos;
  }
  function ungzip(bytes) {
    const all=[]; let total=0,cursor=0;
    do {
      const start=header(bytes,cursor),member=[]; let size=0;
      const stream=new globalThis.fflate.Inflate(chunk=>{
        size+=chunk.length; total+=chunk.length;
        if (total>2*1024**3) throw new Error('GZIP expanded data exceeds the offline budget');
        if (chunk.length) {member.push(chunk);all.push(chunk);}
      });
      stream.push(bytes.subarray(start),true);
      // fflate 0.8.3 retains unused bytes in p and the residual bit offset in
      // s.p. The vendored version and these boundary semantics are pinned and
      // covered by real-codec tests. Decode only DEFLATE; validate GZIP here so
      // valid zero padding never gets mistaken for another member's header.
      if (!stream.s.f || stream.s.l) throw new Error('Incomplete GZIP DEFLATE stream');
      const trailer=bytes.length-stream.p.length+(stream.s.p?1:0);
      if (trailer<start || trailer+8>bytes.length) throw new Error('Truncated GZIP trailer');
      const view=new DataView(bytes.buffer,bytes.byteOffset+trailer,8);
      if (view.getUint32(0,true)!==checksum(member) || view.getUint32(4,true)!==(size>>>0)) throw new Error('Invalid GZIP CRC or size');
      cursor=trailer+8;
      while (cursor<bytes.length && bytes[cursor]===0) cursor++;
    } while (cursor<bytes.length);
    const result=new Uint8Array(total); let offset=0;
    for (const chunk of all) {result.set(chunk,offset);offset+=chunk.length;}
    return result;
  }
  globalThis.OfflineCompression = {
    ungzip,
    unlzma: (bytes, size, flags) => {
      if (bytes.length < 14 || (bytes[2] | bytes[3] << 8) !== 5) throw new Error('Invalid ZIP LZMA properties');
      if (size===0 && !(flags&2)) {
        // Without an EOS marker, a known zero output size finishes immediately.
        // Still validate properties and the complete five-byte range header.
        const dictionary=new DataView(bytes.buffer,bytes.byteOffset+5,4).getUint32(0,true);
        if(bytes[4]>=225||dictionary>99999999||bytes[9]!==0) throw new Error('Invalid ZIP LZMA properties');
        return new Uint8Array(0);
      }
      // ZIP method 14 has a four-byte SDK/properties header. LZMA-alone has
      // five property bytes followed by the known eight-byte unpacked size.
      const frame=new Uint8Array(bytes.length+4),data=new DataView(frame.buffer);
      frame.set(bytes.subarray(4,9));
      data.setUint32(5,size>>>0,true);data.setUint32(9,Math.floor(size/4294967296),true);
      frame.set(bytes.subarray(9),13);
      return globalThis.OfflineLzmaBytes(frame,size);
    },
    inflateRaw: bytes => globalThis.fflate.inflateSync(bytes),
    bunzip: bytes => new Uint8Array(Bunzip.decode(bytes, undefined, true)),
    unxz: async bytes => new Uint8Array(await new Response(
      new globalThis['xz-decompress'].XzReadableStream(new Blob([bytes]).stream())
    ).arrayBuffer())
  };
})();
""")
    pieces.append((SOURCE / "libarchive-codec.js").read_text(encoding="utf-8"))
    pieces.append((SOURCE / "unrar-codec.js").read_text(encoding="utf-8"))
    return "\n".join(pieces)


def worker_source() -> str:
    return "\n".join([compression_source(), *(SOURCE.joinpath(name).read_text(encoding="utf-8")
                                             for name in ("archive.js", "parser.js", "worker.js"))])


def build_page(output: str | Path) -> dict:
    template = (SOURCE / "index.html").read_text(encoding="utf-8")
    stylesheet = (SOURCE / "styles.css").read_text(encoding="utf-8")
    application = (SOURCE / "app.js").read_text(encoding="utf-8")
    worker = base64.b64encode(worker_source().encode("utf-8")).decode("ascii")
    replacements = {
        "<!--OFFLINE_STYLE-->": "<style>" + stylesheet.replace("</style", "<\\/style") + "</style>",
        "<!--OFFLINE_WORKER-->": '<script id="offline-worker" type="application/octet-stream">' + worker + "</script>",
        "<!--OFFLINE_APP-->": "<script>" + application.replace("</script", "<\\/script") + "</script>",
    }
    for placeholder, contents in replacements.items():
        if template.count(placeholder) != 1:
            raise ValueError(f"Expected exactly one template marker: {placeholder}")
        template = template.replace(placeholder, contents)
    notice = (SOURCE / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    template = template.replace("</main>", '<details class="panel licenses"><summary>第三方许可</summary><pre>'
                                + html.escape(notice) + "</pre></details></main>", 1)
    payload = template.encode("utf-8")
    output = Path(output).resolve()
    if output.is_relative_to(SOURCE.resolve()):
        raise ValueError("Output must be outside the standalone source directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(output)
    return {"output": str(output), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "product-log-offline.html")
    args = parser.parse_args(argv)
    try:
        result = build_page(args.output)
    except (OSError, ValueError) as error:
        parser.exit(2, f"Build failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
