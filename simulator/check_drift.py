"""
check_drift.py — IMU dead-reckoning drift self-check.

Run from repo root:
  py -3 -m simulator.check_drift

What this measures
------------------
How far a dead-reckoning position estimate drifts from truth over 60 s
when the vehicle is flying straight and level at constant cruise speed,
with no GPS correction.

Methodological note
-------------------
The 20-120 m target is for pure IMU-bias-induced drift on a near-constant-
velocity segment. Turns create centripetal acceleration that an IMU cannot
distinguish from linear acceleration, so a manoeuvre-heavy test would give
numbers 5-10x higher than the bias-only floor. The detector operates on
5 Hz GPS windows (0.2 s between corrections), so the relevant noise floor
is the straight-cruise bias — that is what we measure here.

We fly drone_clean for 60 s after a brief warmup to steady cruise speed,
on a segment that is mostly straight-east (lowest manoeuvre content).
"""

import math
import numpy as np
import sys

from .scenarios import get_scenario
from .vehicle import Vehicle, DT, Waypoint
from .sensors import SensorSuite

GRAVITY = 9.79
SIM_SECONDS = 60.0
RUNS = 6


def dead_reckon_one_run(seed: int) -> float:
    """
    Fly a straight-cruise segment at 15 m/s east for SIM_SECONDS.
    Initialise DR with exact truth at t=0 (no initial error).
    Integrate IMU only — no GPS, no magnetometer.
    Returns 3D drift from truth at t=60 s.
    """
    rng = np.random.default_rng(seed)

    # Straight east: minimises centripetal contamination
    wps = [
        Waypoint(   0, 0, 50, speed_mps=15, max_acc=3, turn_rate=0.1, max_climb=1),
        Waypoint(5000, 0, 50, speed_mps=15, max_acc=3, turn_rate=0.1, max_climb=1),
    ]
    vehicle = Vehicle(wps, rng)
    sensors = SensorSuite(rng, vehicle_type="drone")

    # Short warmup to reach cruise speed (5 s)
    for _ in range(int(5.0 / DT)):
        sensors.update(vehicle)
        vehicle.step()

    # Snapshot truth at cruise-speed start
    dr_pos = vehicle.position_enu.copy()
    dr_vel = vehicle.velocity_enu.copy()   # initialise with true velocity
    dr_yaw = vehicle.yaw_rad               # initialise with true heading

    # Integrate for SIM_SECONDS
    steps = int(SIM_SECONDS / DT)
    for _ in range(steps):
        sd = sensors.update(vehicle)
        imu = sd["imu"]

        ax_b = imu["ax"]
        ay_b = imu["ay"]
        az_b = imu["az"]
        gz   = imu["gz"]

        # Yaw integration from gyro
        dr_yaw += gz * DT

        # Gravity removal (assume near-level: only az has gravity)
        lin_az = az_b - GRAVITY

        # Rotate body horizontal accel → ENU (yaw-only; near-level approximation)
        cy, sy = math.cos(dr_yaw), math.sin(dr_yaw)
        acc_e = cy * ax_b - sy * ay_b
        acc_n = sy * ax_b + cy * ay_b
        acc_u = lin_az

        dr_vel[0] += acc_e * DT
        dr_vel[1] += acc_n * DT
        dr_vel[2] += acc_u * DT
        dr_pos    += dr_vel * DT

        vehicle.step()

    true_end = vehicle.position_enu
    drift = float(np.linalg.norm(dr_pos - true_end))
    return drift


def main():
    print(f"Dead-reckoning drift self-check ({SIM_SECONDS:.0f} s straight cruise, {RUNS} runs)")
    print(f"Target band: 20-120 m\n")

    drifts = []
    base_seed = int(np.random.SeedSequence().entropy & 0xFFFFFFFF)

    for i in range(RUNS):
        seed = base_seed + i * 137
        drift = dead_reckon_one_run(seed)
        drifts.append(drift)
        status = "OK" if 20 <= drift <= 120 else ("LOW" if drift < 20 else "HIGH")
        print(f"  Run {i+1}  seed={seed}  drift={drift:6.1f} m  [{status}]")

    mean   = sum(drifts) / len(drifts)
    lo, hi = min(drifts), max(drifts)
    in_band = all(20 <= d <= 120 for d in drifts)

    print(f"\nSummary: mean={mean:.1f} m  min={lo:.1f} m  max={hi:.1f} m")
    print("All runs in band:", "YES" if in_band else "NO -- adjust IMU noise")

    if not in_band:
        sys.exit(1)


if __name__ == "__main__":
    main()
