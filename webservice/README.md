# GTOpt Web Service

A web service for uploading, running, and downloading results from GTOpt
optimization cases. Built with [Next.js](https://nextjs.org/) (React +
Node.js full-stack framework).

## Features

- **Upload** a case directory as a `.zip` archive containing the system JSON
  file and data files (e.g., Parquet files).
- **Submit** the job and receive a unique **token** for tracking.
- **Check status** of a running job using the token.
- **Download results** as a compressed `.zip` file once the job completes.
- **Recent jobs list** with auto-refresh on the web UI.

## Quick Start

### Using gtopt_websrv Launcher (Recommended)

After installing via CMake:

```bash
# Install webservice
cmake -S webservice -B build-web
sudo cmake --install build-web

# Install Node dependencies (including build dependencies)
cd /usr/local/share/gtopt/webservice
npm install
npm run build

# Launch the web service
gtopt_websrv

# Or with options
gtopt_websrv --port 8080
gtopt_websrv --gtopt-bin /path/to/gtopt
gtopt_websrv --help
```

**Note**: We use `npm install` (not `--production`) because TypeScript and other
devDependencies are required for the build step (`npm run build`).

### Prerequisites

- Node.js 18+ and npm
- A built `gtopt` binary (see the main project README)

### Development

```bash
cd webservice
npm install
npm run dev
```

The service starts at `http://localhost:3000`.

Set the `GTOPT_BIN` environment variable to point to your GTOpt binary:

```bash
GTOPT_BIN=/path/to/gtopt npm run dev
```

### Production Build

```bash
npm run build
GTOPT_BIN=/path/to/gtopt npm run start
```

### Using CMake

From the repository root:

```bash
# Configure (from standalone build directory)
cmake -S standalone -B build

# Install web service dependencies and build
cmake --build build --target webservice-install
cmake --build build --target webservice-build

# Start in production mode
cmake --build build --target webservice-start
```

## API Reference

When `GTOPT_WEBSERVICE_API_TOKEN` is set, add
`-H "Authorization: Bearer $GTOPT_WEBSERVICE_API_TOKEN"` to every request
below (see [Security](#security)).

### POST /api/jobs

Upload a case archive and submit a new optimization job.

**Request** (multipart/form-data):
- `file` — `.zip` archive containing the case directory
- `systemFile` — name of the system JSON file inside the archive (e.g.,
  `system_c0.json`)

**Response** (201 Created):
```json
{
  "token": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Job submitted successfully..."
}
```

### GET /api/jobs

List all submitted jobs.

**Response** (200):
```json
{
  "jobs": [
    {
      "token": "...",
      "status": "completed",
      "createdAt": "2025-01-01T00:00:00.000Z",
      "completedAt": "2025-01-01T00:01:00.000Z",
      "systemFile": "system_c0.json"
    }
  ]
}
```

### GET /api/jobs/:token

Get the status of a specific job.

**Response** (200):
```json
{
  "token": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "createdAt": "2025-01-01T00:00:00.000Z",
  "completedAt": "2025-01-01T00:01:00.000Z",
  "systemFile": "system_c0.json"
}
```

### GET /api/jobs/:token/download

Download job results as a zip file.

**Response** (200): Binary `.zip` file containing:
- `output/` — solver output files
- `stdout.log` — solver standard output
- `stderr.log` — solver standard error
- `job.json` — job metadata

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `GTOPT_BIN` | `../build/gtopt` | Path to the GTOpt binary |
| `GTOPT_DATA_DIR` | `./data` | Directory for job data storage |
| `GTOPT_LOG_DIR` | *(console only)* | Directory for log files |
| `PORT` | `3000` | HTTP port |
| `GTOPT_WEBSERVICE_HOST` | `127.0.0.1` | Bind address used by `gtopt_websrv` (legacy alias `GTOPT_HOSTNAME`) |
| `GTOPT_WEBSERVICE_API_TOKEN` | *(unset)* | Shared API token; required to bind a non-loopback address |

## Security

The web service runs the GTOpt solver on uploaded archives, so it must not be
reachable by untrusted clients.

- **Loopback by default.** `gtopt_websrv`, `npm run start` and `npm run dev`
  bind to `127.0.0.1`.  Without an API token the API also rejects any request
  whose `Host` header is not `localhost` / `127.0.0.1` / `::1` (this blocks
  DNS-rebinding attacks from a browser).
- **Exposing the service** on any other interface (`--hostname 0.0.0.0`,
  `GTOPT_WEBSERVICE_HOST=...`) requires an API token; the launcher refuses to
  start otherwise.  Generate a long random secret and keep it out of
  world-readable files:

  ```bash
  export GTOPT_WEBSERVICE_API_TOKEN=$(openssl rand -hex 32)
  gtopt_websrv --hostname 0.0.0.0
  ```

  Every `/api/*` request (except the bare `GET /api` liveness probe) must then
  send `Authorization: Bearer <token>` (or `X-GTOpt-Api-Token: <token>`); the
  token is compared in constant time.  The GUI service forwards the same
  `GTOPT_WEBSERVICE_API_TOKEN` environment variable automatically.
- **Use a TLS reverse proxy** (nginx, Caddy, …) in front of any non-loopback
  deployment so the token is never sent in clear text, and keep the Node.js
  port itself bound to `127.0.0.1`.
- **Input validation.** `systemFile` must be a relative path made of
  `[A-Za-z0-9_.+-]` segments separated by `/` (no `..`, no leading `-` or `.`,
  no absolute paths); it is resolved with `realpath` and must stay inside the
  job's input directory.  Uploaded archives are extracted with symlinks,
  absolute paths and `..` entries rejected.  Job tokens must be UUIDs.

## Testing

An integration test script validates the full workflow using a mock GTOpt
binary. It starts the server, submits a case, waits for completion, downloads
and verifies results, and tests error handling.

```bash
# Requires a prior build: npm run build
npm test
# or directly:
bash test/integration_test.sh [port]
```

## Project Structure

```
webservice/
├── CMakeLists.txt          # CMake build/install configuration
├── package.json            # Node.js dependencies
├── next.config.ts          # Next.js configuration
├── tsconfig.json           # TypeScript configuration
├── src/
│   ├── app/
│   │   ├── layout.tsx      # Root layout
│   │   ├── globals.css     # Global styles
│   │   ├── page.tsx        # Main UI page
│   │   └── api/
│   │       └── jobs/
│   │           ├── route.ts           # POST/GET /api/jobs
│   │           └── [token]/
│   │               ├── route.ts       # GET /api/jobs/:token
│   │               └── download/
│   │                   └── route.ts   # GET /api/jobs/:token/download
│   └── lib/
│       └── jobs.ts         # Job management logic
├── test/
│   └── integration_test.sh # End-to-end integration test
└── README.md
```

## Licence and source offer (AGPL)

The web service is part of the GTOpt core, licensed
`AGPL-3.0-or-later OR LicenseRef-gtopt-commercial` (see
[`LICENSING.md`](../LICENSING.md)). The UI footer and `GET /api` (`source`
field) link to the corresponding source. If you deploy a **modified**
version for users over a network, set `GTOPT_SOURCE_URL` to the source of
your version and update the footer link, or obtain a commercial licence.
