# GTOpt GUI Service

A web-based graphical user interface for creating, editing, and visualizing
**GTOpt** optimization cases, with integrated connectivity to the
[GTOpt webservice](../webservice/README.md) for remote solving.

## Features

- **Case Creation** – Build cases from scratch with a modular form interface for
  all system elements (buses, generators, demands, lines, batteries, converters,
  junctions, waterways, reservoirs, turbines, flows, filtrations, profiles,
  reserve zones, and reserve provisions).
- **Simulation Setup** – Define blocks, stages, and scenarios through an
  interactive table editor.
- **JSON Preview** – Inspect the generated JSON configuration before downloading.
- **ZIP Download** – Download the complete case as a ZIP file ready for use with
  the GTOpt solver or the GTOpt webservice.
- **Case Upload** – Upload an existing case ZIP file for further editing.
- **Webservice Integration** – Submit cases directly to the GTOpt webservice for
  solving, monitor job progress in real-time, and retrieve results automatically
  when the solver finishes.
- **Results Visualization** – View optimization results in spreadsheet tables
  and interactive time-series charts (Chart.js). Results can be loaded from
  uploaded ZIP files or retrieved directly from the webservice.

## Requirements

- Python 3.10+
- Dependencies listed in `requirements.txt`
- (Optional) A running [GTOpt webservice](../webservice/README.md) instance for
  remote solving

For detailed installation, deployment, and production setup instructions, see
[INSTALL.md](INSTALL.md).

For the standalone `gtopt_gui` command-line launcher (interactive kiosk mode), see
[GTOPT_GUI.md](GTOPT_GUI.md).

For the `gtopt_guisrv` web server launcher (daemon/service mode), see
[GTOPT_GUISRV.md](GTOPT_GUISRV.md).

## Quick Start

### Using CMake (Recommended for System-wide Installation)

Install the guiservice and launchers system-wide:

```bash
# Install guiservice
cmake -S guiservice -B build-gui
sudo cmake --install build-gui

# Install Python dependencies
pip3 install -r /usr/local/share/gtopt/guiservice/requirements.txt

# Launch interactively (opens browser)
gtopt_gui

# Or launch as web server (no browser)
gtopt_guisrv
```

The guiservice can be installed independently of the GTOpt binary.

### Using gtopt_gui (Interactive Mode)

If you've installed via CMake, you can use the `gtopt_gui` launcher for
interactive kiosk mode:

```bash
# Launch the GUI (opens browser automatically)
gtopt_gui

# Or with a specific config file
gtopt_gui system_c0.json
```

See [GTOPT_GUI.md](GTOPT_GUI.md) for full documentation.

### Using gtopt_guisrv (Web Server Mode)

For running as a daemon or web server without opening a browser:

```bash
# Start web server on default port 5001
gtopt_guisrv

# Custom port
gtopt_guisrv --port 8080

# Run as systemd service
sudo cp /usr/local/share/gtopt/guiservice/gtopt-guiservice.service \
        /etc/systemd/system/
sudo systemctl enable --now gtopt-guiservice
```

See [GTOPT_GUISRV.md](GTOPT_GUISRV.md) for full documentation.

### Running directly (Development)

```bash
cd guiservice
pip install -r requirements.txt
python app.py
```

The GUI will be available at `http://localhost:5001` (bound to `127.0.0.1`
only; see [Security](#security)).

### Connecting to the Webservice

1. Start the GTOpt webservice (default: `http://localhost:3000`)
2. Open the GUI and navigate to the **Solver → Webservice** panel
3. Enter the webservice URL and click **Test** to verify the connection
4. Use the **⚡ Solve** button in the header to submit cases

The webservice URL can also be set via environment variable:

```bash
GTOPT_WEBSERVICE_URL=http://my-server:3000 python app.py
```

URLs entered in the GUI must point at a loopback host, the host of
`GTOPT_WEBSERVICE_URL`, or a host listed in `GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS`.

## Security

The GUI service has **no user authentication**; it is a local, single-user
tool.

- It binds to `127.0.0.1` by default (`--host` / `GTOPT_GUI_HOST` to
  override).  For remote use, tunnel over SSH
  (`ssh -L 5001:127.0.0.1:5001 server`) or use an authenticating TLS reverse
  proxy.
- Flask debug mode (`--debug`, `FLASK_DEBUG=1`) is for local development only:
  the Werkzeug debugger allows code execution, and the service refuses to run
  in debug mode on a non-loopback host.
- `POST /api/solve/config` rejects non-`http(s)` URLs and hosts that are not
  loopback or allowlisted in `GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS` (SSRF guard).
- `POST /api/shutdown` requires a direct loopback client sending the
  `X-GTOpt-GUI` header (as the bundled UI does), or the secret configured in
  `GTOPT_GUI_SHUTDOWN_TOKEN`.
- `GTOPT_WEBSERVICE_API_TOKEN` is forwarded to the webservice as a bearer
  token.

See [GTOPT_GUISRV.md](GTOPT_GUISRV.md#security) for details.

## API Endpoints

| Method | Path                         | Description |
|--------|------------------------------|-------------|
| GET    | `/`                          | Main GUI page |
| GET    | `/api/schemas`               | Element field schemas |
| POST   | `/api/case/download`         | Generate and download case ZIP |
| POST   | `/api/case/upload`           | Upload a case ZIP for editing |
| POST   | `/api/case/preview`          | Preview the generated JSON |
| POST   | `/api/results/upload`        | Upload results ZIP for viewing |
| GET    | `/api/solve/config`          | Get webservice URL configuration |
| POST   | `/api/solve/config`          | Set webservice URL (loopback / allowlisted hosts only) |
| POST   | `/api/solve/submit`          | Submit case to webservice for solving |
| GET    | `/api/solve/status/<token>`  | Poll job status from webservice |
| GET    | `/api/solve/results/<token>` | Retrieve and parse results from webservice |
| GET    | `/api/solve/jobs`            | List all jobs from webservice |
| POST   | `/api/shutdown`              | Stop the service (loopback + `X-GTOpt-GUI`, or shutdown token) |

## Input Data Documentation

See [INPUT_DATA.md](../docs/input-data.md) for a comprehensive description of the GTOpt
input data structure, including all element types and their fields.

## Licence and source offer (AGPL)

The GUI service is part of the GTOpt core, licensed
`AGPL-3.0-or-later OR LicenseRef-gtopt-commercial` (see
[`LICENSING.md`](../LICENSING.md)). The "Source" link in the page header
points to the corresponding source. If you deploy a **modified** version
for users over a network, point that link at the source of your version,
or obtain a commercial licence.
