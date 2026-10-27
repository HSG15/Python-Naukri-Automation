#!/usr/bin/env python3
"""
track_profile.py — Naukri Profile Performance, Search Appearance & Recruiter Tracker

Usage:
  python track_profile.py               # Fetch latest metrics, update history, print terminal report
  python track_profile.py --dashboard   # Start interactive graphical web dashboard & open in browser
  python track_profile.py --serve       # Start background dashboard server on http://localhost:5050
  python track_profile.py --open        # Open existing dashboard.html in browser
"""

import os
import sys
import json
import argparse
import logging
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.client.naukri_client import NaukriLoginClient
from src.tracker.profile_tracker import (
    fetch_profile_metrics,
    save_daily_snapshot,
    generate_dashboard_html,
    format_terminal_dashboard,
    HISTORY_FILE,
    DASHBOARD_HTML_FILE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("track_profile")

load_dotenv()


def sync_naukri_data():
    """Authenticate and fetch latest metrics from Naukri, then update history and HTML."""
    username = os.getenv("NAUKRI_USERNAME")
    password = os.getenv("NAUKRI_PASSWORD")

    if not username or not password:
        raise ValueError("Missing NAUKRI_USERNAME or NAUKRI_PASSWORD in .env file.")

    log.info("Logging into Naukri as %s...", username)
    client = NaukriLoginClient(username, password)
    client.login()

    log.info("Fetching search appearance & recruiter engagement metrics...")
    metrics = fetch_profile_metrics(client)

    log.info("Saving daily tracker snapshot...")
    history = save_daily_snapshot(metrics, HISTORY_FILE)

    log.info("Generating updated interactive dashboard.html...")
    generate_dashboard_html(history, DASHBOARD_HTML_FILE)

    return metrics, history


class TrackerServerHandler(SimpleHTTPRequestHandler):
    """Custom handler to serve dashboard.html and handle /api/sync endpoint."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/dashboard", "/dashboard.html"):
            self.path = "/" + DASHBOARD_HTML_FILE
            return super().do_GET()
        elif parsed.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.wfile.write(b"{}")
            return
        elif parsed.path == "/api/sync":
            self.handle_sync()
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/sync":
            self.handle_sync()
            return
        self.send_response(404)
        self.end_headers()

    def handle_sync(self):
        log.info("Live sync requested via Web Dashboard...")
        try:
            _, history = sync_naukri_data()
            payload = json.dumps(history).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            log.info("Live sync completed successfully.")
        except Exception as e:
            log.error("Sync failed: %s", e)
            err_payload = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_payload)

    def log_message(self, format, *args):
        # Cleaner console logs
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))


def run_web_dashboard(port: int = 5050, auto_open: bool = True):
    """Start local web server and open the graphical dashboard."""
    # Ensure dashboard files exist first
    if not os.path.exists(HISTORY_FILE) or not os.path.exists(DASHBOARD_HTML_FILE):
        log.info("Initial metrics not found. Fetching now...")
        sync_naukri_data()

    server_address = ("", port)
    try:
        httpd = HTTPServer(server_address, TrackerServerHandler)
    except OSError as err:
        if "Address already in use" in str(err):
            port += 1
            httpd = HTTPServer(("", port), TrackerServerHandler)
        else:
            raise

    url = f"http://localhost:{port}"
    print("\n" + "=" * 65)
    print(f"  🚀 NAUKRI INTERACTIVE DASHBOARD RUNNING")
    print(f"  🔗 URL: {url}")
    print(f"  ⚡ Live Sync: Active (click 'Sync Live' button in UI)")
    print(f"  Press Ctrl+C to stop the server.")
    print("=" * 65 + "\n")

    if auto_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server...")
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="Track Naukri profile search appearance, recruiter actions, and performance."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dashboard",
        action="store_true",
        help="Start the interactive web dashboard on localhost and open in browser",
    )
    group.add_argument(
        "--serve",
        action="store_true",
        help="Start the local dashboard web server without auto-opening",
    )
    group.add_argument(
        "--open",
        action="store_true",
        help="Open the existing static dashboard.html in your default web browser",
    )
    group.add_argument(
        "--sync",
        action="store_true",
        help="Force sync live metrics with Naukri and print terminal report",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5050,
        help="Custom port for the dashboard server (default: 5050)",
    )

    args = parser.parse_args()

    if args.open:
        abs_path = os.path.abspath(DASHBOARD_HTML_FILE)
        if not os.path.exists(abs_path):
            log.info("dashboard.html does not exist yet. Syncing now...")
            sync_naukri_data()
        log.info("Opening dashboard in browser: %s", abs_path)
        webbrowser.open("file://" + abs_path)
        return

    if args.dashboard:
        run_web_dashboard(port=args.port, auto_open=True)
        return

    if args.serve:
        run_web_dashboard(port=args.port, auto_open=False)
        return

    # Default action: sync and print terminal report
    metrics, history = sync_naukri_data()
    today_str = metrics.get("date")
    deltas = history.get("daily_snapshots", {}).get(today_str, {}).get("deltas", {})
    report = format_terminal_dashboard(metrics, deltas)
    print(report)
    print(f"💡 Tip: Run 'python track_profile.py --dashboard' to view the interactive graphical dashboard!\n")


if __name__ == "__main__":
    main()
