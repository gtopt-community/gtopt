import { promises as fs } from "fs";
import path from "path";
import { v4 as uuidv4 } from "uuid";
import { spawn, execFile, ChildProcess } from "child_process";
import { createLogger } from "./logger";
import { listDirRecursive } from "./files";
import { isValidJobToken, resolveInside, validateSystemFile } from "./security";
import { jobEnvironment, jobForcedOptions, publicJobError } from "./jobsandbox";

const log = createLogger("jobs");

export interface JobInfo {
  token: string;
  status: "pending" | "running" | "completed" | "failed";
  createdAt: string;
  completedAt?: string;
  systemFile: string;
  error?: string;
}

// In-memory job store (for production, use a database)
const jobs = new Map<string, JobInfo>();

// In-memory map of running child processes keyed by job token
const runningProcesses = new Map<string, ChildProcess>();

// Lazy data-dir / gtopt-binary resolution.  Calling `process.cwd()` at
// module scope makes Turbopack's NFT walk pull the entire project into
// the deployment bundle (warned as "Encountered unexpected file in
// NFT list").  Lazy initialisation defers the cwd read to first-call
// time — well past the build-time trace — and the cached value still
// behaves like a module constant for the rest of the process lifetime.
let _dataDir: string | undefined;
function dataDir(): string {
  if (_dataDir === undefined) {
    _dataDir =
      process.env.GTOPT_DATA_DIR || path.join(process.cwd(), "data");
  }
  return _dataDir;
}

// `gtoptBinPath()` returns the explicit-config path (`$GTOPT_BIN`) or
// empty string when not set.  The full lookup chain — including the
// in-project `../build/gtopt` fallback that walks out of the webservice
// tree — lives entirely inside `resolveGtoptBinary()` below, so the
// `..` traversal never appears at module scope and never triggers
// Turbopack's "Encountered unexpected file in NFT list" warning.
function gtoptBinPath(): string {
  return process.env.GTOPT_BIN ?? "";
}

export function getDataDir(): string {
  return dataDir();
}

export function getJobDir(token: string): string {
  return path.join(dataDir(), "jobs", token);
}

export function getJobInputDir(token: string): string {
  return path.join(getJobDir(token), "input");
}

export function getJobOutputDir(token: string): string {
  return path.join(getJobDir(token), "output");
}

export async function ensureDataDir(): Promise<void> {
  await fs.mkdir(path.join(dataDir(), "jobs"), { recursive: true });
}

export async function createJob(systemFile: string): Promise<JobInfo> {
  if (!systemFile || systemFile.trim() === "") {
    throw new Error("systemFile parameter must not be empty");
  }
  const check = validateSystemFile(systemFile);
  if (!check.ok) {
    throw new Error(`Invalid systemFile: ${check.error}`);
  }
  systemFile = check.value;
  const token = uuidv4();
  const job: JobInfo = {
    token,
    status: "pending",
    createdAt: new Date().toISOString(),
    systemFile,
  };
  jobs.set(token, job);

  // Persist job info to disk
  const jobDir = getJobDir(token);
  await fs.mkdir(jobDir, { recursive: true });
  await fs.writeFile(
    path.join(jobDir, "job.json"),
    JSON.stringify(job, null, 2)
  );

  log.info(`Job created: token=${token} systemFile=${systemFile}`);
  return job;
}

export async function getJob(token: string): Promise<JobInfo | null> {
  // Tokens are server-generated UUIDs; reject anything else before it is
  // used to build a filesystem path (e.g. "../../etc").
  if (!isValidJobToken(token)) {
    return null;
  }

  // Check in-memory first
  if (jobs.has(token)) {
    return jobs.get(token)!;
  }

  // Try loading from disk
  const jobFile = path.join(getJobDir(token), "job.json");
  try {
    const data = await fs.readFile(jobFile, "utf-8");
    const job = JSON.parse(data) as JobInfo;
    jobs.set(token, job);
    return job;
  } catch {
    return null;
  }
}

async function updateJob(job: JobInfo): Promise<void> {
  jobs.set(job.token, job);
  const jobDir = getJobDir(job.token);
  await fs.writeFile(
    path.join(jobDir, "job.json"),
    JSON.stringify(job, null, 2)
  );
}

