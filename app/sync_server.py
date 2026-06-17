"""Embedded sync listener for MoneyPilot Pocket (the phone capture app).

A tiny stdlib HTTP server started by the desktop app and alive only while it
runs — NOT a background service. The phone POSTs queued entries to /pocket/sync
over the user's private Tailscale HTTPS; this ingests them into the home ledger.
Bound to 127.0.0.1; `tailscale serve` is what exposes it to the tailnet.
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app import pocket

log = logging.getLogger("moneypilot.sync")

DEFAULT_PORT = 8788


def _make_handler(api, token):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _cors(self):
            # The PWA is served cross-origin (GitHub Pages) → CORS is required.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "authorization, content-type")
            # Tailscale IPs (100.64.0.0/10) are a PRIVATE range. Chrome's Private
            # Network Access blocks a public origin (github.io) from reaching a
            # private address unless the server opts in with this header — without
            # it the phone's fetch fails with a bare "Failed to fetch".
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            # Unauthenticated reachability probe so the phone can show "reachable".
            if self.path.rstrip("/") == "/pocket/ping":
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            parts = urlparse(self.path)
            if parts.path.rstrip("/") != "/pocket/sync":
                self._json(404, {"error": "not found"})
                return
            # Token from the Authorization header OR a ?t= query param. The query
            # form lets the phone send a CORS "simple request" (no preflight),
            # which some phone/proxy network paths handle when a preflight fails.
            auth = self.headers.get("Authorization", "")
            sent = (auth[7:] if auth.startswith("Bearer ") else "") \
                or (parse_qs(parts.query).get("t", [""])[0])
            if not (sent and hmac.compare_digest(sent, token)):
                self._json(401, {"error": "unauthorized"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                entries = data.get("entries") or []
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "bad request"})
                return
            try:
                with api._lock:
                    synced = pocket.ingest(api.conn, entries, api._today())
            except Exception as e:  # noqa: BLE001
                log.warning("pocket sync failed: %r", e)
                self._json(500, {"error": "ingest failed"})
                return
            self._json(200, {"synced": synced})

        def log_message(self, *args):
            pass  # don't spam stderr (the GUI has no console anyway)

    return Handler


def start(api, token, host="127.0.0.1", port=DEFAULT_PORT):
    """Start the listener on a daemon thread; returns the server (call
    .shutdown() to stop). Raises OSError if the port is taken."""
    httpd = ThreadingHTTPServer((host, port), _make_handler(api, token))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
