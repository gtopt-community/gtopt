import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";
import {
  createJob,
  ensureDataDir,
  getJobDir,
  getJobInputDir,
  runGtopt,
  listJobs,
} from "@/lib/jobs";
import { createLogger } from "@/lib/logger";
import { listDirRecursive } from "@/lib/files";
import { resolveInside, validateSystemFile } from "@/lib/security";
import { safeExtractZip } from "@/lib/unzip";
import { checkCaseDir } from "@/lib/jobsandbox";

const log = createLogger("api/jobs");

export const dynamic = "force-dynamic";

// POST /api/jobs - Upload a case zip and start a job
export async function POST(request: NextRequest) {
  const startTime = Date.now();
  try {
    await ensureDataDir();

    const formData = await request.formData();
    const file = formData.get("file") as File | null;
    const systemFileField = formData.get("systemFile");
    const systemFile =
      typeof systemFileField === "string" ? systemFileField : null;

    if (!file) {
      log.warn("POST /api/jobs: no file uploaded");
      return NextResponse.json(
        { error: "No file uploaded. Please upload a zip file." },
        { status: 400 }
      );
    }

    if (!systemFile) {
      log.warn("POST /api/jobs: no systemFile specified");
      return NextResponse.json(
        {
          error:
            "No systemFile specified. Please provide the system JSON filename.",
        },
        { status: 400 }
      );
    }

    // Validate file type
    if (
      !file.name.endsWith(".zip")
    ) {
      log.warn(`POST /api/jobs: invalid file type: ${file.name}`);
      return NextResponse.json(
        {
          error:
            "Invalid file type. Please upload a .zip file.",
        },
        { status: 400 }
      );
    }

    // Allowlist validation: no traversal, no absolute path, never an option.
    const check = validateSystemFile(systemFile);
    if (!check.ok) {
      log.warn(`POST /api/jobs: rejected systemFile: ${check.error}`);
      return NextResponse.json(
        { error: `Invalid systemFile: ${check.error}` },
        { status: 400 }
      );
    }

    log.info(`POST /api/jobs: received file=${file.name} (${file.size} bytes) systemFile=${check.value}`);

    // Create job
    const job = await createJob(check.value);
    const inputDir = getJobInputDir(job.token);
    await fs.mkdir(inputDir, { recursive: true });
    log.info(`Job ${job.token}: created dedicated job directory inputDir=${inputDir}`);

    // Save uploaded file next to (not inside) the extraction directory
    const buffer = Buffer.from(await file.arrayBuffer());
    const zipPath = path.join(getJobDir(job.token), "upload.zip");
    await fs.writeFile(zipPath, buffer);
    log.info(`Job ${job.token}: saved uploaded zip (${buffer.length} bytes) to ${zipPath}`);

    // Extract zip into the dedicated job input directory
    try {
      await safeExtractZip(zipPath, inputDir);
      log.info(`Job ${job.token}: zip extracted successfully to ${inputDir}`);
    } catch (err) {
      await fs.unlink(zipPath).catch(() => {});
      log.error(`Job ${job.token}: zip extraction failed in ${inputDir}: ${err instanceof Error ? err.message : String(err)}`);
      return NextResponse.json(
        { error: "Failed to extract zip file (unsafe or too large archive)" },
        { status: 400 }
      );
    }

    // Clean up zip file after extraction
    await fs.unlink(zipPath);

    // Log the contents of the extracted directory
    try {
      const extractedFiles = await listDirRecursive(inputDir);
      log.info(`Job ${job.token}: extracted ${extractedFiles.length} file(s) in ${inputDir}:`);
      for (const f of extractedFiles) {
        log.info(`Job ${job.token}:   ${f}`);
      }
    } catch (err) {
      log.warn(`Job ${job.token}: could not list extracted files: ${err}`);
    }

    // Case options must not point outside the job directory.
    const caseCheck = await checkCaseDir(inputDir);
    if (!caseCheck.ok) {
      log.warn(`Job ${job.token}: rejected case: ${caseCheck.error}`);
      return NextResponse.json(
        { error: `Invalid case: ${caseCheck.error}` },
        { status: 400 }
      );
    }

    // Verify the system file exists and (following symlinks) stays inside
    // the job input directory.
    const systemFilePath = await resolveInside(inputDir, check.value);
    if (systemFilePath === null) {
      log.error(`Job ${job.token}: system file '${check.value}' not found in archive under ${inputDir}`);
      return NextResponse.json(
        {
          error: `System file '${check.value}' not found in uploaded archive.`,
        },
        { status: 400 }
      );
    }
    log.info(`Job ${job.token}: system file '${check.value}' found at ${systemFilePath}`);

    // Start the job asynchronously
    log.info(`Job ${job.token}: starting gtopt asynchronously, cwd=${inputDir}`);
    runGtopt(job.token).catch((err) => {
      log.error(`Job ${job.token} failed: ${err}`);
    });

    log.info(`Job ${job.token}: job submitted successfully (total request time: ${Date.now() - startTime}ms)`);
    return NextResponse.json(
      {
        token: job.token,
        status: job.status,
        message:
          "Job submitted successfully. Use the token to check status and download results.",
      },
      { status: 201 }
    );
  } catch (err) {
    log.error(`POST /api/jobs: internal error: ${err}`);
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}

// GET /api/jobs - List all jobs
export async function GET() {
  const startTime = Date.now();
  try {
    log.info("GET /api/jobs: listing jobs");
    const allJobs = await listJobs();
    log.info(`GET /api/jobs: returning ${allJobs.length} job(s) in ${Date.now() - startTime}ms`);
    return NextResponse.json({ jobs: allJobs });
  } catch (err) {
    log.error(`GET /api/jobs: internal error: ${err}`);
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
