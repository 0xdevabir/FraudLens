// Build favicon.ico + apple-icon.png from app/icon.svg (the FraudLens mark).
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const svgPath = join(root, "app/icon.svg");
const dir = mkdtempSync(join(tmpdir(), "fl-favicon-"));

function png(size, out) {
  execFileSync("rsvg-convert", ["-w", String(size), "-h", String(size), "-o", out, svgPath]);
  return readFileSync(out);
}

/** Pack PNG buffers into a .ico (PNG-compressed images — fine for modern browsers). */
function toIco(pngs, sizes) {
  const count = pngs.length;
  const headerSize = 6 + count * 16;
  let offset = headerSize;
  const entries = pngs.map((buf, i) => {
    const size = sizes[i];
    const entry = { size, bytes: buf.length, offset };
    offset += buf.length;
    return entry;
  });
  const out = Buffer.alloc(offset);
  out.writeUInt16LE(0, 0);
  out.writeUInt16LE(1, 2);
  out.writeUInt16LE(count, 4);
  entries.forEach((entry, i) => {
    const at = 6 + i * 16;
    out[at] = entry.size >= 256 ? 0 : entry.size;
    out[at + 1] = entry.size >= 256 ? 0 : entry.size;
    out[at + 2] = 0;
    out[at + 3] = 0;
    out.writeUInt16LE(1, at + 4);
    out.writeUInt16LE(32, at + 6);
    out.writeUInt32LE(entry.bytes, at + 8);
    out.writeUInt32LE(entry.offset, at + 12);
    pngs[i].copy(out, entry.offset);
  });
  return out;
}

try {
  const sizes = [16, 32, 48];
  const buffers = sizes.map((size) => png(size, join(dir, `${size}.png`)));
  writeFileSync(join(root, "app/favicon.ico"), toIco(buffers, sizes));
  writeFileSync(join(root, "app/apple-icon.png"), png(180, join(dir, "apple.png")));
  console.log("wrote app/favicon.ico and app/apple-icon.png from icon.svg");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
