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
import subprocess
import sys
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
from .report import compose
from .geo import ENU, Origin, llh_from_enu
from .ingest import DEFAULT_PORT, SchemaViolation, UdpReceiver
from .pipeline import Pipeline

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
SIMULATOR_CONTROL = "http://127.0.0.1:5010"
STALE_VEHICLE_S = 8.0
"""How long a vehicle may go unheard before it leaves the fleet view.

Switching scenarios would otherwise leave the previous run's vehicle on
screen — and since the detail panels follow whichever vehicle is worst, the
console kept describing a drone that had finished flying while a truck was
running. Vehicles genuinely do go quiet, so this is also the right behaviour
in the field: no report for eight seconds and it is no longer current."""

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
        self.last_heard = 0.0

    def update(self, payload: dict) -> None:
        self.last_heard = time.monotonic()
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
        self.records: dict[str, str] = {}
        """Vehicle -> path of its evidence file, so a report can be written
        from the record rather than from whatever is on screen."""

        self.report_enabled = False
        """The switch. Off by default, because the claim being demonstrated is
        that detection does not depend on it."""

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
            self._drop_stale()

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

    def _drop_stale(self) -> None:
        """Forget vehicles that have stopped reporting."""
        now = time.monotonic()
        gone = [v for v, w in self.vehicles.items()
                if now - w.last_heard > STALE_VEHICLE_S]
        for vehicle_id in gone:
            del self.vehicles[vehicle_id]
            self.records.pop(vehicle_id, None)
            self.incidents = [i for i in self.incidents if i.vehicle_id != vehicle_id]
            if self.focus == vehicle_id:
                self.focus = None

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
                "report_enabled": self.report_enabled,
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

    def note_record(self, vehicle_id: str, path: str) -> None:
        with self.lock:
            self.records[vehicle_id] = path

    def clear(self) -> None:
        with self.lock:
            self.vehicles.clear()
            self.records.clear()
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
                    if recorder.path is not None:
                        shared.note_record(vid, str(recorder.path))
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


_FLEET: Optional[subprocess.Popen] = None
"""The fleet sender, when one is running. Held so a second start replaces it
rather than putting two fleets on the same port."""


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
        elif path == "/report":
            self._json(self._report())
        elif path == "/basemap":
            self._json(self._basemap())
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

    def _basemap(self) -> dict[str, Any]:
        """The world the vehicles are driving through, as lat/lon.

        Served from the simulator's own road network rather than a tile
        server. Two reasons, and the second is the one that matters.

        The demo runs with wifi off, and an online map fails silently — a grey
        rectangle with no error, in front of judges, at the worst moment.

        And these are the actual roads the truck is following. A real street
        map would be prettier and would not line up with anything: the truck
        would drive through buildings and the cargo-theft story would make no
        sense. Here, when the fake track runs neatly up the highway while the
        real truck sits at the warehouse, both of those places are on screen.
        """
        try:
            from simulator.roads import ROADS
            from simulator.vehicle import ORIGIN_ALT, ORIGIN_LAT, ORIGIN_LON
        except Exception:
            return {"roads": [], "places": []}

        origin = Origin(ORIGIN_LAT, ORIGIN_LON, ORIGIN_ALT)

        def to_llh(east: float, north: float) -> list[float]:
            lat, lon, _alt = llh_from_enu(ENU(east, north, 0.0), origin)
            return [lat, lon]

        roads = [
            {"name": name, "points": [to_llh(e, n) for e, n in points],
             "major": name.upper().startswith(("NH", "SH"))}
            for name, points in ROADS
        ]

        # Named ends of the network, so the story has landmarks rather than
        # coordinates. Taken from the roads themselves so they cannot drift
        # out of step with the simulator.
        places = []
        by_name = {name: points for name, points in ROADS}
        if "warehouse lane" in by_name:
            end = by_name["warehouse lane"][-1]
            places.append({"name": "Warehouse", "at": to_llh(*end), "kind": "building"})
        if "NH-544" in by_name:
            places.append({"name": "Depot", "at": to_llh(*by_name["NH-544"][0]),
                           "kind": "building"})
            places.append({"name": "NH-544 north", "at": to_llh(*by_name["NH-544"][-1]),
                           "kind": "waypoint"})
        return {"roads": roads, "places": places}

    def _report(self) -> dict[str, Any]:
        """Write up the focused vehicle's incident from its stored record.

        Reads a file the detector has already finished with. Nothing here can
        affect a verdict, which is the whole point of the switch.
        """
        with self.shared.lock:
            enabled = self.shared.report_enabled
            path = self.shared.records.get(self.shared.focus or "")
        if not enabled:
            return {"enabled": False, "report": None}
        if not path:
            return {"enabled": True, "report": None, "note": "no run recorded yet"}
        written = compose(Path(path), enabled=True)
        if written is None:
            return {"enabled": True, "report": None}
        return {"enabled": True, "report": {
            "title": written.title, "body": written.body,
            "generated_by": written.generated_by}}

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
        global _FLEET
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        if path == "/control/clear":
            self.shared.clear()
            self._json({"ok": True})
            return

        if path == "/control/fleet":
            # The fleet finale needs several vehicles at once, and the
            # simulator flies one per process from a fixed origin. Launching
            # the sender from here means the demo is one button rather than a
            # second terminal at the worst possible moment.
            if _FLEET is not None and _FLEET.poll() is None:
                _FLEET.terminate()
            self.shared.clear()
            _FLEET = subprocess.Popen(
                [sys.executable, "-m", "harness.send_fleet",
                 "--spoof", "3.0", "--spoof-at", "25", "--duration", "600"],
                cwd=str(CONSOLE_DIR.parent),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._json({"ok": True, "vehicles": 4})
            return

        if path == "/control/stopfleet":
            if _FLEET is not None and _FLEET.poll() is None:
                _FLEET.terminate()
            _FLEET = None
            self.shared.clear()
            self._json({"ok": True})
            return

        if path == "/report/toggle":
            with self.shared.lock:
                self.shared.report_enabled = not self.shared.report_enabled
                self.shared.version += 1
                enabled = self.shared.report_enabled
            self._json({"enabled": enabled})
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
