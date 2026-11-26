/**
 * Unit tests for src/lib/jobsandbox.ts — job environment, forced options,
 * case-path checks, log redaction and public job errors.
 */

import { promises as fs } from "fs";
import * as os from "os";
import * as path from "path";
import {
  JOB_PATH,
  JOB_ENV_PASSTHROUGH_ENV,
  MAX_CASE_JSON_BYTES,
  checkCaseDir,
  checkCaseJson,
  jobEnvironment,
  jobForcedOptions,
  publicJobError,
  redactJobTokens,
} from "../jobsandbox";

describe("jobEnvironment", () => {
  it("starts from a minimal environment", () => {
    const env = jobEnvironment("/data/jobs/x", {
      PATH: "/evil:/usr/bin",
      HOME: "/home/someone",
      GTOPT_WEBSERVICE_API_TOKEN: "secret",
      AWS_SECRET_ACCESS_KEY: "k",
      GTOPT_CONFIG: "/home/someone/.gtopt.conf",
      LANG: "C.UTF-8",
      GTOPT_SOLVER: "highs",
    });
    expect(env.PATH).toBe(JOB_PATH);
    expect(env.HOME).toBe("/data/jobs/x");
    expect(env.TMPDIR).toBe(path.join("/data/jobs/x", "tmp"));
    expect(env.GTOPT_CONFIG).toBe("/dev/null");
    expect(env.LANG).toBe("C.UTF-8");
    expect(env.GTOPT_SOLVER).toBe("highs");
    expect(env.GTOPT_WEBSERVICE_API_TOKEN).toBeUndefined();
    expect(env.AWS_SECRET_ACCESS_KEY).toBeUndefined();
  });

  it("honours the extra pass-through list but never overrides fixed vars", () => {
    const env = jobEnvironment("/j", {
      [JOB_ENV_PASSTHROUGH_ENV]: "MY_LICENSE, bad-name ,PATH,EMPTY",
      MY_LICENSE: "srv@host",
      PATH: "/evil",
      EMPTY: "",
    });
    expect(env.MY_LICENSE).toBe("srv@host");
    expect(env.PATH).toBe(JOB_PATH);
    expect(env.EMPTY).toBeUndefined();
    expect(Object.keys(env)).not.toContain("bad-name");
  });

  it("defaults to process.env", () => {
    expect(jobEnvironment("/j").GTOPT_CONFIG).toBe("/dev/null");
  });
});

describe("jobForcedOptions", () => {
  it("pins output, log directory and disables LP debug", () => {
    expect(jobForcedOptions("/o", "/l")).toEqual([
      "--set",
      "output_directory=/o",
      "--set",
      "log_directory=/l",
      "--set",
      "lp_debug=false",
    ]);
  });
});

describe("checkCaseJson", () => {
  it("accepts relative paths and ordinary values", () => {
    const doc = {
      options: {
        input_directory: "input",
        output_directory: "out/run1",
        sddp_options: { cut_directory: "cuts", cuts_input_file: "cuts/x.csv" },
        description: "Case with / slashes and .. dots in prose",
      },
      system: { generator_array: [{ name: "g1", pmax: 10, profile: "solar" }] },
      flag: true,
      n: null,
    };
    expect(checkCaseJson(doc)).toEqual({ ok: true });
  });

  it.each([
    [{ options: { log_directory: "/var/tmp/x" } }, "absolute"],
    [{ options: { output_directory: "../../elsewhere" } }, ".."],
    [{ options: { cut_directory: "C:\\x" } }, "absolute"],
    [{ options: { x_file: "~/x" } }, "~"],
    [{ options: { x_path: "a\0b" } }, "NUL"],
    [{ system: { demand_array: [{ lmax: "../secret" }] } }, ".."],
    [{ options: { input_directory: "\\\\host\\share" } }, "absolute"],
  ])("rejects unsafe value %#", (doc, needle) => {
    const res = checkCaseJson(doc);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error).toContain(needle);
  });

  it("reports where the offending value is", () => {
    const res = checkCaseJson({ a: [{ b_file: "/etc/x" }] }, "case.json");
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error).toContain("case.json.a[0].b_file");
  });

  it("ignores prose with spaces outside path keys", () => {
    expect(checkCaseJson({ note: "see ../docs for details" })).toEqual({ ok: true });
  });
});

describe("checkCaseDir", () => {
  let dir: string;
  beforeEach(async () => {
    dir = await fs.mkdtemp(path.join(os.tmpdir(), "gtopt-casecheck-"));
  });
  afterEach(async () => {
    await fs.rm(dir, { recursive: true, force: true });
  });

  it("accepts a clean case and skips non-JSON / invalid JSON", async () => {
    await fs.mkdir(path.join(dir, "sub"));
    await fs.writeFile(path.join(dir, "case.json"), JSON.stringify({ options: { input_directory: "sub" } }));
    await fs.writeFile(path.join(dir, "sub", "broken.JSON"), "{not json");
    await fs.writeFile(path.join(dir, "sub", "data.csv"), "a,b\n");
    expect(await checkCaseDir(dir)).toEqual({ ok: true });
  });

  it("rejects an unsafe nested JSON file", async () => {
    await fs.mkdir(path.join(dir, "sub"));
    await fs.writeFile(path.join(dir, "sub", "extra.json"), JSON.stringify({ options: { log_directory: "/tmp/x" } }));
    const res = await checkCaseDir(dir);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error).toContain(path.join("sub", "extra.json"));
  });

  it("rejects an oversized JSON file", async () => {
    const big = path.join(dir, "big.json");
    const fh = await fs.open(big, "w");
    await fh.truncate(MAX_CASE_JSON_BYTES + 1);
    await fh.close();
    const res = await checkCaseDir(dir);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error).toContain("larger than");
  });
});

describe("redactJobTokens / publicJobError", () => {
  it("shortens UUIDs", () => {
    const line = "Job 0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b: started, other 0F1E2D3C-4B5A-6978-8A9B-0C1D2E3F4A5B";
    const out = redactJobTokens(line);
    expect(out).toBe("Job 0f1e2d3c…: started, other 0F1E2D3C…");
  });

  it("reports exit code or signal only", () => {
    expect(publicJobError(3)).toContain("exited with code 3");
    expect(publicJobError(null, "SIGKILL")).toContain("terminated by SIGKILL");
  });
});
