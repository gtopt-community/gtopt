/**
 * Unit tests for src/lib/unzip.ts — safe ZIP extraction (zip-slip, symlink
 * and size-limit rejection).
 */

import { promises as fs } from "fs";
import * as os from "os";
import * as path from "path";
import * as zlib from "zlib";
import { checkEntryName, safeExtractZip } from "../unzip";

interface RawEntry {
  name: string;
  data?: string;
  /** Unix mode (e.g. 0o120777 for a symlink); default regular file. */
  mode?: number;
}

/** Build a minimal STORE-method zip archive by hand. */
function buildZip(entries: RawEntry[]): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const e of entries) {
    const name = Buffer.from(e.name, "utf8");
    const data = Buffer.from(e.data ?? "", "utf8");
    const crc = zlib.crc32(data);
    const mode = e.mode ?? (e.name.endsWith("/") ? 0o040755 : 0o100644);

    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0);
    lh.writeUInt16LE(20, 4); // version needed
    lh.writeUInt16LE(0x0800, 6); // UTF-8 names
    lh.writeUInt16LE(0, 8); // store
    lh.writeUInt32LE(crc, 14);
    lh.writeUInt32LE(data.length, 18);
    lh.writeUInt32LE(data.length, 22);
    lh.writeUInt16LE(name.length, 26);
    locals.push(lh, name, data);

    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0);
    ch.writeUInt16LE((3 << 8) | 20, 4); // made by Unix
    ch.writeUInt16LE(20, 6);
    ch.writeUInt16LE(0x0800, 8);
    ch.writeUInt16LE(0, 10);
    ch.writeUInt32LE(crc, 16);
    ch.writeUInt32LE(data.length, 20);
    ch.writeUInt32LE(data.length, 24);
    ch.writeUInt16LE(name.length, 28);
    ch.writeUInt32LE((mode << 16) >>> 0, 38);
    ch.writeUInt32LE(offset, 42);
    centrals.push(ch, name);

    offset += lh.length + name.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cd.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, eocd]);
}

describe("safeExtractZip", () => {
  let tmp: string;
  let dest: string;
  let n = 0;

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "gtopt-unzip-"));
    dest = path.join(tmp, "input");
    await fs.mkdir(dest);
  });

  afterEach(async () => {
    await fs.rm(tmp, { recursive: true, force: true });
  });

  async function writeZip(entries: RawEntry[]): Promise<string> {
    const p = path.join(tmp, `t${n++}.zip`);
    await fs.writeFile(p, buildZip(entries));
    return p;
  }

  it("extracts regular files and directories", async () => {
    const zip = await writeZip([
      { name: "system_c0.json", data: "{}" },
      { name: "system_c0/" },
      { name: "system_c0/demand.csv", data: "a,b\n1,2\n" },
    ]);
    await safeExtractZip(zip, dest);
    expect(await fs.readFile(path.join(dest, "system_c0.json"), "utf8")).toBe("{}");
    expect(await fs.readFile(path.join(dest, "system_c0", "demand.csv"), "utf8")).toBe(
      "a,b\n1,2\n"
    );
  });

  it("rejects '..' entries (zip slip)", async () => {
    const zip = await writeZip([{ name: "../evil.txt", data: "x" }]);
    await expect(safeExtractZip(zip, dest)).rejects.toThrow();
    await expect(fs.access(path.join(tmp, "evil.txt"))).rejects.toThrow();
  });

  it("rejects absolute entries", async () => {
    const zip = await writeZip([{ name: "/tmp/evil.txt", data: "x" }]);
    await expect(safeExtractZip(zip, dest)).rejects.toThrow();
  });

  it("rejects symlink entries", async () => {
    const zip = await writeZip([
      { name: "link", data: tmp, mode: 0o120777 },
      { name: "link/evil.txt", data: "x" },
    ]);
    await expect(safeExtractZip(zip, dest)).rejects.toThrow(/symlink/);
    await expect(fs.lstat(path.join(dest, "link"))).rejects.toThrow();
    await expect(fs.access(path.join(tmp, "evil.txt"))).rejects.toThrow();
  });

  it("extracts entries with permission bits only (Python writestr)", async () => {
    // zipfile.ZipFile.writestr() stores 0o600 with no file-type bits.
    const zip = await writeZip([
      { name: "ieee_9b.json", data: "{}", mode: 0o600 },
      { name: "data/", mode: 0o700 },
      { name: "data/demand.csv", data: "a\n", mode: 0o600 },
    ]);
    await safeExtractZip(zip, dest);
    expect(await fs.readFile(path.join(dest, "ieee_9b.json"), "utf8")).toBe("{}");
    expect(await fs.readFile(path.join(dest, "data", "demand.csv"), "utf8")).toBe("a\n");
  });

  it("enforces the uncompressed size limit", async () => {
    const zip = await writeZip([{ name: "big.txt", data: "x".repeat(1000) }]);
    await expect(
      safeExtractZip(zip, dest, { maxEntries: 10, maxTotalBytes: 100 })
    ).rejects.toThrow(/maximum uncompressed size/);
  });

  it("enforces the entry-count limit", async () => {
    const zip = await writeZip([
      { name: "a.txt", data: "a" },
      { name: "b.txt", data: "b" },
    ]);
    await expect(
      safeExtractZip(zip, dest, { maxEntries: 1, maxTotalBytes: 1000 })
    ).rejects.toThrow(/entries/);
  });
});

describe("checkEntryName", () => {
  it.each(["../a", "a/../../b", "/etc/passwd", "C:/x", "a\\b", ""])(
    "rejects %j",
    (name) => {
      expect(() => checkEntryName(name)).toThrow();
    }
  );
  it.each(["a", "a/b.json", "dir/"])("accepts %j", (name) => {
    expect(() => checkEntryName(name)).not.toThrow();
  });
});
