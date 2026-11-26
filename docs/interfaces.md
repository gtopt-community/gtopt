# GUI, Web Service and Terminal Dashboard

GTOpt has three optional interfaces on top of the `gtopt` binary:

- the **GUI service** (`gtopt_gui`, `gtopt_guisrv`): a browser interface to
  create, edit, solve and visualize cases;
- the **web service** (`gtopt_websrv`): a REST API and web page to upload
  cases, run the solver and download results;
- the **terminal dashboard** of [`run_gtopt`](scripts-guide.md#run_gtopt):
  a live view of a run (bounds, gap, iterations, resources, log).

The GUI and the web service bind to `127.0.0.1` by default and are
installed separately from the solver; `run_gtopt` comes with the Python
scripts.

## GUI service

A web-based graphical interface for creating, editing, and visualizing GTOpt
cases. The GUI service also connects to the GTOpt web service for remote
solving.

![GUI case editor, Topology view: the IEEE 9-bus case (cases/ieee_9b) with
its buses, lines and ratings, three generators, the solar profile of g3 and
the three demands](images/screenshots/gui_topology.png)

![GUI case editor, spreadsheet view of the nine lines of the IEEE 9-bus case
with their buses, reactances and ratings](images/screenshots/gui_case_editor.png)

![GUI results viewer: the generation of g1, g2 and the solar unit g3 over
24 hours for the IEEE 9-bus case, as a table and a chart; solved with
HiGHS](images/screenshots/gui_results.png)

*Screenshots: `cases/ieee_9b` solved with HiGHS through the GUI's Solve
button on a local web service; see [Regenerating the
screenshots](#regenerating-the-screenshots).*

### Quick Start with gtopt_gui

The easiest way to launch the GUI is with the `gtopt_gui` command, which provides
a complete integrated environment for editing, solving, and viewing optimization
cases:

```bash
# Install guiservice (independent of gtopt binary)
cmake -S guiservice -B build-gui
sudo cmake --install build-gui

# Install Python dependencies
pip3 install -r /usr/local/share/gtopt/guiservice/requirements.txt

# Launch interactively (opens browser, auto-starts webservice)
gtopt_gui

# Or with a specific configuration file
gtopt_gui system_c0.json
```

**Fully Integrated Workflow:**
- Opens a web browser with a standalone GUI interface
- Automatically starts local webservice (if installed) for solving cases
- Automatically detects GTOpt binary
- Complete workflow: Edit → Submit for Solving → Monitor → View Results
- Zero configuration required

**In the browser:**
1. Upload or create a case
2. Edit system elements (buses, generators, storage, demands)
3. Click "Solve" - runs on the auto-started local webservice
4. Monitor job progress in real-time
5. Click "Load Results" to view results with interactive charts and tables

For detailed usage and options, see [guiservice/GTOPT_GUI.md](../guiservice/GTOPT_GUI.md).

### GUI Plus (Next.js / React front-end)

An alternative modern front-end — **GUI Plus** — is available under
[`guiservice-plus/`](../guiservice-plus/). It is a Next.js 15 + React 19 +
Tailwind v4 + shadcn/ui single-page app that sits on top of the existing
Flask `guiservice` and offers an interactive topology editor (ReactFlow),
technology-coloured dispatch stack charts, SDDP convergence views, a merit
order table, and a KPI dashboard.

```bash
# Launch both the Flask guiservice and GUI Plus together
gtopt_gui --ui-plus --ui-plus-port 5002

# Or run it standalone against an existing Flask guiservice
cd guiservice-plus && npm install
GTOPT_GUISERVICE_URL=http://localhost:5001 npm run dev -- -p 5002
```

See [guiservice-plus/README.md](../guiservice-plus/README.md) for details.

### Quick Start with gtopt_guisrv

For running the GUI service as a web server (without opening a browser):

```bash
# Start GUI service on default port 5001
gtopt_guisrv

# Or with custom options
gtopt_guisrv --port 8080
```

The service binds to `127.0.0.1` by default. `--debug` is for local
development only and is refused on non-loopback addresses.

### Access token (non-loopback binds)

On `127.0.0.1` the GUI is open, for single-user local use.  When it
listens on another address (`--host` / `GTOPT_GUI_HOST`) an **access
token** is required (`GTOPT_GUI_AUTH=auto`, the default):

```bash
export GTOPT_GUI_AUTH_TOKEN=$(openssl rand -hex 32)   # or let it generate one
gtopt_guisrv --host 0.0.0.0
# open http://<server>:5001/?token=<token> once; a session cookie is set
```

Without `GTOPT_GUI_AUTH_TOKEN` a random token is generated at start-up
and a login link (`/?token=...`) is printed, as Jupyter does.  Scripted
clients send `Authorization: Bearer <token>` (or `X-GTOpt-Auth`).
`GTOPT_GUI_AUTH=on` requires the token on loopback too (shared hosts);
`off` disables it (only behind an authenticating reverse proxy).  An SSH
tunnel (`ssh -L 5001:127.0.0.1:5001 server`) keeps the service on
loopback instead.  See the Security section of
[guiservice/GTOPT_GUISRV.md](../guiservice/GTOPT_GUISRV.md#security).

For detailed usage and systemd service setup, see [guiservice/GTOPT_GUISRV.md](../guiservice/GTOPT_GUISRV.md).

### Manual Start

You can also run the GUI service directly:

```bash
pip install -r guiservice/requirements.txt
cd guiservice
python app.py
# Open http://localhost:5001 in your browser
```

For detailed installation and deployment instructions, see
[guiservice/INSTALL.md](../guiservice/INSTALL.md).

## Web service

gtopt includes a web service that lets you upload optimization cases, run the
solver, and download results — all through a browser or REST API.

<p align="center">
  <img src="images/screenshots/webservice_jobs.png" width="560"
       alt="Web service page: the case upload form, the job token returned after
       submitting cases/c0, and the job list with the job completed and ready to
       download">
</p>

### Quick Start with gtopt_websrv

Install and run the web service using the `gtopt_websrv` command:

```bash
# Install webservice (independent of gtopt binary)
cmake -S webservice -B build-web
sudo cmake --install build-web

# Install Node.js dependencies (including build dependencies)
cd /usr/local/share/gtopt/webservice
npm install
npm run build

# Launch the web service
gtopt_websrv

# Or with options
gtopt_websrv --port 8080
gtopt_websrv --gtopt-bin /path/to/gtopt
```

Binding a non-loopback address (`--hostname` / `GTOPT_WEBSERVICE_HOST`)
requires `GTOPT_WEBSERVICE_API_TOKEN`; clients then send
`Authorization: Bearer <token>` (see
[webservice/INSTALL.md](../webservice/INSTALL.md)).

### REST API example

The same workflow from the command line, with `curl` (the job token is
returned by the first call):

```bash
# Zip a case and submit it
(cd cases/c0 && zip -r /tmp/c0.zip system_c0.json system_c0)
curl -F file=@/tmp/c0.zip -F systemFile=system_c0.json \
  http://localhost:3000/api/jobs
# {"token":"<TOKEN>","status":"pending","message":"Job submitted successfully. ..."}

# Poll the job status (pending → running → completed / failed)
curl http://localhost:3000/api/jobs/<TOKEN>

# Download the results as a zip
curl -o c0-results.zip http://localhost:3000/api/jobs/<TOKEN>/download
```

Other endpoints: `GET /api/jobs` (list jobs), `GET /api/jobs/<TOKEN>/logs`,
`GET /api/jobs/<TOKEN>/monitor`, `POST /api/jobs/<TOKEN>/stop` and
`GET /api/ping`.

For detailed installation and deployment instructions, see [webservice/INSTALL.md](../webservice/INSTALL.md).

## Terminal dashboard (run_gtopt)

In an interactive terminal [`run_gtopt`](scripts-guide.md#run_gtopt)
shows a live dashboard while gtopt runs: run status, lower / upper bound
and gap, the progress of the gap towards the convergence tolerance (with
the SDDP pass and phase being solved), solver statistics, the iteration
history, CPU and memory use and the colour-coded solver log.

<p>
  <img src="images/screenshots/run_gtopt_tui.png" width="49%"
       alt="run_gtopt dashboard view during an SDDP run solved with HiGHS
       (iteration 6 of 100, forward pass): header with lower and upper bound and
       gap, plan steps, gap progress towards the tolerance, solver statistics,
       iteration history table, CPU and memory bars and the solver log">
  <img src="images/screenshots/run_gtopt_tui_convergence.png" width="49%"
       alt="run_gtopt convergence view after convergence: sparklines of the
       lower bound, upper bound and gap, a per-scene cost heatmap by iteration
       and per-scene cost trends">
</p>

*Frames from an 8-scenario, 24-phase SDDP hydro-cascade run (built from
`cases/hydro_valley_sddpjl`) solved with HiGHS: the dashboard at
iteration 6, the convergence view after convergence.*

| Key | Action |
|-----|--------|
| `1` / `2` / `3`, `Tab` | Dashboard / SDDP phase grid / per-scene convergence view; Tab cycles |
| `s` | Graceful stop: SDDP / cascade finish the current iteration and save cuts |
| `q` | Quit: terminate gtopt (asks y/N once the run is older than a minute; with `--attach` it only detaches) |
| `i` | System statistics overlay (buses, generators, … from the planning JSON) |
| `o` | Paths overlay: output directory, gtopt log, status file, error LPs and a **Charts** row with the `gtopt_monitor --status-file …` command that plots the same status file |
| `h` / `?` | Help overlay; `Esc` closes any overlay |
| `j` / `k`, `J` / `K`, `g` / `G` (arrows, PgUp/PgDn, Home/End) | Grid view: scroll scenes by 1, by a page, to the first / last |

Ctrl-C works as `s` the first time, then terminates and finally kills
gtopt.  Below 100 columns or 40 rows a compact layout is used;
`--ui plain` (or a non-terminal stdout) streams the log instead.

### Watching a run

```bash
run_gtopt --list           # live gtopt runs (from the run registry)
run_gtopt --attach [PID]   # this dashboard on an already-running gtopt
gtopt_monitor --status-file <output>/solver_status.json
                           # charts of the same status file (matplotlib;
                           # --no-gui prints one line per poll)
```

See [scripts-guide.md](scripts-guide.md#run_gtopt) for all options.

## Regenerating the screenshots

All six images are produced by
[`tools/make_interface_screenshots.py`](../tools/make_interface_screenshots.py)
with an open-source solver (HiGHS by default):

```bash
python -m venv ~/venvs/shots && . ~/venvs/shots/bin/activate
pip install -e ./scripts playwright -r guiservice/requirements.txt
(cd webservice && npm ci && npm run build)
python tools/make_interface_screenshots.py --gtopt build/standalone/gtopt
# or one part: ... tui | webservice | gui
```

- **tui**: builds the SDDP demo case
  ([`tools/figures/make_sddp_demo_case.py`](../tools/figures/make_sddp_demo_case.py)),
  solves it, replays its log through the dashboard controller on a clock
  driven by the log timestamps, records the frame with Rich
  (`Console(record=True).export_svg()`, 120 columns) and rasterises it
  with headless Chrome.  Absolute paths are removed from the log lines.
- **webservice** / **gui**: start both services on `127.0.0.1` (job and
  state directories in a scratch directory), drive them with Playwright
  (system Chrome when present) at a 1440×900 viewport, 2× pixel ratio:
  `cases/c0` is submitted on the web service page; `cases/ieee_9b` is
  uploaded to the GUI, solved with its Solve button and its generator
  dispatch opened in the results viewer.

PNGs are shrunk with `pngquant` / `oxipng` when installed.
