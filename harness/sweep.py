"""Measure what the detector actually does, against the real simulator.

    python -m harness.sweep

Produces the two numbers the demo closes on — false alarms on honest flights,
and where detection stops working — by running the whole pipeline over many
seeds. Nothing here is hardcoded into the demo; the table it prints is the
table we show, and if the detector regresses the table says so.

The failure boundary matters as much as the successes. A team claiming to
catch everything is overselling and experienced judges know it; naming the
exact point where the method stops working is the most credible thing we can
put on a slide.
"""

from __future__ import annotations

import argparse
import math

import numpy as np

from detector.pipeline import Pipeline
from simulator.scenarios import get_scenario
from simulator.sensors import SensorSuite
from simulator.vehicle import Vehicle, enu_to_geodetic

DT = 1.0 / 20.0
ONSET_S = 40.0


def run(scenario: str, seed: int, *, spoof_mps: float = 0.0, magnet_deg: float = 0.0,
        onset_s: float = ONSET_S, secs: float = 180.0, bearing_deg: float = 135.0):
    """One run. Returns (alert_frames, first_alert_time or None).

    The attack is applied to the *reported* GNSS position only — the vehicle
    flies its real route throughout, exactly as a real spoofer would leave it.
    """
    rng = np.random.default_rng(seed)
    waypoints, vehicle_type = get_scenario(scenario)[:2]
    vehicle = Vehicle(waypoints, rng)
    sensors = SensorSuite(rng, vehicle_type=vehicle_type)
    pipeline = Pipeline()
    pipeline.accept({
        "type": "run_start", "run_id": f"sweep-{seed}", "vehicle_id": "SWEEP",
        "vehicle_type": vehicle_type, "seed": seed,
        "rate_hz": 20, "gnss_rate_hz": 5, "t0": 0.0,
    })

    yaw = math.radians(90.0 - bearing_deg)
    alerts = 0
    first: float | None = None

    for i in range(int(secs / DT)):
        vehicle.step()
        reading = sensors.update(vehicle)
        t = i * DT

        if reading["gnss"] and spoof_mps and t >= onset_s:
            drift = spoof_mps * (t - onset_s)
            pos = vehicle.position_enu
            lat, lon, _alt = enu_to_geodetic(
                pos[0] + drift * math.cos(yaw), pos[1] + drift * math.sin(yaw), pos[2]
            )
            reading["gnss"] = dict(reading["gnss"])
            reading["gnss"]["lat"] = lat
            reading["gnss"]["lon"] = lon

        if magnet_deg and t >= onset_s:
            reading["mag"] = dict(reading["mag"])
            reading["mag"]["heading_deg"] = (reading["mag"]["heading_deg"] + magnet_deg) % 360.0

        state = pipeline.accept({
            "vehicle_id": "SWEEP", "t": t, "seq": i, "imu": reading["imu"],
            "baro": reading["baro"], "mag": reading["mag"],
            "gnss": reading["gnss"], "odom": reading["odom"],
        })
        if state is None:
            continue
        if state.state == "ALERT":
            alerts += 1
            if first is None:
                first = t
    return alerts, first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SensorSentry measurement sweep")
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--secs", type=float, default=180.0)
    args = parser.parse_args(argv)
    seeds = [4242, 77, 903, 11, 555, 8080][: args.seeds]

    print("FALSE ALARMS — honest flights, no attack")
    bad = 0
    for scenario in ("drone_clean", "drone_manoeuvre"):
        for seed in seeds:
            alerts, _ = run(scenario, seed, secs=args.secs)
            bad += 1 if alerts else 0
            verdict = "quiet" if alerts == 0 else f"{alerts} ALERT frames"
            print(f"  {scenario:17s} seed {seed:5d}   {verdict}")
    print(f"  => {'zero false alarms' if bad == 0 else f'{bad} runs raised an alarm'}")

    print()
    print("DETECTION — GPS walk-off, attack begins at t=40 s")
    print("  strength     result")
    for speed in (0.5, 1.0, 2.0, 3.0, 5.0):
        found = [f for _a, f in
                 (run("drone_clean", s, spoof_mps=speed, secs=args.secs) for s in seeds)
                 if f is not None]
        if len(found) == len(seeds):
            lo, hi = min(found) - ONSET_S, max(found) - ONSET_S
            result = f"caught {lo:.0f}-{hi:.0f} s after onset"
        elif found:
            result = f"caught in only {len(found)} of {len(seeds)} runs"
        else:
            result = "NOT DETECTED — below our floor"
        print(f"  {speed:4.1f} m/s     {result}")

    print()
    print("INTERFERENCE — magnet beside the compass, from t=40 s")
    for offset in (10.0, 25.0, 40.0):
        found = [f for _a, f in
                 (run("drone_clean", s, magnet_deg=offset, secs=args.secs) for s in seeds)
                 if f is not None]
        result = (f"caught {min(found) - ONSET_S:.0f} s after onset"
                  if len(found) == len(seeds) else
                  f"caught in only {len(found)} of {len(seeds)} runs" if found else
                  "NOT DETECTED")
        print(f"  {offset:4.0f} deg     {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
