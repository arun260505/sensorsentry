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

from fleet.advisory import VehicleState, advise
from fleet.cluster import Incident, find_zones

from .evidence import Recorder
from .ingest import DEFAULT_PORT, SchemaViolation, UdpReceiver
from .pipeline import Pipeline

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
SIMULATOR_CONTROL = "http://127.0.0.1:5010"
TRAIL_LIMIT = 4000
"""Points kept per path. At 5 Hz that is over ten minutes — long enough for any
demo, bounded so a forgotten run cannot eat memory."""


class VehicleView:
    """Everything the console needs about one vehicle."""

    def __init__(self, vehicle_id: str) -> None:
        self.vehicle_id = vehicle_id
        self.latest = None
        self.gnss_trail: list[tuple[float, float]] = []
        self.witness_trail: list[tuple[float, float]] = []
        self.run_id = None

    def update(self, payload: dict) -> None:
        if payload["run_id"] != self.run_id:
            self.run_id = payload["run_id"]
            self.gnss_trail.clear()
            self.witness_trail.clear()
        gnss = payload.get("gnss") or {}
        witness = payload.get("witness") or {}
        # Stored as lat/lon, not local metres. Each vehicle anchors its own
        # origin, so its metres mean nothing to any other vehicle — a fleet
        # map built from them draws everyone on top of everyone else.
        if gnss.get("lat") is not None and witness.get("lat") is not None:
            self.gnss_trail.append((gnss["lat"], gnss["lon"]))
            self.witness_trail.append((witness["lat"], witness["lon"]))
            del self.gnss_trail[:-TRAIL_LIMIT]
            del self.witness_trail[:-TRAIL_LIMIT]
        self.latest = payload

    def trails(self) -> dict:
        return {"gnss": self.gnss_trail[-TRAIL_LIMIT:],
                "witness": self.witness_trail[-TRAIL_LIMIT:]}


class Shared:
    """State the UDP thread writes and HTTP threads read.

    Keyed by vehicle, because one attacker hits several at once and locating
    him is the whole point of the fleet view. A single-vehicle console can
    never show the thing that makes this product different.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.vehicles: dict[str, VehicleView] = {}
        self.focus = None
        """Which vehicle the detail panels describe. The first one seen, unless
        one is under attack — then that is what the operator wants."""

        self.raw = None
        self.violation = None
        self.incidents: list[Incident] = []
        self.zones: list = []
        self.advisories: list = []
        self.version = 0

    def update(self, payload: dict, raw: dict) -> None:
        with self.lock:
            vid = payload["vehicle_id"]
            view = self.vehicles.get(vid)
            if view is None:
                view = self.vehicles[vid] = VehicleView(vid)
            view.update(payload)

            self._record_incident(payload)
            self._recompute()

            if self.focus is None or self.focus not in self.vehicles:
                self.focus = vid
            attacked = [v for v, w in self.vehicles.items()
                        if w.latest and w.latest.get("state") == "ALERT"]
            if attacked and self.focus not in attacked:
                self.focus = attacked[0]

            self.raw = raw
            self.version += 1

    def _record_incident(self, payload: dict) -> None:
        """Keep one report per vehicle: the newest.

        A vehicle alerting every frame must not outvote three other vehicles
        when the zone is worked out.
        """
        blame = payload.get("blame") or {}
        cause = payload.get("cause") or {}
        nav = payload.get("navigation")
        if (payload.get("state") != "ALERT" or not nav
                or not blame.get("guilty") or blame["guilty"] == "cannot_isolate"
                or cause.get("label") in (None, "unclassified")):
            return

        vid = payload["vehicle_id"]
        self.incidents = [i for i in self.incidents if i.vehicle_id != vid]
        self.incidents.append(Incident(
            vehicle_id=vid, t=payload["t"],
            # The vehicle's own estimate, never the reported fix — under a
            # spoof the reported fix is the attacker's choice.
            lat=nav["lat"], lon=nav["lon"],
            guilty=blame["guilty"], cause=cause["label"],
            confidence=float(cause.get("confidence") or 0.0),
        ))

    def _recompute(self) -> None:
        self.zones = find_zones(self.incidents)
        states = []
        for vid, view in self.vehicles.items():
            payload = view.latest
            nav = (payload or {}).get("navigation")
            if not nav:
                continue
            witness = payload.get("witness") or {}
            states.append(VehicleState(
                vehicle_id=vid, lat=nav["lat"], lon=nav["lon"],
                speed_mps=float(witness.get("speed_mps") or 0.0),
                under_attack=payload.get("state") == "ALERT",
            ))
        self.advisories = advise(states, self.zones)

    def snapshot(self) -> dict:
        with self.lock:
            focus = self.vehicles.get(self.focus) if self.focus else None
            return {
                "version": self.version,
                # Flat, for the single-vehicle detail panels.
                "state": focus.latest if focus else None,
                "trails": focus.trails() if focus else {"gnss": [], "witness": []},
                "raw": self.raw,
                "violation": self.violation,
                "focus": self.focus,
                "vehicles": {
                    v: {"state": w.latest, "trails": w.trails()}
                    for v, w in self.vehicles.items()
                },
                "zones": [
                    {"lat": z.lat, "lon": z.lon, "radius_m": round(z.radius_m),
                     "vehicles": list(z.vehicles),
                     "confidence": round(z.confidence, 2),
                     "describe": z.describe()}
                    for z in self.zones
                ],
                "advisories": [
                    {"vehicle_id": a.vehicle_id, "distance_m": round(a.distance_m),
                     "seconds_away": (None if a.seconds_away is None
                                      else round(a.seconds_away)),
                     "inside": a.inside, "message": a.message()}
                    for a in self.advisories
                ],
            }

    def fail(self, message: str) -> None:
        with self.lock:
            self.violation = message
            self.version += 1

    def clear(self) -> None:
        with self.lock:
            self.vehicles.clear()
            self.focus = None
            self.raw = None
            self.violation = None
            self.incidents.clear()
            self.zones = []
            self.advisories = []
            self.version += 1


def detector_loop(shared: Shared, port: int, vehicle_type: Optional[str]) -> None:
    """One detector per vehicle, all reading the same socket.

    Vehicles are told apart by `vehicle_id`, so several simulators can share
    the port and each still gets its own independent pipeline. That
    independence matters: the fleet argument only holds if the vehicles really
    did reach their conclusions separately.
    """
    receiver = UdpReceiver(port=port)
    pipelines: dict[str, Pipeline] = {}
    recorders: dict[str, Recorder] = {}

    while True:
        try:
            for message in receiver.messages():
                vid = message.get("vehicle_id")
                if not vid:
                    continue
                if vid not in pipelines:
                    pipelines[vid] = Pipeline(vehicle_type)
                    recorders[vid] = Recorder()
                recorder = recorders[vid]
                if message.get("type") == "run_start":
                    recorder.note_run(message)
                else:
                    recorder.note_frame(message)
                try:
                    state = pipelines[vid].accept(message)
                except SchemaViolation as exc:
                    # Surfaced to the console rather than killed silently: a
                    # forbidden field invalidates the demo and the operator
                    # needs to see that, loudly.
                    shared.fail(str(exc))
                    pipelines.pop(vid, None)
                    continue
                if state is not None:
                    payload = state.to_json()
                    recorder.note_state(payload)
                    shared.update(payload, message)
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
