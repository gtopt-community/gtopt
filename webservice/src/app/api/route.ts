import { NextResponse } from "next/server";
import { createLogger } from "@/lib/logger";

const log = createLogger("api");

export const dynamic = "force-dynamic";

// GET /api - API index / health check
export async function GET() {
  const startTime = Date.now();
  log.info("GET /api called");
  const response = NextResponse.json({
    status: "ok",
    service: "gtopt-webservice",
    // AGPL-3.0 section 13: offer the corresponding source to network users.
    // Deployments of a modified version must point this at their own source.
    license: "AGPL-3.0-or-later OR LicenseRef-gtopt-commercial",
    source:
      process.env.GTOPT_SOURCE_URL ?? "https://github.com/gtopt-community/gtopt",
    endpoints: [
      "GET  /api          - Service info and endpoint list",
      "GET  /api/ping     - Health check with version info",
      "POST /api/jobs     - Submit a new optimization job",
      "GET  /api/jobs     - List all jobs",
      "GET  /api/jobs/:id - Get job status",
      "GET  /api/jobs/:id/logs     - Get job logs",
      "GET  /api/jobs/:id/download - Download job results",
      "GET  /api/logs     - Retrieve service logs",
    ],
  });
  log.info(`GET /api completed in ${Date.now() - startTime}ms`);
  return response;
}
