# Installing and Running the GTOpt GUI Service

This guide covers setting up a server, installing the GTOpt GUI service, and
running it in development or production. The GUI service provides a web-based
graphical interface for creating, editing, and visualizing GTOpt optimization
cases, with integrated connectivity to the
[GTOpt webservice](../webservice/INSTALL.md) for remote solving.

## Table of Contents

- [Prerequisites](#prerequisites)
- [1. Server Setup](#1-server-setup)
- [2. Install Python and Dependencies](#2-install-python-and-dependencies)
- [3. Install the GUI Service](#3-install-the-gui-service)
- [4. Configure the GUI Service](#4-configure-the-gui-service)
- [5. Run the GUI Service](#5-run-the-gui-service)
- [6. Run as a System Service](#6-run-as-a-system-service)
- [7. Reverse Proxy (nginx)](#7-reverse-proxy-nginx)
- [8. Connecting to the GTOpt Webservice](#8-connecting-to-the-gtopt-webservice)
- [9. Testing](#9-testing)
- [10. API Reference](#10-api-reference)
- [11. Troubleshooting](#11-troubleshooting)

---

## Prerequisites

| Component | Minimum Version | Purpose |
|-----------|----------------|---------|
| Ubuntu | 22.04 LTS | Server OS (other Linux distros work with adjustments) |
| Python | 3.10 | Runtime for the Flask application |
| pip | 22 | Python package manager |
| (Optional) GTOpt webservice | — | Remote solver (see [webservice/INSTALL.md](../webservice/INSTALL.md)) |

## 1. Server Setup

Start with a fresh Ubuntu server (e.g., an AWS EC2 instance, a DigitalOcean
droplet, or a local machine).

### Update the system

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

### Install basic tools

```bash
sudo apt-get install -y build-essential curl git zip unzip wget
```

### Configure the firewall (optional)

If using `ufw`, allow SSH and the GUI service port:

```bash
sudo ufw allow ssh
sudo ufw allow 5001/tcp   # or your chosen port
sudo ufw enable
```

## 2. Install Python and Dependencies

### Install Python 3.12 (recommended)

On Ubuntu 24.04, Python 3.12 is available by default:

```bash
sudo apt-get install -y python3 python3-pip python3-venv
python3 --version   # should be 3.10+
```

On older Ubuntu versions, use the deadsnakes PPA:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
```

### Create a virtual environment (recommended)

```bash
python3 -m venv /opt/guiservice/venv
source /opt/guiservice/venv/bin/activate
```

## 3. Install the GUI Service

### Clone the repository

```bash
git clone https://github.com/gtopt-community/gtopt.git
cd gtopt
```

### Install Python dependencies

```bash
pip install -r guiservice/requirements.txt
```

The dependencies are:

| Package | Version | Purpose |
|---------|---------|---------|
| Flask | ≥ 3.1.1 | Web framework |
| pandas | ≥ 2.2.3 | Data manipulation for CSV/Parquet handling |
| pyarrow | ≥ 19.0.1 | Parquet file support |
| requests | ≥ 2.32.3 | HTTP client for webservice communication |

### Verify the installation

```bash
cd guiservice
python app.py &
curl -s http://localhost:5001/api/schemas | python3 -m json.tool | head -5
# Should output JSON with element schemas (bus, generator, demand, etc.)
kill %1
```

## 4. Configure the GUI Service

The GUI service is configured through environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `GTOPT_WEBSERVICE_URL` | `http://localhost:3000` | URL of the GTOpt webservice for remote solving |
| `GTOPT_GUI_HOST` | `127.0.0.1` | Bind address of `app.py` / `gtopt_guisrv` |
| `GTOPT_GUI_PORT` | `5001` | Port of the GUI service; loopback `Host` headers must use it |
| `GTOPT_GUI_ALLOWED_HOSTS` | *(empty)* | Extra `Host` header values (`host` or `host:port`, comma-separated) accepted besides loopback (DNS-rebinding guard) |
| `GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS` | *(empty)* | Extra non-loopback webservice hosts (`host` or `host:port`, comma-separated) accepted by `POST /api/solve/config` |
| `GTOPT_WEBSERVICE_API_TOKEN` | *(unset)* | Token forwarded to the webservice as `Authorization: Bearer` |
| `GTOPT_GUI_SHUTDOWN_TOKEN` | *(unset)* | Secret that authorises `POST /api/shutdown` from non-loopback clients |
| `FLASK_DEBUG` | `0` | `1` enables the Werkzeug debugger — local development only, refused on non-loopback hosts |
| `GTOPT_GUI_AUTH` | `auto` | Access-token authentication: `auto` = required on non-loopback bind addresses, `on` = always, `off` = never (only behind an authenticating proxy) |
| `GTOPT_GUI_AUTH_TOKEN` | *(unset)* | The access token; when authentication is required and it is unset, a random token is generated and a login link is printed at start-up |
| `GTOPT_GUI_SECRET_KEY` | *(random per process)* | Session-cookie signing key; set it when several worker processes serve the GUI |
| `GTOPT_GUI_COOKIE_SECURE` | `0` | `1` marks the session cookie `Secure` (GUI served over HTTPS) |
| `GTOPT_GUI_SERVER` | `auto` | `waitress` (production WSGI server; used by `auto` when installed) or `werkzeug` (development server) |
| `GTOPT_GUI_LOG_FILE` | `$XDG_STATE_HOME/gtopt/guiservice.log` | Server log file (default `~/.local/state/gtopt/guiservice.log`) |
| `GTOPT_GUI_MAX_UNCOMPRESSED_BYTES` | `2147483648` | Largest total size an uploaded ZIP (incl. `.gz`/`.zst` members) may expand to |
| `GTOPT_GUI_MAX_ZIP_MEMBERS` | `20000` | Largest number of members of an uploaded ZIP |

Create a configuration file for persistent settings:

```bash
cat > /opt/guiservice/.env << 'EOF'
GTOPT_WEBSERVICE_URL=http://localhost:3000
FLASK_DEBUG=0
EOF
```

The webservice URL can also be changed at runtime through the GUI (Solver →
Webservice panel) or via the API.  Non-loopback hosts must be listed in
`GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS` (SSRF guard):

```bash
GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS=my-server python app.py &
curl -X POST http://localhost:5001/api/solve/config \
  -H "Content-Type: application/json" -H "X-GTOpt-GUI: 1" \
  -d '{"webservice_url": "http://my-server:3000"}'
```

## 5. Run the GUI Service

### Local development (with hot reload)

```bash
cd guiservice
python app.py          # binds 127.0.0.1:5001
```

`FLASK_DEBUG=1` enables the Werkzeug debugger and auto-reload.  It is for local
development on your own machine only (it allows code execution) and is refused
when `GTOPT_GUI_HOST` is not a loopback address.

The GUI will be available at `http://localhost:5001`.

### Production mode

`python app.py` / `gtopt_guisrv` serve the GUI with **waitress** (a
production WSGI server, listed in `requirements.txt`) whenever it is
installed and debug mode is off; otherwise they fall back to the Werkzeug
development server with a warning.  `GTOPT_GUI_SERVER=werkzeug` forces the
development server.

Another WSGI server works too; keep it on `127.0.0.1`, set
`GTOPT_GUI_AUTH_TOKEN` (and `GTOPT_GUI_SECRET_KEY` for several worker
processes), and put a TLS reverse proxy in front of it for remote users:

```bash
pip install gunicorn
cd guiservice
GTOPT_GUI_AUTH=on GTOPT_GUI_AUTH_TOKEN=... GTOPT_GUI_SECRET_KEY=... \
  gunicorn -w 4 -b 127.0.0.1:5001 app:app
```

### Quick start (no webservice required)

The GUI service works standalone for case creation, editing, and download.
No GTOpt webservice is required for these features:

```bash
cd guiservice
python app.py
# Open http://localhost:5001 in your browser
```

## 6. Run as a System Service

For production deployments, use systemd to run the GUI service as a background
service that starts on boot.

### Create a systemd unit file

```bash
sudo tee /etc/systemd/system/gtopt-guiservice.service << 'EOF'
[Unit]
Description=GTOpt GUI Service
After=network.target

[Service]
Type=simple
User=gtopt
Group=gtopt
WorkingDirectory=/home/gtopt/gtopt/guiservice
Environment=GTOPT_WEBSERVICE_URL=http://localhost:3000
ExecStart=/opt/guiservice/venv/bin/gunicorn -w 4 -b 127.0.0.1:5001 app:app
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

### Create a dedicated service user (recommended)

```bash
sudo useradd -r -m -s /bin/bash gtopt
# Copy or clone the repo under /home/gtopt/gtopt and install there
```

### Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable gtopt-guiservice
sudo systemctl start gtopt-guiservice

# Check status
sudo systemctl status gtopt-guiservice

# View logs
sudo journalctl -u gtopt-guiservice -f
```

## 7. Reverse Proxy (nginx)

For production, place nginx in front of the Flask application to handle TLS,
static file serving, and standard HTTP port mapping.

### Install nginx

```bash
sudo apt-get install -y nginx
```

### Configure a site

```bash
sudo tee /etc/nginx/sites-available/gtopt-gui << 'EOF'
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 100M;  # Allow large case file uploads

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/gtopt-gui /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Add TLS with Let's Encrypt (optional)

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## 8. Connecting to the GTOpt Webservice

The GUI service can submit cases to the GTOpt webservice for solving. This
requires a running webservice instance (see
[webservice/INSTALL.md](../webservice/INSTALL.md)).

### From the GUI

1. Start the GTOpt webservice (default: `http://localhost:3000`)
2. Open the GUI at `http://localhost:5001`
3. Navigate to **Solver → Webservice** in the left panel
4. Enter the webservice URL and click **Test** to verify the connection
5. Use the **⚡ Solve** button in the header to submit cases

### From the command line

```bash
# Set the webservice URL
curl -X POST http://localhost:5001/api/solve/config \
  -H "Content-Type: application/json" -H "X-GTOpt-GUI: 1" \
  -d '{"webservice_url": "http://localhost:3000"}'

# Submit a case (requires JSON case data)
curl -X POST http://localhost:5001/api/solve/submit \
  -H "Content-Type: application/json" -H "X-GTOpt-GUI: 1" \
  -d @case_data.json
# Returns: {"token":"<job-token>","status":"pending",...}

# Check status
curl http://localhost:5001/api/solve/status/<job-token>

# Get results when completed
curl http://localhost:5001/api/solve/results/<job-token>
```

## 9. Testing

### Run the test suite

```bash
pip install pytest
python -m pytest guiservice/tests/test_app.py -v
```

The test suite includes:

| Test class | Tests | Description |
|------------|-------|-------------|
| `TestBuildCaseJson` | 5 | Unit tests for JSON case building |
| `TestBuildZip` | 4 | Unit tests for ZIP archive generation |
| `TestParseUploadedZip` | 2 | Unit tests for ZIP parsing |
| `TestRoutes` | 8 | Integration tests for Flask routes |
| `TestRoundTrip` | 1 | Download and re-upload round-trip |
| `TestSolveConfig` | 5 | Webservice URL configuration |
| `TestSolveSubmit` | 3 | Job submission to webservice |
| `TestSolveStatus` | 2 | Job status polling |
| `TestSolveResults` | 2 | Results retrieval and parsing |
| `TestSolveJobsList` | 2 | Jobs listing from webservice |
| `TestWebserviceEndToEnd` | 6 | Full workflow: submit → poll → results |

### Run tests in CI

The GitHub Actions workflow `.github/workflows/guiservice.yml` runs the test
suite automatically on push and pull request:

```yaml
- name: Run tests
  run: python -m pytest guiservice/tests/test_app.py -v
```

## 10. API Reference

### Case Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Main GUI page |
| `GET` | `/api/schemas` | Element field schemas |
| `POST` | `/api/case/download` | Generate and download case ZIP |
| `POST` | `/api/case/upload` | Upload a case ZIP for editing |
| `POST` | `/api/case/preview` | Preview the generated JSON |
| `POST` | `/api/results/upload` | Upload results ZIP for viewing |

### Webservice Integration

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/solve/config` | Get webservice URL configuration |
| `POST` | `/api/solve/config` | Set webservice URL |
| `POST` | `/api/solve/submit` | Submit case to webservice for solving |
| `GET` | `/api/solve/status/<token>` | Poll job status from webservice |
| `GET` | `/api/solve/results/<token>` | Retrieve and parse results from webservice |
| `GET` | `/api/solve/jobs` | List all jobs from webservice |

## 11. Troubleshooting

### GUI service won't start

Check Python version and dependencies:

```bash
python3 --version   # needs 3.10+
pip list | grep -E "flask|pandas|pyarrow|requests"
```

Reinstall if needed:

```bash
pip install -r guiservice/requirements.txt
```

### "Cannot connect to webservice"

Ensure the GTOpt webservice is running and accessible:

```bash
curl http://localhost:3000/api/jobs
```

If running on a different host, update the webservice URL via the GUI or API.

### Upload fails or times out

- Check file size: the default limit is 100 MB.
- If using nginx, set `client_max_body_size 100M;` in the server block.
- Ensure the server has enough disk space for temporary files.

### Port already in use

```bash
# Find what's using the port
sudo lsof -i :5001
# Kill it or use a different port
```

To use a different port, modify the `app.run()` call in `app.py` or set it
via gunicorn:

```bash
gunicorn -w 4 -b 127.0.0.1:8080 app:app
```

### Tests fail with import errors

Make sure you run tests from the repository root:

```bash
cd /path/to/gtopt
python -m pytest guiservice/tests/test_app.py -v
```

## Security

The GUI service has a single shared **access token** (no user accounts).

- **Authentication.** With `GTOPT_GUI_AUTH=auto` (default) a token is
  required whenever the service binds a non-loopback address; a
  loopback-only service (the default) stays open for single-user local
  use.  `GTOPT_GUI_AUTH=on` requires it on loopback too (shared hosts),
  `off` disables it (only behind an authenticating proxy).  Set the token
  with `GTOPT_GUI_AUTH_TOKEN`, or let the service generate one: it prints
  a login link `http://<host>:<port>/?token=<token>`.  Opening it sets an
  `HttpOnly`, `SameSite=Strict` session cookie and redirects to the page
  without the token.  Scripted clients send
  `Authorization: Bearer <token>` (or `X-GTOpt-Auth: <token>`).
- **Security headers.** Every response carries a Content-Security-Policy
  (scripts only from the service itself plus the version-pinned,
  SRI-checked vis-network file; `connect-src 'self'`; no framing),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and
  `Referrer-Policy: no-referrer`; API responses are `no-store`.
- **Uploads.** Case and result ZIPs are limited in member count and in the
  total size they expand to (`GTOPT_GUI_MAX_ZIP_MEMBERS`,
  `GTOPT_GUI_MAX_UNCOMPRESSED_BYTES`); larger uploads get `413`.
- **Logs.** The server log lives in `$XDG_STATE_HOME/gtopt/guiservice.log`
  (never a shared `/tmp` file); `GET /api/logs` serves recent lines with
  tokens and keys redacted.
- **Excel export.** Text cells starting with `=`, `+`, `-`, `@` (or a
  tab / carriage return) are written as text, never as formulas.

- **Loopback by default.** `app.py`, `gtopt_guisrv` and `gtopt_gui` bind to
  `127.0.0.1` (override with `--host` / `GTOPT_GUI_HOST`).  To use the GUI from
  another machine prefer an SSH tunnel
  (`ssh -L 5001:127.0.0.1:5001 server`) or an authenticating TLS reverse
  proxy; binding `0.0.0.0` exposes every endpoint to the network.
- **Debug mode is for local development only.** The Werkzeug debugger allows
  arbitrary code execution, so `--debug` / `FLASK_DEBUG=1` is refused unless
  the service is bound to a loopback address.  Never enable it on a server.
- **Webservice URL (SSRF guard).** `POST /api/solve/config` only accepts
  `http`/`https` URLs without credentials, query or fragment whose host is
  loopback, the host of `GTOPT_WEBSERVICE_URL`, or listed in
  `GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS` (comma-separated `host` or
  `host:port` entries, e.g. `solver.lan,10.0.0.7:3000`).  Loopback URLs
  are limited to the port of `GTOPT_WEBSERVICE_URL` (default 3000) or a
  loopback `host:port` listed there, so another local user cannot redirect
  the GUI (and the forwarded API token) to a port of their own.
- **Host header (DNS rebinding guard).** Requests whose `Host` is not
  `localhost` / `127.0.0.1` / `[::1]` (on the GUI port), the concrete
  `GTOPT_GUI_HOST` bind address, or an entry of `GTOPT_GUI_ALLOWED_HOSTS`
  (comma-separated `host` or `host:port`) get `403`.  When serving under
  another name (reverse proxy, LAN hostname) list it there.
- **CSRF header.** Every non-GET/HEAD/OPTIONS request must carry
  `X-GTOpt-GUI` (the bundled UIs send it); scripted clients add
  `-H "X-GTOpt-GUI: 1"`.
- **Webservice API token.** When `GTOPT_WEBSERVICE_API_TOKEN` is set, the GUI
  forwards it to the webservice as `Authorization: Bearer <token>`.
- **`POST /api/shutdown`** is accepted either with the secret configured in
  `GTOPT_GUI_SHUTDOWN_TOKEN` (`X-GTOpt-Shutdown-Token: <token>` or
  `Authorization: Bearer <token>`, compared in constant time), or from a
  direct loopback client (no `X-Forwarded-For`/`Forwarded` header) that sends
  the `X-GTOpt-GUI` header used by the bundled UI (CSRF guard).
