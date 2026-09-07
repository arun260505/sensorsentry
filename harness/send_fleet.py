"""Send several vehicles at once, so the fleet view has something to show.

    python -m harness.send_fleet                 # 4 drones, 3 attacked
    python -m harness.send_fleet --attacked 2

A test tool, not the product — the same role `send_fixture` plays for one
vehicle. It exists because Abishek's simulator flies one vehicle per process
from a fixed origin, and the fleet argument needs several of them spread over
a few kilometres with one attacker reaching some and not others.

Each vehicle is a separate flight through the real simulator, tagged with its
own `vehicle_id`, sharing the UDP port. The detector tells them apart by id
and gives each an independent pipeline, which is the point: the fleet argument
only holds if the vehicles genuinely reached their conclusions separately.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time

import numpy as np

from simulator.scenarios import get_scenario
from simulator.sensors import SensorSuite
from simulator.vehicle import Vehicle, enu_to_geodetic

DT = 1.0 / 20.0

# Spread over a few kilometres. The first three sit inside one transmitter's
# reach; the last is further out, and is the one that gets warned.
OFFSETS = [
    (0.0000, 0.0000),
    (0.0070, 0.0030),
    (0.0030, 0.0080),
    (0.0180, 0.0150),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send a fleet of vehicles")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--vehicles", type=int, default=4)
    parser.add_argument("--attacked", type=int, default=3,
                        help="how many of them the attacker reaches")
    parser.add_argument("--spoof", type=float, default=3.0, metavar="M/S")
    parser.add_argument("--spoof-at", type=float, default=25.0, metavar="SECONDS")
    parser.add_argument("--duration", type=float, default=240.0)
    parser.add_argument("--bearing", type=float, default=135.0)
    args = parser.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)
    yaw = math.radians(90.0 - args.bearing)
    waypoints, vehicle_type = get_scenario("drone_clean")[:2]

    fleet = []
    for index in range(min(args.vehicles, len(OFFSETS))):
        seed = 4242 + index * 17
        rng = np.random.default_rng(seed)
        vehicle_id = f"DRONE-{11 + index}"
        fleet.append({
            "id": vehicle_id,
            "seed": seed,
            "vehicle": Vehicle(waypoints, rng),
            "sensors": SensorSuite(rng, vehicle_type=vehicle_type),
            "dlat": OFFSETS[index][0],
            "dlon": OFFSETS[index][1],
            "attacked": index < args.attacked,
        })
        sock.sendto(json.dumps({
            "type": "run_start", "run_id": f"fleet-{vehicle_id}",
            "vehicle_id": vehicle_id, "vehicle_type": vehicle_type,
            "seed": seed, "rate_hz": 20, "gnss_rate_hz": 5, "t0": 0.0,
        }).encode(), target)

    attacked = [v["id"] for v in fleet if v["attacked"]]
    print(f"{len(fleet)} vehicles -> {args.host}:{args.port}")
    print(f"attacked from t={args.spoof_at:.0f}s at {args.spoof:.1f} m/s: "
          f"{', '.join(attacked) if attacked else 'none'}")
    print("Ctrl-C to stop")

    started = time.monotonic()
    frames = int(args.duration / DT)
    try:
        for i in range(frames):
            t = i * DT
            for member in fleet:
                member["vehicle"].step()
                reading = member["sensors"].update(member["vehicle"])

                if reading["gnss"]:
                    pos = member["vehicle"].position_enu
                    east, north = pos[0], pos[1]
                    if member["attacked"] and args.spoof and t >= args.spoof_at:
                        drift = args.spoof * (t - args.spoof_at)
                        east += drift * math.cos(yaw)
                        north += drift * math.sin(yaw)
                    lat, lon, alt = enu_to_geodetic(east, north, pos[2])
                    reading["gnss"] = dict(reading["gnss"])
                    reading["gnss"]["lat"] = lat + member["dlat"]
                    reading["gnss"]["lon"] = lon + member["dlon"]

                sock.sendto(json.dumps({
                    "vehicle_id": member["id"], "t": t, "seq": i,
                    "imu": reading["imu"], "baro": reading["baro"],
                    "mag": reading["mag"], "gnss": reading["gnss"],
                    "odom": reading["odom"],
                }).encode(), target)

            # Paced to the wall clock. Sending flat out floods the socket and
            # the detector drops frames, which breaks dead reckoning.
            ahead = (started + (i + 1) * DT) - time.monotonic()
            if ahead > 0:
                time.sleep(ahead)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
