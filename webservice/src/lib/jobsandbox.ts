/**
 * Isolation helpers for gtopt jobs started by the webservice.
 *
 *  - `jobEnvironment()`   — the minimal environment a gtopt child gets
 *    (fixed PATH, HOME/TMPDIR inside the job directory,
 *    `GTOPT_CONFIG=/dev/null`, plus an allowlist of solver variables).
 *  - `jobForcedOptions()` — `--set` overrides every job runs with
 *    (output and log directories inside the job, no LP debug dumps).
 *  - `checkCaseJson()`    — refuses uploaded case JSON whose path-like
 *    options are absolute or climb out of the job directory.
 *  - `checkCaseDir()`     — runs `checkCaseJson()` on every `.json` file
 *    of an extracted case.
 *  - `redactJobTokens()`  — shortens job tokens (UUIDs) in log lines.
 *  - `publicJobError()`   — the job error reported through the API
 *    (exit code only; the solver output stays in the per-job logs).
 *
 * Pure helpers (no Next.js imports) so jest can cover them directly.
 */

import { promises as fs } from "fs";
import path from "path";

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------

/** PATH given to gtopt (it only spawns gzip/zstd for optional dumps). */
export const JOB_PATH = "/usr/local/bin:/usr/bin:/bin";

/**
 * Variables copied from the webservice environment when set: locale,
 * solver plugin selection and the licence / home variables the commercial
 * solver plugins read.  Extend with `GTOPT_JOB_ENV_PASSTHROUGH`
 * (comma-separated names).
 */
export const JOB_ENV_PASSTHROUGH: readonly string[] = [
  "LANG",
  "LC_ALL",
  "TZ",
  "LD_LIBRARY_PATH",
  "GTOPT_SOLVER",
  "GTOPT_PLUGIN_DIR",
  "GTOPT_THREADS",
  "OMP_NUM_THREADS",
  "CPLEX_STUDIO_DIR",
  "CPLEX_HOME",
  "GRB_LICENSE_FILE",
  "GUROBI_HOME",
  "MINDOPT_HOME",
  "SCIPOPTDIR",
];

/** Environment variable naming extra pass-through variables. */
export const JOB_ENV_PASSTHROUGH_ENV = "GTOPT_JOB_ENV_PASSTHROUGH";

const ENV_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/**
 * Build the environment of a gtopt job.  Secrets of the webservice (API
 * tokens, cloud credentials, ...) are never inherited.
 */
export function jobEnvironment(
  jobDir: string,
  env: Record<string, string | undefined> = process.env
): Record<string, string> {
  const out: Record<string, string> = {
    PATH: JOB_PATH,
    HOME: jobDir,
    TMPDIR: path.join(jobDir, "tmp"),
    // No user / project config file may change how the job runs.
    GTOPT_CONFIG: "/dev/null",
  };
  const extra = (env[JOB_ENV_PASSTHROUGH_ENV] ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => ENV_NAME_RE.test(s));
  for (const name of [...JOB_ENV_PASSTHROUGH, ...extra]) {
    if (name in out) continue;
    const value = env[name];
    if (value !== undefined && value !== "") out[name] = value;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Forced options
// ---------------------------------------------------------------------------

/** `--set` arguments applied after the case options (they win). */
export function jobForcedOptions(outputDir: string, logDir: string): string[] {
  return [
    "--set",
    `output_directory=${outputDir}`,
    "--set",
    `log_directory=${logDir}`,
    "--set",
    "lp_debug=false",
  ];
}

// ---------------------------------------------------------------------------
// Case JSON validation
// ---------------------------------------------------------------------------

/** Keys whose string values name files or directories. */
const PATH_KEY_RE = /(^|_)(directory|dir|file|path|files)$/;

/** Largest JSON file `checkCaseDir()` parses (bigger files are refused). */
export const MAX_CASE_JSON_BYTES = 512 * 1024 * 1024;

export type CaseCheck = { ok: true } | { ok: false; error: string };

function unsafePath(value: string): string | null {
  const v = value.trim();
  if (v.includes("\0")) return "contains a NUL character";
  if (v.startsWith("/") || v.startsWith("\\") || /^[A-Za-z]:/.test(v)) {
    return "is an absolute path";
  }
  if (v.startsWith("~")) return "starts with '~'";
  if (v.split(/[\\/]/).some((seg) => seg === "..")) return "contains '..'";
  return null;
}

/**
 * Check an uploaded case document: every string under a path-like key
 * (`*_directory`, `*_dir`, `*_file`, `*_path`) and every whitespace-free
 * string anywhere must stay relative to the job directory.
 */
export function checkCaseJson(doc: unknown, where = "$"): CaseCheck {
  const stack: Array<[unknown, string, string]> = [[doc, where, ""]];
  while (stack.length > 0) {
    const [node, at, key] = stack.pop()!;
    if (typeof node === "string") {
      const pathLike = PATH_KEY_RE.test(key) || !/\s/.test(node);
      const why = pathLike ? unsafePath(node) : null;
      if (why !== null) {
        return { ok: false, error: `${at} ${why}; case paths must stay inside the case` };
      }
    } else if (Array.isArray(node)) {
      node.forEach((v, i) => stack.push([v, `${at}[${i}]`, key]));
    } else if (node !== null && typeof node === "object") {
      for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
        stack.push([v, `${at}.${k}`, k]);
      }
    }
  }
  return { ok: true };
}

async function* jsonFiles(dir: string): AsyncGenerator<string> {
  for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* jsonFiles(full);
    else if (entry.isFile() && entry.name.toLowerCase().endsWith(".json")) yield full;
  }
}

/** Run {@link checkCaseJson} on every `.json` file below `inputDir`. */
export async function checkCaseDir(inputDir: string): Promise<CaseCheck> {
  for await (const file of jsonFiles(inputDir)) {
    const rel = path.relative(inputDir, file);
    const stat = await fs.stat(file);
    if (stat.size > MAX_CASE_JSON_BYTES) {
      return { ok: false, error: `${rel} is larger than ${MAX_CASE_JSON_BYTES} bytes` };
    }
    let doc: unknown;
    try {
      doc = JSON.parse(await fs.readFile(file, "utf-8"));
    } catch {
      continue; // not JSON gtopt would read either; gtopt reports it
    }
    const res = checkCaseJson(doc, rel);
    if (!res.ok) return res;
  }
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Logs and errors
// ---------------------------------------------------------------------------

const UUID_G_RE =
  /\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;

/** Replace every job token (UUID) by its first 8 hex digits + `…`. */
export function redactJobTokens(line: string): string {
  return line.replace(UUID_G_RE, "$1…");
}

/** Error text stored in job.json / returned by the status API. */
export function publicJobError(code: number | null, signal?: string | null): string {
  const how = signal ? `was terminated by ${signal}` : `exited with code ${code}`;
  return `gtopt ${how}; see the job logs (GET /api/jobs/<token>/logs)`;
}
