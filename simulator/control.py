"""
control.py — HTTP control server for the SensorSentry simulator.

Listens on port 5010. The console (and any operator tool) calls these
endpoints to start runs, inject attacks, and reset.

Endpoints
---------
POST /start      {"scenario": "drone_clean"}           start a run
POST /reset      —                                     stop + clear
POST /inject     {"kind","type","strength","bearing_deg"}  inject now
GET  /scenarios  —                                     list scenario names

Design notes
------------
- Runs in a background daemon thread so the simulator loop can be the
  main thread (or vice versa).
- Uses threading.Event for clean shutdown within 2 seconds.
- The injector state is shared via a simple lock-protected slot.
- Inject takes effect on the very next frame (run loop reads the slot).
- Changing scenario does not require a restart: POST /reset then /start.
"""

import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

from .scenarios import list_scenarios, get_scenario
from .attacks import make_attack
from .faults import make_fault
from .interference import make_interference

PORT = 5010


# ---------------------------------------------------------------------------
# Shared simulation state
# ---------------------------------------------------------------------------
class SimState:
    """
    Thread-safe container for the live simulation state.

    The run loop reads `.injector` each frame; control.py writes it.
    """

    def __init__(self):
        self._lock       = threading.Lock()
        # slot -> [injector, t_start, kind]. A slot is what the judge is
        # holding: "attack" for the GPS, "interference:magnet" for the
        # compass, "fault:baro" for a broken barometer. Keyed this way rather
        # than by kind so picking up a second weapon does not put the first
        # one down — two hands on the vehicle at once is the hardest case we
        # have, and the one worth letting them try.
        self._injectors: dict = {}
        self._stop_event = threading.Event()
        self._run_thread = None
        self._scenario   = None
        self._seed       = None

    # -- Injector access (read by run loop) --
    def injectors(self):
        """Every live injector as (injector, kind, t_start, slot)."""
        with self._lock:
            return [(inj, kind, t0, slot)
                    for slot, (inj, t0, kind) in self._injectors.items()]

    def get_injector(self, slot: str):
        """The injector in a slot, or the first one of that kind."""
        with self._lock:
            entry = self._injectors.get(slot)
            if entry:
                return entry[0]
            for held_slot, (inj, _t0, kind) in self._injectors.items():
                if kind == slot or held_slot.startswith(f"{slot}:"):
                    return inj
            return None

    def set_injector(self, injector, kind: str, t_start, slot: str | None = None):
        with self._lock:
            self._injectors[slot or kind] = [injector, t_start, kind]

    def latch(self, slot: str, t_start: float) -> None:
        """Record the sim time an injector first took effect."""
        with self._lock:
            if slot in self._injectors:
                self._injectors[slot][1] = t_start

    def clear_injector(self, which: str | None = None):
        """Put down one weapon, or all of them.

        `which` matches a slot exactly ("interference:magnet") or every slot
        of a kind ("interference"), so the console can drop the spoof and keep
        the magnet, or the reverse.
        """
        with self._lock:
            if which is None:
                self._injectors.clear()
                return
            for slot in [s for s in self._injectors
                         if s == which or s.split(":")[0] == which]:
                self._injectors.pop(slot, None)

    # -- Run control --
    def is_running(self) -> bool:
        return self._run_thread is not None and self._run_thread.is_alive()

    def start_run(self, scenario: str, seed: int, run_fn):
        """Launch the simulation loop in a daemon thread."""
        self._stop_event.clear()
        self.clear_injector()
        self._scenario = scenario
        self._seed     = seed
        self._run_thread = threading.Thread(
            target=run_fn,
            args=(scenario, seed, self._stop_event, self),
            daemon=True,
            name="sim-loop",
        )
        self._run_thread.start()

    def stop(self, timeout: float = 1.8):
        """Signal the run loop to stop and wait for it (max timeout s)."""
        self._stop_event.set()
        if self._run_thread and self._run_thread.is_alive():
            self._run_thread.join(timeout=timeout)
        self.clear_injector()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
TARGET_SLOT = {
    "gps":      "attack",
    "compass":  "interference:magnet",
    "altitude": "interference:pressure",
}
"""What the console calls a target, and which slot it steers.

The judge picks one and drives it with the arrow keys. Keeping the mapping
here rather than in the browser means the page cannot ask for something the
simulator does not have.
"""