export async function runGtopt(token: string): Promise<void> {
  const job = await getJob(token);
  if (!job) return;

  job.status = "running";
  await updateJob(job);

  const inputDir = getJobInputDir(token);
  const outputDir = getJobOutputDir(token);

  await fs.mkdir(outputDir, { recursive: true });
  const jobDir = getJobDir(token);
  const logDir = path.join(jobDir, "logs");
  await fs.mkdir(logDir, { recursive: true });
  await fs.mkdir(path.join(jobDir, "tmp"), { recursive: true });

  // Re-validate the system file right before it reaches the command line
  // (job.json may have been loaded from disk) and confine it — following
  // symlinks — to the job input directory.
  const check = validateSystemFile(job.systemFile);
  const resolvedSystemFile = check.ok
    ? await resolveInside(inputDir, check.value)
    : null;
  if (!check.ok || resolvedSystemFile === null) {
    job.status = "failed";
    job.error = check.ok
      ? `System file '${job.systemFile}' is outside the job input directory or missing`
      : `Invalid systemFile: ${check.error}`;
    log.error(`Job ${token}: refusing to run gtopt: ${job.error}`);
    await updateJob(job);
    return;
  }
  const systemFileArg = check.value;

  const gtoptBin = await resolveGtoptBinary();
  log.info(`Job ${token}: starting gtopt binary=${gtoptBin} systemFile=${job.systemFile}`);
  log.info(`Job ${token}: working directory (cwd)=${inputDir}`);
  log.info(`Job ${token}: output directory=${outputDir}`);

  // Log input directory contents before execution
  try {
    const inputFiles = await listDirRecursive(inputDir);
    log.info(`Job ${token}: input directory contains ${inputFiles.length} file(s):`);
    for (const f of inputFiles) {
      log.info(`Job ${token}:   input: ${f}`);
    }
  } catch (err) {
    log.warn(`Job ${token}: could not list input directory contents: ${err}`);
  }

  // Optional whitespace-separated extra args appended to every gtopt
  // invocation.  Used by `webservice/test/e2e_test.sh` to force
  // `--write-out all` (so the c0 golden fixtures under
  // `cases/c0/output/Demand/*_cost.csv` keep matching the lean default
  // emit set, which only writes reduced costs for Generator+Line).
  // Empty / unset means production behaviour is unchanged.
  const extraArgs = (process.env.GTOPT_EXTRA_ARGS ?? "")
    .split(/\s+/)
    .filter((s) => s.length > 0);

  return new Promise<void>((resolve) => {
    // Arguments are passed as an array (no shell).  `systemFileArg` was
    // validated above: relative, inside inputDir, never starting with '-'.
    // The forced options come last so they win over the case options, and
    // the child gets a minimal environment (no webservice secrets, no
    // user / case config file).
    const proc = spawn(
      /*turbopackIgnore: true*/ gtoptBin,
      [systemFileArg, ...extraArgs, ...jobForcedOptions(outputDir, logDir)],
      {
        cwd: inputDir,
        env: jobEnvironment(jobDir) as NodeJS.ProcessEnv,
        stdio: ["ignore", "pipe", "pipe"] as const,
      }
    );

    // Register process so it can be stopped via stopJob()
    runningProcesses.set(token, proc);
    log.info(`Job ${token}: process started pid=${proc.pid ?? "unknown"}`);

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (data: Buffer) => {
      const text = data.toString();
      stdout += text;
      // Log each line of terminal output for real-time visibility
      for (const line of text.split("\n").filter((l: string) => l.length > 0)) {
        log.info(`Job ${token} [stdout]: ${line}`);
      }
    });

    proc.stderr.on("data", (data: Buffer) => {
      const text = data.toString();
      stderr += text;
      for (const line of text.split("\n").filter((l: string) => l.length > 0)) {
        log.warn(`Job ${token} [stderr]: ${line}`);
      }
    });

    proc.on("close", async (code, signal) => {
      // Unregister the process when it exits
      runningProcesses.delete(token);

      if (code === 0) {
        job.status = "completed";
        job.completedAt = new Date().toISOString();
        log.info(`Job ${token}: gtopt completed successfully (exit code 0)`);
      } else {
        job.status = "failed";
        // The solver output stays in the per-job logs; the status API only
        // reports how the process ended.
        job.error = publicJobError(code, signal);
        log.error(`Job ${token}: ${job.error}`);
      }

      // Remove the stop-request file so it does not interfere with
      // subsequent hot-start runs in the same output directory.
      await cleanupStopRequestFile(token);

      // Log output directory contents after execution
      try {
        const outputFiles = await listDirRecursive(outputDir);
        log.info(`Job ${token}: output directory contains ${outputFiles.length} file(s):`);
        for (const f of outputFiles) {
          log.info(`Job ${token}:   output: ${f}`);
        }
      } catch (err) {
        log.warn(`Job ${token}: could not list output directory contents: ${err}`);
      }

      // Save logs to job directory
      try {
        const jobDir = getJobDir(token);
        await fs.writeFile(path.join(jobDir, "stdout.log"), stdout);
        await fs.writeFile(path.join(jobDir, "stderr.log"), stderr);
        log.info(`Job ${token}: saved stdout.log (${stdout.length} bytes) and stderr.log (${stderr.length} bytes)`);
      } catch {
        log.warn(`Job ${token}: failed to save log files`);
      }
      // Also save terminal output inside the output directory so it is
      // included in the results ZIP downloaded by the GUI.
      try {
        await fs.writeFile(path.join(outputDir, "gtopt_terminal.log"), stdout + stderr);
      } catch {
        // Ignore write errors
      }
      await updateJob(job);
      resolve();
    });

    proc.on("error", async (err) => {
      runningProcesses.delete(token);
      job.status = "failed";
      job.error = "Failed to start gtopt (see the webservice log)";
      log.error(`Job ${token}: failed to start gtopt: ${err.message}`);
      await updateJob(job);
      resolve();
    });
  });
}

