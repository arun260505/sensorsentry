"""
run.py — Entry point for the SensorSentry simulator.

Usage
-----
  python -m simulator.run --scenario drone_clean
  python -m simulator.run --scenario drone_manoeuvre
  python -m simulator.run --scenario drone_clean --vehicle-id DRONE-99
  python -m simulator.run --list

Options
-------
  --scenario NAME     Which scenario to run (default: drone_clean)
  --vehicle-id ID     Override the vehicle ID in frames (default: from scenario)
  --seed INT          Fix the random seed for exact replay (default: random)
  --realtime          Throttle output to 20 Hz wall time (default: as-fast-as)
  --list              Print available scenarios and exit
  --truth-log FILE    Write truth position to FILE (never to the socket)
  --quiet             Suppress per-frame console output
"""

import argparse
import json
import math
import os
import sys
import time
import datetime
import numpy as np

from .scenarios import get_scenario, list_scenarios
from .vehicle import Vehicle
from .sensors import SensorSuite
from .publisher import Publisher

FRAME_RATE_HZ = 20
SIM_DURATION_S = 180.0          # 3 minutes


# ---------------------------------------------------------------------------
def make_run_id() -> str:
    now = datetime.datetime.utcnow()
    suffix = "".join(f"{b:02x}" for b in os.urandom(2))
    return f"r-{now.strftime('%Y%m%d-%H%M%S')}-{suffix}"


# ---------------------------------------------------------------------------
def run(scenario_name: str,
        vehicle_id: str,
        seed: int,
        realtime: bool,
        truth_log_path: str | None,
        quiet: bool):

    # --- Set up RNG ---
    rng = np.random.default_rng(seed)

    # --- Load scenario ---
    waypoints, vehicle_type = get_scenario(scenario_name)

    # --- Default vehicle ID ---
    if vehicle_id is None:
        vehicle_id = f"DRONE-{seed % 100:02d}" if vehicle_type == "drone" else f"TRUCK-{seed % 100:02d}"

    # --- Create objects ---
    vehicle = Vehicle(waypoints, rng)
    sensors = SensorSuite(rng, vehicle_type=vehicle_type)

    run_id = make_run_id()
    t0 = float(int(time.time()))     # wall-clock anchor (arbitrary monotonic start)

    pub = Publisher(
        vehicle_id=vehicle_id,
        vehicle_type=vehicle_type,
        run_id=run_id,
        seed=seed,
        t0=t0,
    )

    # --- Truth log (simulator-side only, NEVER on the socket) ---
    truth_file = None
    if truth_log_path:
        truth_file = open(truth_log_path, "w", encoding="utf-8")

    # --- Send header ---
    pub.send_run_start()
    if not quiet:
        print(f"[SIM] run_id={run_id}  scenario={scenario_name}  "
              f"vehicle={vehicle_id}  seed={seed}  t0={t0}")
        print(f"[SIM] Sending to UDP 127.0.0.1:5005 at {FRAME_RATE_HZ} Hz "
              f"for {SIM_DURATION_S:.0f} s")

    # --- Main loop ---
    seq = 0
    total_frames = int(SIM_DURATION_S * FRAME_RATE_HZ)
    first_frame_printed = False
    loop_start = time.monotonic()

    for i in range(total_frames):
        if vehicle.done:
            if not quiet:
                print("[SIM] All waypoints reached — holding final position.")
            # Keep sending frames at last position so detector can observe steady state
            # (vehicle.step() is a no-op when done)

        # Collect sensor readings BEFORE stepping (so t matches position)
        sensor_data = sensors.update(vehicle)
        t_sim = vehicle.t

        # Send frame
        frame = pub.send_frame(t_sim, seq, sensor_data)

        # Print first frame for verification
        if not first_frame_printed and not quiet:
            print("\n[SIM] === Sample frame (first with GNSS) ===")
            print(json.dumps(frame, indent=2))
            print("===\n")
            first_frame_printed = True

        # Truth log (simulator-side only)
        if truth_file:
            pos = vehicle.position_enu
            truth_record = {
                "seq": seq,
                "t": round(t_sim, 3),
                "true_east_m": round(float(pos[0]), 3),
                "true_north_m": round(float(pos[1]), 3),
                "true_up_m": round(float(pos[2]), 3),
                "true_speed_mps": round(vehicle.speed_mps, 3),
            }
            truth_file.write(json.dumps(truth_record) + "\n")

        # Advance vehicle
        vehicle.step()
        seq += 1

        # Real-time throttle
        if realtime:
            expected_wall = loop_start + (i + 1) / FRAME_RATE_HZ
            sleep_s = expected_wall - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)

    pub.close()
    if truth_file:
        truth_file.close()

    if not quiet:
        elapsed = time.monotonic() - loop_start
        print(f"[SIM] Done. {seq} frames sent in {elapsed:.2f} s wall time.")


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="SensorSentry vehicle simulator"
    )
    parser.add_argument("--scenario", default="drone_clean",
                        help="Scenario name (default: drone_clean)")
    parser.add_argument("--vehicle-id", default=None,
                        help="Override vehicle ID in frames")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for exact replay (default: random)")
    parser.add_argument("--realtime", action="store_true",
                        help="Throttle to 20 Hz wall time")
    parser.add_argument("--list", action="store_true",
                        help="List available scenarios and exit")
    parser.add_argument("--truth-log", default=None, metavar="FILE",
                        help="Write truth positions to FILE (simulator-side only)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-frame output")
    args = parser.parse_args()

    if args.list:
        print("Available scenarios:")
        for name in list_scenarios():
            print(f"  {name}")
        sys.exit(0)

    seed = args.seed if args.seed is not None else int(np.random.SeedSequence().entropy & 0xFFFFFFFF)

    run(
        scenario_name=args.scenario,
        vehicle_id=args.vehicle_id,
        seed=seed,
        realtime=args.realtime,
        truth_log_path=args.truth_log,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    main()
