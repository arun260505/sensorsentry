"""Detector entry point.

    python -m detector.run                 # listen on UDP 5005
    python -m detector.run --port 5005 --verbose

Wires stages 1-3 plus the GNSS-versus-witness residual, and prints a status
line once a second. Stages 4-10 land in later phases; this is what PLAN.md
phase 2 needs to be finished.

Run it in its own terminal, with the simulator in another. That separation is
the point: this process receives sensor readings and nothing else, so it
cannot know whether an attack is happening. Keep it that way.
"""

from __future__ import annotations

import argparse
import sys

from . import health, profiles
from .deadreckon import DeadReckoner
from .ingest import DEFAULT_PORT, FrameStream, SchemaViolation, UdpReceiver
from .residual import ResidualTracker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SensorSentry detector")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--vehicle-type", default=None,
                        help="override the profile; normally taken from run_start")
    parser.add_argument("--verbose", action="store_true",
                        help="print every frame instead of once a second")
    args = parser.parse_args(argv)

    receiver = UdpReceiver(port=args.port)
    stream = FrameStream()

    profile = profiles.get(args.vehicle_type) if args.vehicle_type else None
    monitor: health.HealthMonitor | None = None
    reckoner: DeadReckoner | None = None
    tracker: ResidualTracker | None = None

    print(f"detector listening on UDP {args.port} — waiting for a run", flush=True)
    last_report_t = 0.0

    try:
        for message in receiver.messages():
            try:
                frame = stream.accept(message)
            except SchemaViolation as exc:
                # Loud and fatal on purpose. A malformed frame is a bug to fix
                # now; a forbidden field invalidates the entire demo.
                print(f"\nSCHEMA VIOLATION: {exc}\n", file=sys.stderr, flush=True)
                return 2

            if frame is None:
                header = stream.header
                if header is not None:
                    profile = profiles.get(args.vehicle_type or header.vehicle_type)
                    monitor = health.HealthMonitor(profile)
                    reckoner = DeadReckoner(profile)
                    tracker = ResidualTracker(profile.accel_bias_sigma)
                    last_report_t = 0.0
                    print(
                        f"\nrun {header.run_id} — {header.vehicle_id} "
                        f"({profile.name}, seed {header.seed})",
                        flush=True,
                    )
                continue

            if reckoner is None or monitor is None or tracker is None:
                # Frames before a run_start. The simulator was already going
                # when we attached; wait for the next run rather than guessing
                # a profile.
                continue

            report = monitor.update(frame)
            witness = reckoner.update(frame)
            residual = tracker.update(frame, witness)

            due = args.verbose or frame.t - last_report_t >= 1.0
            if not due:
                continue
            last_report_t = frame.t

            if not tracker.anchored:
                print(f"  t={frame.t:6.1f}  anchoring…", flush=True)
                continue

            last = residual or tracker.last
            if last is None:
                continue

            faults = health.summarise(report)
            faults = "" if faults == "all sensors healthy" else f"  [{faults}]"
            print(
                f"  t={frame.t:6.1f}  "
                f"residual {last.horizontal_m:7.1f} m  "
                f"vert {last.vertical_m:+6.1f} m  "
                f"sigma {last.sigma_m:6.1f}  "
                f"ratio {last.ratio:5.2f}  "
                f"dr {witness.distance_travelled_m:7.1f} m"
                f"{faults}",
                flush=True,
            )

    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        receiver.close()

    if stream.frames_seen:
        print(
            f"{stream.frames_seen} frames, {stream.frames_dropped} dropped",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
