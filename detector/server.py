"""Stage 10 — serve the console and stream the detector's state to it.

    python -m detector.server              # then open http://127.0.0.1:8080

Server-Sent Events over plain HTTP rather than a WebSocket. The stream is
one-way — the detector talks, the browser listens — so a WebSocket buys
nothing here, and SSE needs only the standard library where a WebSocket needs
either a dependency or a hundred lines of hand-rolled frame encoding to go
wrong on demo day. See docs/schema.md.

Two threads: one drains the UDP socket and updates shared state, one serves
HTTP. The detector loop must never wait on a browser, or a slow client would
start dropping sensor frames.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from .ingest import DEFAULT_PORT, SchemaViolation, UdpReceiver
from .pipeline import Pipeline

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
SIMULATOR_CONTROL = "http://127.0.0.1:5010"
TRAIL_LIMIT = 4000
"""Points kept per path. At 5 Hz that is over ten minutes — long enough for any
demo, bounded so a forgotten run cannot eat memory."""


class Shared:
    """State the UDP thread writes and HTTP threads read."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.latest: Optional[dict[str, Any]] = None
        self.raw: Optional[dict[str, Any]] = None
        self.gnss_trail: list[tuple[float, float]] = []
        self.witness_trail: list[tuple[float, float]] = []
        self.run_id: Optional[str] = None
        self.violation: Optional[str] = None
        self.version = 0

    def update(self, payload: dict[str, Any], raw: dict[str, Any]) -> None:
        with self.lock:
            if payload["run_id"] != self.run_id:
                self.run_id = payload["run_id"]
                self.gnss_trail.clear()
                self.witness_trail.clear()
                self.violation = None
            if payload.get("gnss") and payload.get("witness", {}).get("e") is not None:
                self.gnss_trail.append((payload["gnss"]["e"], payload["gnss"]["n"]))
                self.witness_trail.append((payload["witness"]["e"], payload["witness"]["n"]))
                del self.gnss_trail[:-TRAIL_LIMIT]
                del self.witness_trail[:-TRAIL_LIMIT]
            self.latest = payload
            self.raw = raw
            self.version += 1

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "version": self.version,
                "state": self.latest,
                "raw": self.raw,
                "violation": self.violation,
                "trails": {
                    "gnss": self.gnss_trail[-TRAIL_LIMIT:],
                    "witness": self.witness_trail[-TRAIL_LIMIT:],
                },
            }

    def fail(self, message: str) -> None:
        with self.lock:
            self.violation = message
            self.version += 1

    def clear(self) -> None:
        with self.lock:
            self.latest = None
            self.raw = None
            self.run_id = None
            self.violation = None
            self.gnss_trail.clear()
            self.witness_trail.clear()
            self.version += 1


def detector_loop(shared: Shared, port: int, vehicle_type: Optional[str]) -> None:
    receiver = UdpReceiver(port=port)
    pipeline = Pipeline(vehicle_type)
    while True:
        try:
            for message in receiver.messages():
                try:
                    state = pipeline.accept(message)
                except SchemaViolation as exc:
                    # Surfaced to the console rather than killed silently: a
                    # forbidden field invalidates the demo and the operator
                    # needs to see that, loudly.
                    shared.fail(str(exc))
                    pipeline = Pipeline(vehicle_type)
                    continue
                if state is not None:
                    shared.update(state.to_json(), message)
        except OSError:
            time.sleep(0.5)


def forward_to_simulator(path: str, body: bytes | None = None,
                         method: str = "POST") -> tuple[int, bytes]:
    """Pass a control request through to the simulator.

    The console talks only to us, so the simulator does not need to be
    reachable from the browser, and there is one place to report it being
    absent — which it will be until phase 4.
    """
    request = urllib.request.Request(
        f"{SIMULATOR_CONTROL}{path}",
        data=(body or b"{}") if method == "POST" else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 503, json.dumps({
            "error": "simulator not reachable on port 5010",
            "hint": "start it, or send frames with: python -m harness.send_fixture",
        }).encode()


class Handler(BaseHTTPRequestHandler):
    shared: Shared = Shared()
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass  # one line per frame would bury the detector's own output

    # --- GET ---------------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/stream":
            self._stream()
        elif path in ("/", "/index.html"):
            self._static("index.html")
        elif path == "/snapshot":
            self._json(self.shared.snapshot())
        elif path.startswith("/control/"):
            # The console asks the simulator what scenarios it has rather than
            # carrying its own list. A judge picking from a list that came out
            # of the other process is part of the argument that nothing here
            # is staged.
            status, payload = forward_to_simulator(
                path[len("/control"):], method="GET"
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self._static(path.lstrip("/"))

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_version = -1
        try:
            while True:
                snapshot = self.shared.snapshot()
                if snapshot["version"] != last_version:
                    last_version = snapshot["version"]
                    payload = json.dumps(snapshot, separators=(",", ":"))
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                else:
                    # Keep-alive comment so a proxy or an idle browser does not
                    # quietly drop the connection between runs.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the tab was closed or reloaded

    def _static(self, name: str) -> None:
        target = (CONSOLE_DIR / name).resolve()
        if not str(target).startswith(str(CONSOLE_DIR.resolve())) or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # --- POST --------------------------------------------------------------

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        if path == "/control/clear":
            self.shared.clear()
            self._json({"ok": True})
            return

        if path.startswith("/control/"):
            status, payload = forward_to_simulator(path[len("/control"):], body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_error(404)

    def _json(self, obj: Any) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SensorSentry console server")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--vehicle-type", default=None)
    args = parser.parse_args(argv)

    shared = Shared()
    Handler.shared = shared

    thread = threading.Thread(
        target=detector_loop, args=(shared, args.udp_port, args.vehicle_type), daemon=True
    )
    thread.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.http_port), Handler)
    print(f"detector listening on UDP {args.udp_port}")
    print(f"console at http://127.0.0.1:{args.http_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