/**
 * The monitoring API stop-request file name.
 * Must match `sddp_file::stop_request` in include/gtopt/sddp_solver.hpp.
 *
 * When this file exists in the job output directory the SDDP solver stops
 * gracefully after the current iteration and saves all accumulated Benders
 * cuts.  The solver checks both the legacy sentinel file and this file
 * (whichever is found first triggers a soft stop).
 */
export const SDDP_STOP_REQUEST_FILE = "sddp_stop_request.json";

/**
 * Soft-stop a running SDDP job by writing the monitoring API stop-request
 * file in the job output directory.  The SDDP solver checks for this file
 * at the start of each iteration; when found it finishes the iteration,
 * saves all accumulated Benders cuts, and returns normally.
 *
 * The stop-request file contains a small JSON object with the timestamp and
 * the requesting source, useful for debugging.  The C++ solver only tests
 * file existence, not content.
 *
 * The output directory is created if it does not yet exist so that the file
 * can be written as soon as the solver starts.  Returns true if the file was
 * written successfully, false otherwise.
 */
export async function softStopJob(token: string): Promise<boolean> {
  const outputDir = getJobOutputDir(token);
  const stopRequestPath = path.join(outputDir, SDDP_STOP_REQUEST_FILE);
  try {
    // Ensure the output directory exists before writing
    await fs.mkdir(outputDir, { recursive: true });
    const payload = JSON.stringify(
      {
        stop_requested: true,
        source: "webservice",
        timestamp: new Date().toISOString(),
      },
      null,
      2
    );
    await fs.writeFile(stopRequestPath, payload + "\n");
    log.info(
      `Job ${token}: wrote monitoring API stop-request to ${stopRequestPath}`
    );
    return true;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    log.warn(
      `Job ${token}: could not write stop-request file ${stopRequestPath}: ${msg}`
    );
    return false;
  }
}

/**
 * Remove the monitoring API stop-request file (if present) so it does not
 * interfere with a subsequent hot-start run of the same job directory.
 * Called internally when a job transitions to completed or failed.
 */
async function cleanupStopRequestFile(token: string): Promise<void> {
  const stopRequestPath = path.join(
    getJobOutputDir(token),
    SDDP_STOP_REQUEST_FILE
  );
  try {
    await fs.unlink(stopRequestPath);
    log.info(`Job ${token}: removed stop-request file ${stopRequestPath}`);
  } catch {
    // File may not exist — that is the normal case; ignore silently
  }
}

/**
 * Stop a running job by sending SIGTERM to its child process (hard stop).
 * Returns true if a signal was sent successfully, false if the job was not
 * running or if the signal could not be delivered.
 */
