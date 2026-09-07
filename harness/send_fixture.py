"""Send fixture frames over UDP, so the detector can be exercised end to end.

    python -m harness.send_fixture --spoof 2.0 --spoof-at 20

NOT the simulator. This exists so the detector's socket path can be tested
before `simulator/` lands, and afterwards as a fast way to reproduce a bug
without starting the real thing. Straight-line motion only — routes,
manoeuvres and the attack library belong in `simulator/`.
"""

from __future__ import annotations

import argparse
import json
import socket
import time

from . import fixtures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="send fixture frames over UDP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--speed", type=float, default=12.0)
    parser.add_argument("--bearing", type=float, default=45.0)
    parser.add_argument("--seed", type=int, default=None,
                        help="default: from the clock, so runs differ")
    parser.add_argument("--spoof", type=float, default=0.0,
                        help="walk-off speed in m/s; 0 disables")
    parser.add_argument("--spoof-at", type=float, default=20.0)
    parser.add_argument("--spoof-bearing", type=float, default=135.0)
    parser.add_argument("--realtime", action="store_true",
                        help="pace to the wall clock instead of sending flat out")
    args = parser.parse_args(argv)

    seed = args.seed if args.seed is not None else int(time.time() * 1000) % 2**31
    track = fixtures.Track(speed_mps=args.speed, bearing_deg=args.bearing)
    spoof = fixtures.Spoof(
        start_t=args.spoof_at if args.spoof > 0 else float("inf"),
        speed_mps=args.spoof,
        bearing_deg=args.spoof_bearing,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)
    sent = 0
    started = time.monotonic()

    print(f"sending to {args.host}:{args.port}  seed={seed}"
          + (f"  spoof {args.spoof} m/s from t={args.spoof_at}" if args.spoof else ""))

    for message in fixtures.frames(
        duration_s=args.duration, track=track, spoof=spoof, seed=seed
    ):
        sock.sendto(json.dumps(message).encode("utf-8"), target)
        sent += 1
        if args.realtime and message.get("type") != "run_start":
            ahead = message["t"] - (time.monotonic() - started)
            if ahead > 0:
                time.sleep(ahead)
        elif not args.realtime:
            # Even flat out, pace slightly: a burst of thousands of datagrams
            # overruns the receive buffer and the detector sees dropped frames
            # that never happened on the wire.
            time.sleep(0.001)

    sock.close()
    print(f"sent {sent} messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
