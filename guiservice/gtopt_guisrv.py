#!/usr/bin/env python3
"""
gtopt_guisrv - Web server launcher for the GTOpt GUI service.

This script starts the Flask guiservice as a web server (without opening
a browser), similar to how gtopt_websrv runs the web service.

Usage:
    gtopt_guisrv [options]

Options:
    --port PORT          Port for the GUI service (default: 5001)
    --host HOST          Host to bind to (default: 127.0.0.1; an access
                         token is required on non-loopback hosts)
    --debug              Run Flask in debug mode (LOCAL DEVELOPMENT ONLY;
                         refused on non-loopback hosts)
    --help               Show this help message

Environment Variables:
    GTOPT_GUI_PORT       GUI service port (default: 5001)
    GTOPT_GUI_HOST       Host to bind to (default: 127.0.0.1)

Security:
    The GUI service has no user accounts.  On 127.0.0.1 it is open; on
    another address it requires an access token (GTOPT_GUI_AUTH_TOKEN, or
    a generated one printed at start-up).  An SSH tunnel or an
    authenticating TLS reverse proxy remain the preferred remote access.  Never enable --debug outside local development: the
    Werkzeug debugger allows remote code execution.
"""

import argparse
import importlib.util
import ipaddress
import os
import sys
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"


def is_loopback_host(host):
    """Return True when *host* (hostname or IP literal) is loopback."""
    h = (host or "").strip().lower().strip("[]")
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def get_guiservice_dir():
    """Get the directory containing the guiservice app.py."""
    # This script might be in bin/, guiservice is in ../share/gtopt/guiservice/
    script_dir = Path(__file__).parent.resolve()

    # Check common installation locations
    possible_locations = [
        script_dir.parent / "share" / "gtopt" / "guiservice",
        script_dir,  # During development
        Path("/usr/local/share/gtopt/guiservice"),
        Path(sys.prefix) / "share" / "gtopt" / "guiservice",
    ]

    for location in possible_locations:
        app_py = location / "app.py"
        if app_py.exists():
            return location

    print("Error: Cannot find guiservice directory with app.py", file=sys.stderr)
    print("Searched locations:", file=sys.stderr)
    for loc in possible_locations:
        print(f"  {loc}", file=sys.stderr)
    sys.exit(1)


def check_dependencies():
    """Check if required Python packages are installed."""
    required = ["flask", "pandas", "pyarrow", "requests"]
    return all(importlib.util.find_spec(pkg) is not None for pkg in required)


def show_help():
    """Show help message."""
    print(__doc__)


def main():
    """Main entry point for gtopt_guisrv."""
    parser = argparse.ArgumentParser(
        description="Launch the GTOpt GUI service as a web server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  gtopt_guisrv                    # Start on default port 5001
  gtopt_guisrv --port 8080        # Use custom port
  gtopt_guisrv --debug            # Local development only (loopback)

The GUI service will be available at http://HOST:PORT/
For interactive kiosk mode, use 'gtopt_gui' instead.
        """,
    )

    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("GTOPT_GUI_PORT", 5001)),
        help="Port for the GUI service (default: 5001)",
    )

    parser.add_argument(
        "--host",
        default=os.environ.get("GTOPT_GUI_HOST", DEFAULT_HOST),
        help=(
            "Host to bind to (default: 127.0.0.1).  A non-loopback address "
            "requires an access token (GTOPT_GUI_AUTH_TOKEN, or a generated "
            "one printed at start-up)."
        ),
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Run Flask in debug mode with auto-reload.  LOCAL DEVELOPMENT "
            "ONLY: refused on non-loopback hosts (remote code execution)."
        ),
    )

    args = parser.parse_args()

    if args.debug and not is_loopback_host(args.host):
        parser.error(
            f"--debug is refused on non-loopback host '{args.host}': the Werkzeug "
            "debugger allows remote code execution (local development only)"
        )
    if not is_loopback_host(args.host):
        print(
            f"Warning: binding to non-loopback host '{args.host}'.  An access "
            "token is required (GTOPT_GUI_AUTH_TOKEN, or the generated login "
            "link printed below); prefer a TLS reverse proxy for remote use.",
            file=sys.stderr,
        )

    # Find guiservice directory
    guiservice_dir = get_guiservice_dir()
    print(f"Using guiservice from: {guiservice_dir}")

    # Check dependencies
    if not check_dependencies():
        print("Error: Required Python packages are not installed", file=sys.stderr)
        print("", file=sys.stderr)
        print("Please install the required packages:", file=sys.stderr)
        print(
            f"  python3 -m pip install -r {guiservice_dir}/requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    # The app reads its bind address from the environment (Host allowlist).
    os.environ["GTOPT_GUI_PORT"] = str(args.port)
    os.environ["GTOPT_GUI_HOST"] = args.host
    if args.debug:
        os.environ["FLASK_DEBUG"] = "1"

    print(f"Starting GTOpt GUI service on {args.host}:{args.port}...")
    print("")
    print("=" * 60)
    print("GTOpt GUI service is running")
    host_display = "localhost" if args.host in ("0.0.0.0", "::") else args.host  # noqa: S104 - display only
    print(f"URL: http://{host_display}:{args.port}")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    print("")

    try:
        # Run Flask app directly
        sys.path.insert(0, str(guiservice_dir))

        # Import and run the Flask app
        import app  # noqa: PLC0415  # pylint: disable=import-error,import-outside-toplevel

        # Token auth, security headers and waitress (when installed).
        app.run_server(host=args.host, port=args.port, debug=args.debug)

    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Error starting GUI service: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
