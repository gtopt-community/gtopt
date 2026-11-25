/**
 * Safe ZIP extraction for uploaded gtopt cases.
 *
 * Replaces `extract-zip` (GHSA-jmr9-qjv8-65gv / GHSA-7pqw-9j4j-h8q3: no
 * fixed release) with a small yauzl-based extractor that:
 *
 *  - rejects absolute paths, drive letters, backslashes and `..` segments
 *    (yauzl's own `validateFileName` plus our own checks);
 *  - rejects symlinks and any entry that is not a regular file/directory;
 *  - confines every output path to the destination directory;
 *  - caps the number of entries and total uncompressed size (zip bombs).
 */

import { createWriteStream, promises as fs } from "fs";
import path from "path";
import { pipeline } from "stream/promises";
import { Transform } from "stream";
import yauzl from "yauzl";

export interface ExtractLimits {
  /** Maximum number of entries in the archive. */
  maxEntries: number;
  /** Maximum total uncompressed bytes written. */
  maxTotalBytes: number;
}

export const DEFAULT_EXTRACT_LIMITS: ExtractLimits = {
  maxEntries: 100_000,
  maxTotalBytes: 20 * 1024 * 1024 * 1024, // 20 GiB
};

const S_IFMT = 0o170000;
const S_IFREG = 0o100000;
const S_IFDIR = 0o040000;

function entryKind(entry: yauzl.Entry): "file" | "dir" | "other" {
  const isDirName = entry.fileName.endsWith("/");
  // Unix mode lives in the high 16 bits of externalFileAttributes when the
  // archive was created on a Unix host (versionMadeBy >> 8 === 3).
  const madeByUnix = entry.versionMadeBy >> 8 === 3;
  const mode = (entry.externalFileAttributes >>> 16) & 0xffff;
  // A file-type field of 0 carries no type: Python's ZipFile.writestr()
  // (used by the GUI service) stores only the permission bits (0o600), so
  // such an entry is a plain file or directory, decided by its name.
  if (madeByUnix && (mode & S_IFMT) !== 0) {
    const fmt = mode & S_IFMT;
    if (fmt === S_IFDIR) return "dir";
    if (fmt === S_IFREG) return isDirName ? "dir" : "file";
    return "other"; // symlink, device, fifo, socket…
  }
  return isDirName ? "dir" : "file";
}

/** Throw unless `name` is a safe relative archive path. */
export function checkEntryName(name: string): void {
  if (name.length === 0) throw new Error("empty entry name");
  if (name.includes("\0")) throw new Error(`invalid entry name: ${name}`);
  if (name.includes("\\")) {
    throw new Error(`entry name contains a backslash: ${name}`);
  }
  if (name.startsWith("/") || /^[A-Za-z]:/.test(name)) {
    throw new Error(`absolute entry path: ${name}`);
  }
  for (const seg of name.split("/")) {
    if (seg === "..") throw new Error(`entry path escapes archive: ${name}`);
  }
}

function confined(destDir: string, name: string): string {
  const target = path.resolve(destDir, name);
  const rel = path.relative(destDir, target);
  if (rel === "" || rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new Error(`entry path escapes destination: ${name}`);
  }
  return target;
}

function openZip(zipPath: string): Promise<yauzl.ZipFile> {
  return new Promise((resolve, reject) => {
    yauzl.open(
      zipPath,
      { lazyEntries: true, autoClose: true, strictFileNames: true },
      (err, zip) => (err || !zip ? reject(err ?? new Error("open failed")) : resolve(zip))
    );
  });
}

function openEntry(zip: yauzl.ZipFile, entry: yauzl.Entry): Promise<NodeJS.ReadableStream> {
  return new Promise((resolve, reject) => {
    zip.openReadStream(entry, (err, stream) =>
      err || !stream ? reject(err ?? new Error("read failed")) : resolve(stream)
    );
  });
}

/**
 * Extract `zipPath` into `destDir` (which must already exist).  On any
 * unsafe entry the extraction is aborted with an Error.
 */
export async function safeExtractZip(
  zipPath: string,
  destDir: string,
  limits: ExtractLimits = DEFAULT_EXTRACT_LIMITS
): Promise<void> {
  const root = await fs.realpath(path.resolve(destDir));
  const zip = await openZip(zipPath);
  let entries = 0;
  let totalBytes = 0;

  await new Promise<void>((resolve, reject) => {
    let failed = false;
    const fail = (err: unknown) => {
      if (failed) return;
      failed = true;
      zip.close();
      reject(err instanceof Error ? err : new Error(String(err)));
    };

    zip.on("error", fail);
    zip.on("end", () => {
      if (!failed) resolve();
    });
    zip.on("entry", (entry: yauzl.Entry) => {
      (async () => {
        entries += 1;
        if (entries > limits.maxEntries) {
          throw new Error(`archive has more than ${limits.maxEntries} entries`);
        }
        checkEntryName(entry.fileName);
        const kind = entryKind(entry);
        if (kind === "other") {
          throw new Error(
            `unsupported entry type (symlink or special file): ${entry.fileName}`
          );
        }
        const target = confined(root, entry.fileName);
        if (kind === "dir") {
          await fs.mkdir(target, { recursive: true });
          zip.readEntry();
          return;
        }
        await fs.mkdir(path.dirname(target), { recursive: true });
        // A previously-extracted directory cannot be a symlink (we never
        // create any), but double-check the parent's real path.
        const realParent = await fs.realpath(path.dirname(target));
        confined(root, path.relative(root, path.join(realParent, path.basename(target))));

        const stream = await openEntry(zip, entry);
        const counter = new Transform({
          transform(chunk: Buffer, _enc, cb) {
            totalBytes += chunk.length;
            if (totalBytes > limits.maxTotalBytes) {
              cb(new Error("archive exceeds the maximum uncompressed size"));
              return;
            }
            cb(null, chunk);
          },
        });
        // 'wx' refuses to overwrite (and to follow) an existing path.
        await pipeline(stream, counter, createWriteStream(target, { flags: "wx", mode: 0o644 }));
        zip.readEntry();
      })().catch(fail);
    });
    zip.readEntry();
  });
}