export async function stopJob(token: string): Promise<boolean> {
  const proc = runningProcesses.get(token);
  if (!proc) {
    log.info(`Job ${token}: stop requested but no running process found`);
    return false;
  }
  try {
    log.info(`Job ${token}: sending SIGTERM to process pid=${proc.pid}`);
    proc.kill("SIGTERM");
    return true;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    log.warn(`Job ${token}: could not send SIGTERM (pid=${proc.pid}): ${msg}`);
    return false;
  }
}

/**
 * Read the solver monitor status JSON file for a job.
 * Checks for solver_status.json first, then legacy names.
 * Returns the parsed JSON object or null if not found.
 */
export async function getJobMonitorData(
  token: string
): Promise<Record<string, unknown> | null> {
  const outputDir = getJobOutputDir(token);
  const candidates = [
    path.join(outputDir, "solver_status.json"),
    path.join(outputDir, "sddp_status.json"),
    path.join(outputDir, "monolithic_status.json"),
  ];
  for (const candidate of candidates) {
    try {
      const raw = await fs.readFile(/*turbopackIgnore: true*/ candidate, "utf-8");
      const data = JSON.parse(raw) as Record<string, unknown>;
      // Annotate with the solver type derived from the filename
      data._solver_type = path.basename(candidate).startsWith("sddp")
        ? "sddp"
        : "monolithic";
      return data;
    } catch (e) {
      // File not present or not valid JSON yet — try next candidate
      const msg = e instanceof Error ? e.message : String(e);
      log.warn(`getJobMonitorData: could not parse ${candidate}: ${msg}`);
    }
  }
  return null;
}

export async function resolveGtoptBinary(): Promise<string> {
  // Check configured path first ($GTOPT_BIN, when set).
  const configured = gtoptBinPath();
  if (configured) {
    try {
      await fs.access(configured, fs.constants.X_OK);
      log.info(`Using configured gtopt binary: ${configured}`);
      return configured;
    } catch {
      // Fall through
    }
  }

  // Check common install locations — user-local installation first so that a
  // non-root installation in ~/.local/bin takes precedence over a system-wide
  // one in /usr/local/bin.
  const candidates = [
    path.join(/*turbopackIgnore: true*/ process.env.HOME || "", ".local",
              "bin", "gtopt"),
    "/usr/local/bin/gtopt",
    "/usr/bin/gtopt",
    path.join(/*turbopackIgnore: true*/ process.cwd(), "..", "build",
              "standalone", "gtopt"),
  ];

  for (const candidate of candidates) {
    try {
      await fs.access(candidate, fs.constants.X_OK);
      log.info(`Found gtopt binary at: ${candidate}`);
      return candidate;
    } catch {
      continue;
    }
  }

  // Default: hope it's on PATH
  log.warn("gtopt binary not found in configured paths, falling back to PATH lookup");
  return "gtopt";
}

export async function listJobs(): Promise<JobInfo[]> {
  const jobsDir = path.join(dataDir(), "jobs");
  try {
    const entries = await fs.readdir(jobsDir, { withFileTypes: true });
    const result: JobInfo[] = [];
    for (const entry of entries) {
      if (entry.isDirectory()) {
        const job = await getJob(entry.name);
        if (job) result.push(job);
      }
    }
    return result.sort(
      (a, b) =>
        new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
    );
  } catch {
    return [];
  }
}

/**
 * Read saved stdout/stderr logs for a job.
 */
export async function getJobLogs(
  token: string
): Promise<{ stdout: string; stderr: string } | null> {
  const jobDir = getJobDir(token);
  let stdout = "";
  let stderr = "";
  try {
    stdout = await fs.readFile(path.join(jobDir, "stdout.log"), "utf-8");
  } catch {
    // file may not exist yet
  }
  try {
    stderr = await fs.readFile(path.join(jobDir, "stderr.log"), "utf-8");
  } catch {
    // file may not exist yet
  }
  return { stdout, stderr };
}

/**
 * Query the gtopt binary for its version string.
 */
export async function getGtoptVersion(): Promise<string> {
  const gtoptBin = await resolveGtoptBinary();
  return new Promise<string>((resolve) => {
    execFile(/*turbopackIgnore: true*/ gtoptBin, ["--version"], { timeout: 5000 }, (err, stdout, stderr) => {
      if (err) {
        log.warn(`Failed to get gtopt version: ${err.message}`);
        resolve("");
        return;
      }
      const output = (stdout || stderr || "").trim();
      resolve(output);
    });
  });
}
