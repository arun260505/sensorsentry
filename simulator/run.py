"""
run.py — Entry point for the SensorSentry simulator.

Usage
-----
  python -m simulator.run --scenario drone_clean
  python -m simulator.run --scenario drone_walkoff
  python -m simulator.run --list

Options
-------
  --scenario NAME     Which scenario to run (default: drone_clean)
  --vehicle-id ID     Override the vehicle ID in frames (default: from scenario)
  --seed INT          Fix the random seed for exact replay (default: random)
  --fast              Send as fast as possible (offline analysis only)
  --list              Print available scenarios and exit
  --truth-log FILE    Write truth position + inject events to FILE (never to socket)
  --quiet             Suppress per-frame console output
  --serve             Also start the HTTP control server on port 5010
"""

import argparse
import json
import os
import sys
import time
import datetime
import numpy as np

from .scenarios import get_scenario, list_scenarios
from .vehicle import Vehicle
from .sensors import SensorSuite
from .publisher import Publisher
from .attacks import WalkOff, Teleport, AltitudeOnly, Replay
from .faults import Stuck, Noisy, Dropout, Bias
from .interference import Magnet, Pressure

FRAME_RATE_HZ  = 20
SIM_DURATION_S = 180.0

_ATTACK_CLASSES       = {"WalkOff": WalkOff, "Teleport": Teleport, "AltitudeOnly": AltitudeOnly, "Replay": Replay}
_FAULT_CLASSES        = {"Stuck": Stuck, "Noisy": Noisy, "Dropout": Dropout, "Bias": Bias}
_INTERFERENCE_CLASSES = {"Magnet": Magnet, "Pressure": Pressure}


def make_run_id():
    now    = datetime.datetime.utcnow()
    suffix = "".join("%02x" % b for b in os.urandom(2))
    return "r-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), suffix)


def _make_injector(kind, cls_name, kwargs):
    if kind == "attack":
        cls = _ATTACK_CLASSES[cls_name]
    elif kind == "fault":
        cls = _FAULT_CLASSES[cls_name]
    elif kind == "interference":
        cls = _INTERFERENCE_CLASSES[cls_name]
    else:
        raise ValueError("Unknown injector kind: %r" % kind)
    return cls(**kwargs)


def run(scenario_name, vehicle_id, seed, realtime, truth_log_path, quiet):

    rng = np.random.default_rng(seed)

    waypoints, vehicle_type, schedule = get_scenario(scenario_name)

    if vehicle_id is None:
        pfx = "DRONE" if vehicle_type == "drone" else "TRUCK"
        vehicle_id = "%s-%02d" % (pfx, seed % 100)

    vehicle = Vehicle(waypoints, rng)
    sensors = SensorSuite(rng, vehicle_type=vehicle_type)
    run_id  = make_run_id()
    t0      = float(int(time.time()))

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

    # Build scheduled injectors sorted by trigger time
    sched_entries = sorted([
        {
            "t_start":  e["t_start"],
            "kind":     e["kind"],
            "injector": _make_injector(e["kind"], e["cls"], e.get("kwargs", {})),
            "armed":    False,
        }
        for e in schedule
    ], key=lambda x: x["t_start"])

    active_injector = None
    active_kind     = None
    active_t_start  = None

    pub.send_run_start()
    if not quiet:
        print("[SIM] run_id=%s  scenario=%s  vehicle=%s  seed=%s  t0=%s" %
              (run_id, scenario_name, vehicle_id, seed, t0))
        print("[SIM] Sending to UDP 127.0.0.1:5005 at %d Hz for %.0f s" %
              (FRAME_RATE_HZ, SIM_DURATION_S))
        for e in sched_entries:
            print("[SIM] Scheduled: %s %s @ t=%.1f s" %
                  (e["kind"], type(e["injector"]).__name__, e["t_start"]))

    seq = 0
    total_frames     = int(SIM_DURATION_S * FRAME_RATE_HZ)
    first_printed    = False
    loop_start       = time.monotonic()

    for i in range(total_frames):
        sensor_data = sensors.update(vehicle)
        t_sim       = vehicle.t

        # Arm any scheduled injectors
        for entry in sched_entries:
            if not entry["armed"] and t_sim >= entry["t_start"]:
                entry["armed"]  = True
                active_injector = entry["injector"]
                active_kind     = entry["kind"]
                active_t_start  = t_sim
                if not quiet:
                    print("[SIM] Injecting %s %s at t=%.2f s" %
                          (active_kind, type(active_injector).__name__, t_sim))
                if truth_file:
                    truth_file.write(json.dumps({
                        "event": "inject_start",
                        "t":     round(t_sim, 3),
                        "kind":  active_kind,
                        "type":  type(active_injector).__name__,
                    }) + "\n")
                    truth_file.flush()

        # Apply active injector
        if active_injector is not None:
            t_since = t_sim - active_t_start
            if active_kind == "attack":
                if sensor_data["gnss"] is not None:
                    sensor_data["gnss"] = active_injector.apply(sensor_data["gnss"], t_since)
            elif active_kind == "fault":
                sensor_data = active_injector.apply(sensor_data, t_since, rng)
            elif active_kind == "interference":
                sensor_data = active_injector.apply(sensor_data, t_since)

        frame = pub.send_frame(t_sim, seq, sensor_data)

        if not first_printed and not quiet:
            print("\n[SIM] === Sample frame (first) ===")
            print(json.dumps(frame, indent=2))
            print("===\n")
            first_printed = True

        if truth_file:
            pos = vehicle.position_enu
            truth_file.write(json.dumps({
                "seq":            seq,
                "t":              round(t_sim, 3),
                "true_east_m":    round(float(pos[0]), 3),
                "true_north_m":   round(float(pos[1]), 3),
                "true_up_m":      round(float(pos[2]), 3),
                "true_speed_mps": round(vehicle.speed_mps, 3),
            }) + "\n")

        vehicle.step()
        seq += 1

        if realtime:
            expected_wall = loop_start + (i + 1) / FRAME_RATE_HZ
            sleep_s = expected_wall - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)

    pub.close()
    if truth_file:
        truth_file.flush()
        truth_file.close()
    if not quiet:
        elapsed = time.monotonic() - loop_start
        print("[SIM] Done. %d frames sent in %.2f s wall time." % (seq, elapsed))


def main():
    parser = argparse.ArgumentParser(description="SensorSentry vehicle simulator")
    parser.add_argument("--scenario",   default="drone_clean")
    parser.add_argument("--vehicle-id", default=None)
    parser.add_argument("--seed",       type=int, default=None)
    parser.add_argument("--fast",       action="store_true")
    parser.add_argument("--list",       action="store_true")
    parser.add_argument("--truth-log",  default=None, metavar="FILE")
    parser.add_argument("--quiet",      action="store_true")
    parser.add_argument("--serve",      action="store_true",
                        help="Also start HTTP control server on port 5010")
    args = parser.parse_args()

    if args.list:
        print("Available scenarios:")
        for name in list_scenarios():
            print("  %s" % name)
        sys.exit(0)

    seed = args.seed if args.seed is not None else int(np.random.SeedSequence().entropy & 0xFFFFFFFF)

    if args.serve:
        import threading
        from .control import serve as ctrl_serve
        t = threading.Thread(target=ctrl_serve, kwargs={"quiet": args.quiet}, daemon=True)
        t.start()

    run(
        scenario_name  = args.scenario,
        vehicle_id     = args.vehicle_id,
        seed           = seed,
        realtime       = not args.fast,
        truth_log_path = args.truth_log,
        quiet          = args.quiet,
    )


if __name__ == "__main__":
    main()
