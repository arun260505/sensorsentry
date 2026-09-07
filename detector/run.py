"""Detector entry point, terminal output.

    python -m detector.run                 # listen on UDP 5005
    python -m detector.run --verbose       # every frame, not once a second

For the visual version use `python -m detector.server` instead. Both drive the
same Pipeline, so what the console draws is what this prints.

Run it in its own terminal, with the simulator in another. That separation is
the point: this process receives sensor readings and nothing else, so it
cannot know whether an attack is happening. Keep it that way.
"""

from __future__ import annotations

import argparse
import sys

from . import health
from .ingest import DEFAULT_PORT, SchemaViolation, UdpReceiver
from .pipeline import Pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SensorSentry detector")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--vehicle-type", default=None,
                        help="override the profile; normally taken from run_start")
    parser.add_argument("--verbose", action="store_true",
                        help="print every frame instead of once a second")
    args = parser.parse_args(argv)

    receiver = UdpReceiver(port=args.port)
    pipeline = Pipeline(args.vehicle_type)

    print(f"detector listening on UDP {args.port} — waiting for a run", flush=True)
    announced: str | None = None
    last_report_t = 0.0

    try:
        for message in receiver.messages():
            try:
                state = pipeline.accept(message)
            except SchemaViolation as exc:
                # Loud and fatal on purpose. A malformed frame is a bug to fix
                # now; a forbidden field invalidates the entire demo.
                print(f"\nSCHEMA VIOLATION: {exc}\n", file=sys.stderr, flush=True)
                return 2

            header = pipeline.stream.header
            if header is not None and header.run_id != announced:
                announced = header.run_id
                last_report_t = 0.0
                print(f"\nrun {header.run_id} — {header.vehicle_id} "
                      f"(seed {header.seed})", flush=True)

            if state is None:
                continue

            if not (args.verbose or state.t - last_report_t >= 1.0):
                continue
            last_report_t = state.t

            if not state.anchored or state.residual is None:
                print(f"  t={state.t:6.1f}  anchoring…", flush=True)
                continue

            residual = state.residual
            faults = health.summarise(state.health)
            faults = "" if faults == "all sensors healthy" else f"  [{faults}]"
            print(
                f"  t={state.t:6.1f}  {state.state:<5}  "
                f"apart {residual.horizontal_m:7.1f} m  "
                f"vert {residual.vertical_m:+6.1f} m  "
                f"sigma {residual.sigma_m:6.1f}  "
                f"ratio {residual.ratio:5.2f}  "
                f"dr {state.witness.distance_travelled_m:7.1f} m"
                f"{faults}",
                flush=True,
            )

    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        receiver.close()

    if pipeline.stream.frames_seen:
        print(f"{pipeline.stream.frames_seen} frames, "
              f"{pipeline.stream.frames_dropped} dropped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