def _make_handler(state: SimState, run_fn, default_seed_fn):
    """Build a handler class that closes over the shared state."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Suppress default Apache-style access log; print cleaner version
            print(f"[CTRL] {self.command} {self.path} -> {args[1] if len(args) > 1 else '?'}")

        # ---- helpers ----
        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}

        def _send(self, code: int, body: dict):
            payload = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def _bad(self, msg: str):
            self._send(400, {"error": msg})

        # ---- CORS pre-flight ----
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        # ---- GET /scenarios ----
        def do_GET(self):
            if self.path == "/scenarios":
                self._send(200, {"scenarios": list_scenarios()})
            elif self.path == "/status":
                live = state.injectors()
                self._send(200, {
                    "running":   state.is_running(),
                    "scenario":  state._scenario,
                    "injecting": bool(live),
                    "inject_kind":  live[0][1] if live else None,
                    "holding": sorted(slot for _, _, _, slot in live),
                })
            else:
                self._send(404, {"error": "not found"})

        # ---- POST ----
        def do_POST(self):
            body = self._read_json()

            if self.path == "/start":
                self._handle_start(body)
            elif self.path == "/reset":
                self._handle_reset()
            elif self.path == "/inject":
                self._handle_inject(body)
            elif self.path == "/steer":
                self._handle_steer(body)
            else:
                self._send(404, {"error": "not found"})

        # ---- /start ----
        def _handle_start(self, body: dict):
            scenario = body.get("scenario", "drone_clean")
            if scenario not in list_scenarios():
                self._bad(f"unknown scenario {scenario!r}")
                return
            seed = body.get("seed", default_seed_fn())
            if state.is_running():
                state.stop()
            state.start_run(scenario, seed, run_fn)
            self._send(200, {"started": True, "scenario": scenario, "seed": seed})

        # ---- /reset ----
        def _handle_reset(self):
            t0 = time.monotonic()
            state.stop(timeout=1.8)
            elapsed = time.monotonic() - t0
            self._send(200, {"reset": True, "elapsed_s": round(elapsed, 3)})

        # ---- /steer ----
        def _handle_steer(self, body: dict):
            """Change a live attack's direction or speed without restarting it.

            This is the arrow keys. It exists as its own endpoint because
            re-injecting would build a fresh injector, whose clock starts at
            zero — the fake position would snap back onto the truth every time
            the judge turned. Steering keeps the distance already stolen.
            """
            slot = TARGET_SLOT.get(body.get("target", "gps"))
            if slot is None:
                self._bad(f"unknown target {body.get('target')!r} — "
                          f"use one of {sorted(TARGET_SLOT)}")
                return
            inj = state.get_injector(slot)
            if inj is None or not hasattr(inj, "steer"):
                self._bad("that one is not in your hands yet — take it first")
                return
            try:
                inj.steer(
                    speed_mps=(float(body["speed_mps"]) if "speed_mps" in body else None),
                    bearing_deg=(float(body["bearing_deg"]) if "bearing_deg" in body else None),
                    offset=(float(body["offset"]) if "offset" in body else None),
                )
            except (ValueError, TypeError) as exc:
                self._bad(str(exc))
                return

            out = {"steering": True, "target": body.get("target", "gps")}
            for name in ("speed_mps", "bearing_deg"):
                if hasattr(inj, name):
                    out[name] = getattr(inj, name)
            if hasattr(inj, "offset_m"):
                out["offset_m"] = round(inj.offset_m, 1)
            if hasattr(inj, "offset"):
                out["offset"] = round(inj.offset, 1)
            self._send(200, out)

        # ---- /inject ----
        def _handle_inject(self, body: dict):
            if not state.is_running():
                self._bad("no run in progress — POST /start first")
                return

            kind     = body.get("kind", "")       # 'attack', 'fault', 'interference'
            itype    = body.get("type", "")        # e.g. 'walkoff', 'stuck', 'magnet'
            strength = float(body.get("strength", 1.0))
            bearing  = float(body.get("bearing_deg", 0.0))
            sensor   = body.get("sensor", "gnss")  # for faults

            try:
                if kind == "attack":
                    inj = make_attack(itype, strength, bearing,
                                      clock_lag_s=body.get("clock_lag_s", 30.0))
                elif kind == "fault":
                    inj = make_fault(itype, sensor, strength)
                elif kind == "interference":
                    inj = make_interference(itype, strength)
                elif kind == "clear":
                    # "which" clears one hand and leaves the other running:
                    # drop the spoof but keep the magnet, or the reverse.
                    which = body.get("which")
                    state.clear_injector(which)
                    self._send(200, {"injecting": False, "cleared": which or "all"})
                    return
                else:
                    self._bad(f"unknown kind {kind!r} — use attack/fault/interference/clear")
                    return
            except (ValueError, TypeError) as exc:
                self._bad(str(exc))
                return

            # Each weapon gets its own slot, so taking hold of the compass
            # does not make them let go of the GPS.
            if kind == "fault":
                slot = f"fault:{sensor}"
            elif kind == "interference":
                slot = f"interference:{itype.lower()}"
            else:
                slot = "attack"

            # t_start will be filled by the run loop on the next frame
            state.set_injector(inj, kind, t_start=None, slot=slot)
            self._send(200, {
                "injecting": True,
                "kind": kind,
                "type": itype,
                "strength": strength,
                "bearing_deg": bearing,
                "slot": slot,
            })

    return Handler


# ---------------------------------------------------------------------------
# Run loop (called from start_run in a thread)
# ---------------------------------------------------------------------------
def _make_run_fn(vehicle_id_fn, truth_log_path, quiet):
    """
    Build the per-run simulation function that runs inside a thread.

    vehicle_id_fn : callable(vehicle_type, seed) -> str
    truth_log_path: path or None
    quiet         : bool
    """
    import os, datetime
    import numpy as np
    from .vehicle import make_vehicle
    from .sensors import SensorSuite
    from .publisher import Publisher

    FRAME_RATE_HZ = 20
    SIM_DURATION_S = 180.0

    def run_fn(scenario_name: str, seed: int,
               stop_event: threading.Event, state: SimState):
        rng = np.random.default_rng(seed)
        waypoints, vehicle_type, schedule = get_scenario(scenario_name)
        vehicle_id = vehicle_id_fn(vehicle_type, seed)
        vehicle = make_vehicle(vehicle_type, waypoints, rng)
        sensors = SensorSuite(rng, vehicle_type=vehicle_type)

        # Build schedule-based injectors (same logic as run.py)
        from .attacks import WalkOff, Teleport, AltitudeOnly, Replay
        from .faults import Stuck, Noisy, Dropout, Bias
        from .interference import Magnet, Pressure
        _ATC = {"WalkOff": WalkOff, "Teleport": Teleport, "AltitudeOnly": AltitudeOnly, "Replay": Replay}
        _FLT = {"Stuck": Stuck, "Noisy": Noisy, "Dropout": Dropout, "Bias": Bias}
        _INT = {"Magnet": Magnet, "Pressure": Pressure}

        def _make(kind, cls_name, kwargs):
            if kind == "attack":        return _ATC[cls_name](**kwargs)
            elif kind == "fault":       return _FLT[cls_name](**kwargs)
            elif kind == "interference": return _INT[cls_name](**kwargs)

        sched_entries = sorted([
            {"t_start": e["t_start"], "kind": e["kind"],
             "injector": _make(e["kind"], e["cls"], e.get("kwargs", {})), "armed": False}
            for e in schedule
        ], key=lambda x: x["t_start"])

        sched_active_inj   = None
        sched_active_kind  = None
        sched_active_t     = None

        now = datetime.datetime.utcnow()
        suffix = "".join(f"{b:02x}" for b in os.urandom(2))
        run_id = f"r-{now.strftime('%Y%m%d-%H%M%S')}-{suffix}"
        t0 = float(int(time.time()))

        pub = Publisher(
            vehicle_id=vehicle_id,
            vehicle_type=vehicle_type,
            run_id=run_id,
            seed=seed,
            t0=t0,
        )

        truth_file = None
        if truth_log_path:
            truth_file = open(truth_log_path, "w", encoding="utf-8")

        pub.send_run_start()
        if not quiet:
            print(f"[SIM] run_id={run_id}  scenario={scenario_name}  "
                  f"vehicle={vehicle_id}  seed={seed}")

        seq = 0
        total_frames = int(SIM_DURATION_S * FRAME_RATE_HZ)
        loop_start = time.monotonic()

        for i in range(total_frames):
            if stop_event.is_set():
                break

            sensor_data = sensors.update(vehicle)
            t_sim = vehicle.t

            # --- Arm scheduled injectors (scenario-defined) ---
            for entry in sched_entries:
                if not entry["armed"] and t_sim >= entry["t_start"]:
                    entry["armed"]    = True
                    sched_active_inj  = entry["injector"]
                    sched_active_kind = entry["kind"]
                    sched_active_t    = t_sim
                    if truth_file:
                        truth_file.write(json.dumps({
                            "event": "inject_start",
                            "t": round(t_sim, 3),
                            "kind": sched_active_kind,
                            "type": type(sched_active_inj).__name__,
                        }) + "\n")
                        truth_file.flush()

            # --- API injectors override the scheduled one, per kind ---
            #
            # Several can be live at once now. A judge steering the GPS while
            # holding a magnet on the compass is two separate lies arriving
            # together, which is the hardest case we have and the one worth
            # letting them try.
            active = []
            api_kinds = set()
            for inj, kind, inj_t, slot in state.injectors():
                if inj_t is None:          # latch the start time on first frame
                    state.latch(slot, t_sim)
                    inj_t = t_sim
                    if truth_file:
                        truth_file.write(json.dumps({
                            "event": "inject_start",
                            "t": round(t_sim, 3),
                            "kind": kind,
                            "type": type(inj).__name__,
                        }) + "\n")
                        truth_file.flush()
                api_kinds.add(kind)
                active.append((inj, kind, t_sim - inj_t))

            if sched_active_inj is not None and sched_active_kind not in api_kinds:
                active.append((sched_active_inj, sched_active_kind,
                               t_sim - sched_active_t))

            for inj, kind, t_since in active:
                if kind == "attack":
                    if sensor_data["gnss"] is not None:
                        sensor_data["gnss"] = inj.apply(sensor_data["gnss"], t_since)
                elif kind == "fault":
                    sensor_data = inj.apply(sensor_data, t_since, rng)
                elif kind == "interference":
                    sensor_data = inj.apply(sensor_data, t_since)

            pub.send_frame(t_sim, seq, sensor_data)

            if truth_file:
                pos = vehicle.position_enu
                truth_file.write(json.dumps({
                    "seq": seq, "t": round(t_sim, 3),
                    "true_east_m":  round(float(pos[0]), 3),
                    "true_north_m": round(float(pos[1]), 3),
                    "true_up_m":    round(float(pos[2]), 3),
                    "true_speed_mps": round(vehicle.speed_mps, 3),
                }) + "\n")

            vehicle.step()
            seq += 1

            expected_wall = loop_start + (i + 1) / FRAME_RATE_HZ
            sleep_s = expected_wall - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)

        pub.close()
        if truth_file:
            truth_file.flush()
            truth_file.close()
        if not quiet:
            print(f"[SIM] run complete — {seq} frames")

    return run_fn


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------
def serve(host: str = "0.0.0.0", port: int = PORT,
          vehicle_id_fn=None,
          truth_log_path: str | None = None,
          quiet: bool = False):
    """
    Start the HTTP control server (blocking).

    vehicle_id_fn : callable(vehicle_type, seed) -> vehicle_id string
                    defaults to "DRONE-{seed%100:02d}" etc.
    """
    import numpy as np

    if vehicle_id_fn is None:
        def vehicle_id_fn(vtype, seed):
            pfx = "DRONE" if vtype == "drone" else "TRUCK"
            return f"{pfx}-{seed % 100:02d}"

    def default_seed():
        return int(np.random.SeedSequence().entropy & 0xFFFFFFFF)

    state  = SimState()
    run_fn = _make_run_fn(vehicle_id_fn, truth_log_path, quiet)

    handler_cls = _make_handler(state, run_fn, default_seed)
    server = HTTPServer((host, port), handler_cls)
    print(f"[CTRL] HTTP control server on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop(timeout=1.0)
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    """Entry point, so the console can actually reach this.

        python -m simulator.control

    `serve()` existed but nothing could start it, which left the console's
    buttons wired to a port with nobody listening.
    """
    import argparse

    parser = argparse.ArgumentParser(description="SensorSentry simulator control")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--truth-log", default=None, metavar="FILE",
                        help="write truth to FILE (simulator side only)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    serve(host=args.host, port=args.port,
          truth_log_path=args.truth_log, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
