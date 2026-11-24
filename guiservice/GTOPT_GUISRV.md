# gtopt_guisrv - GUI Service Web Server Launcher

A command-line launcher for the GTOpt GUI service that runs it as a web server
(without automatically opening a browser), similar to how `gtopt_websrv` runs
the webservice.

## Difference from gtopt_gui

- **gtopt_gui**: Interactive launcher that opens a browser in kiosk/app mode for a standalone GUI experience
- **gtopt_guisrv**: Web server launcher that runs the Flask app as a daemon/service without opening a browser

## Installation

The `gtopt_guisrv` command is installed when you install the guiservice:

```bash
# Install guiservice
cmake -S guiservice -B build-gui
sudo cmake --install build-gui

# Install Python dependencies
pip3 install -r /usr/local/share/gtopt/guiservice/requirements.txt
```

After installation:
- `gtopt_gui` launcher at `/usr/local/bin/gtopt_gui` (interactive mode)
- `gtopt_guisrv` launcher at `/usr/local/bin/gtopt_guisrv` (web server mode)
- GUI service files at `/usr/local/share/gtopt/guiservice`

## Requirements

- Python 3.10 or later
- Python packages: Flask, pandas, pyarrow, requests (see `requirements.txt`)

## Usage

### Basic usage

Launch the GUI service as a web server on default port 5001:

```bash
gtopt_guisrv
```

The service will be available at `http://localhost:5001`.

### With options

```bash
# Use custom port
gtopt_guisrv --port 8080

# Bind to a specific host (default: 127.0.0.1, loopback only)
gtopt_guisrv --host 127.0.0.1
```

### Environment Variables

You can also use environment variables:

```bash
# Set port
GTOPT_GUI_PORT=8080 gtopt_guisrv

# Bind address (default: 127.0.0.1)
GTOPT_GUI_HOST=127.0.0.1 gtopt_guisrv
```

## Options

```
gtopt_guisrv [options]

Options:
  --port PORT          Port for the GUI service (default: 5001)
  --host HOST          Host to bind to (default: 127.0.0.1)
  --debug              Flask debug mode — LOCAL DEVELOPMENT ONLY; refused on
                       non-loopback hosts (the debugger allows code execution)
  --help               Show help message
```

## Systemd Service

A systemd service file template is included for running the GUI service as a
system daemon.

### Installation

```bash
# Copy the service file to systemd
sudo cp /usr/local/share/gtopt/guiservice/gtopt-guiservice.service \
        /etc/systemd/system/

# Edit the service file if needed (e.g., change port, user, paths)
sudo nano /etc/systemd/system/gtopt-guiservice.service

# Reload systemd
sudo systemctl daemon-reload

# Enable the service to start on boot
sudo systemctl enable gtopt-guiservice

# Start the service
sudo systemctl start gtopt-guiservice

# Check status
sudo systemctl status gtopt-guiservice
```

### Service Configuration

The default service file uses these settings:
- Port: 5001
- User: GTOpt (create this user first)
- Working directory: /usr/local/share/gtopt/guiservice

Edit `/etc/systemd/system/gtopt-guiservice.service` to customize these settings.

## Features

The GUI service provides:
- **Case Editor**: Create and edit GTOpt cases through a web interface
- **File Upload**: Upload existing cases as ZIP files
- **Data Management**: Edit system elements, simulation parameters, and options
- **Visualization**: View input data and configuration
- **Results Viewing**: Upload and visualize optimization results
- **Webservice Integration**: Connect to remote GTOpt webservice for solving

## Comparison with gtopt_gui

| Feature | gtopt_gui | gtopt_guisrv |
|---------|-----------|--------------|
| Opens browser automatically | ✓ | ✗ |
| Kiosk/app mode | ✓ | ✗ |
| Runs as web server | ✓ | ✓ |
| Systemd service support | ✗ | ✓ |
| Remote access | Limited | Full |
| Use case | Interactive local use | Server deployment |

## Examples

### Basic workflow

```bash
# 1. Start the GUI service
gtopt_guisrv

# 2. Access from browser
# Open http://localhost:5001 in your browser

# 3. Upload and edit cases through the web interface
```

### Production deployment

```bash
# Install as system service
sudo cp /usr/local/share/gtopt/guiservice/gtopt-guiservice.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gtopt-guiservice
sudo systemctl start gtopt-guiservice

# Monitor logs
sudo journalctl -u gtopt-guiservice -f

# Stop service
sudo systemctl stop gtopt-guiservice
```

### Remote access

```bash
# The GUI service has no authentication: keep it on loopback and tunnel
# through SSH from your workstation
ssh -L 5001:127.0.0.1:5001 your-server
# then open http://localhost:5001 locally

# Exposing it directly (--host 0.0.0.0) is only acceptable behind an
# authenticating TLS reverse proxy — see "Security" below.
```

## Troubleshooting

### "Error: Required Python packages are not installed"

Install the dependencies:

```bash
pip3 install -r /usr/local/share/gtopt/guiservice/requirements.txt
```

### Port already in use

Use a different port:

```bash
gtopt_guisrv --port 5002
```

Or check what's using the port:

```bash
lsof -i :5001
```

### Service won't start (systemd)

Check the logs:

```bash
sudo journalctl -u gtopt-guiservice -n 50
```

Common issues:
- User 'GTOpt' doesn't exist (create it first)
- Python dependencies not installed
- Port already in use

## Platform Support

- ✅ Ubuntu/Debian
- ✅ macOS
- ✅ WSL
- ✅ Windows (via gtopt_guisrv.bat)

## See Also

- [GTOPT_GUI.md](GTOPT_GUI.md) - Interactive kiosk mode launcher
- [README.md](README.md) - Full GUI service documentation
- [INSTALL.md](INSTALL.md) - Detailed installation guide
- [../webservice/GTOPT_WEBSRV.md](../webservice/GTOPT_WEBSRV.md) - Similar launcher for webservice

## Security

The GUI service has **no user authentication**; treat it as a local,
single-user tool.

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
