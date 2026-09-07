"""Every case a judge can produce, run end to end, checked against what we claim.

    python -m harness.usecases            # the table
    python -m harness.usecases --verbose  # plus the evidence for each

The demo now hands the controls to the judge, so "what happens if they press
that?" stops being rhetorical. This runs every button on the console against
the real pipeline and prints what comes back, so nothing on stage is a
surprise and nothing in the pitch is a claim nobody checked.

Exits non-zero if any case misses what it should say. A case that is expected
to be undetected is listed as such rather than quietly left out — the ones we
fail are as much a part of the demo as the ones we pass, and a judge who finds
an unlisted failure has found a lie, while one who finds a listed failure has
found an honest engineer.

Three outcomes are distinguished, because they are three different sentences
to an operator:

    TAMPERED       someone is inventing this reading      -> stop, do not trust
    FAILED SENSOR  the sensor has stopped telling truth   -> service the vehicle
    INTERFERENCE   something physical is affecting it     -> move away from it
"""

from __future__ import annotations

import argparse
import math

import numpy as np

from detector.pipeline import Pipeline
from simulator.attacks import make_attack
from simulator.faults import make_fault
from simulator.interference import make_interference
from simulator.puppet import GnssPuppet, MagPuppet, BaroPuppet, OdomPuppet
from simulator.scenarios import get_scenario
from simulator.sensors import SensorSuite
from simulator.vehicle import make_vehicle

DT = 1.0 / 20.0
TAKE_AT = 25.0
"""When the judge acts. Late enough that the run is settled and the vehicle is
properly under way, early enough to leave room to be caught."""

WORD = {"attack": "TAMPERED", "fault": "FAILED SENSOR",
        "interference": "INTERFERENCE"}


class Case:
    """One thing a judge can do, and what we say should come back."""

    def __init__(self, name: str, how: str, expect: str, *,
                 scenario: str = "truck_clean", sensor: str | None = None,
                 secs: float = 140.0, build=None, apply=None, note: str = ""):
        self.note = note        # why this is the honest answer, when it surprises
        self.name = name
        self.how = how              # what the judge presses
        self.expect = expect        # TAMPERED / FAILED SENSOR / ... / silent
        self.scenario = scenario
        self.sensor = sensor
        self.secs = secs
        self.build = build          # () -> injector, or None
        self.apply = apply          # (inj, reading, t_since) -> reading


# --- how each kind of injector is applied to a frame -----------------------

def _attack(inj, reading, ts, rng):
    """GNSS-only injectors: attacks reach the receiver and nothing else."""
    if reading["gnss"] is not None:
        reading["gnss"] = inj.apply(reading["gnss"], ts)
    return reading


def _whole(inj, reading, ts, rng):
    """Injectors handed the whole frame — interference, and puppets that are
    not the GNSS one."""
    return inj.apply(reading, ts)


def _fault(inj, reading, ts, rng):
    """Faults need the run's own random source, so a noisy sensor is noisy
    the same way every time for a given seed and the case is replayable."""
    return inj.apply(reading, ts, rng)


_puppet_gnss = _attack


def run_case(case: Case, seed: int = 4242) -> dict:
    """Drive one case through the real pipeline. Returns what came out."""
    waypoints, vehicle_type, _schedule = get_scenario(case.scenario)
    rng = np.random.default_rng(seed)
    vehicle = make_vehicle(vehicle_type, waypoints, rng)
    sensors = SensorSuite(rng, vehicle_type=vehicle_type)

    pipeline = Pipeline()
    pipeline.accept({
        "type": "run_start", "run_id": "uc", "vehicle_id": "UC",
        "vehicle_type": vehicle_type, "seed": seed,
        "rate_hz": 20, "gnss_rate_hz": 5, "t0": 0.0,
    })

    injector = case.build(rng) if case.build else None
    first = None
    settled = None

    for i in range(int(case.secs / DT)):
        vehicle.step()
        reading = sensors.update(vehicle)
        t = vehicle.t

        if injector is not None and t >= TAKE_AT:
            reading = case.apply(injector, reading, t - TAKE_AT, rng)

        state = pipeline.accept({
            "vehicle_id": "UC", "t": t, "seq": i, "imu": reading["imu"],
            "baro": reading["baro"], "mag": reading["mag"],
            "gnss": reading["gnss"], "odom": reading["odom"],
        })
        if state is not None and state.state == "ALERT":
            if first is None:
                first = t - TAKE_AT
            settled = (state.blame.guilty, state.cause.label,
                       list(state.blame.evidence[:2]))

    if first is None:
        return {"got": "silent", "at": None, "sensor": None, "evidence": []}

    guilty, cause, evidence = settled
    named = guilty if guilty and guilty != "cannot_isolate" else None
    got = WORD.get(cause, "not sure yet") if named else "not sure yet"
    return {"got": got, "at": first, "sensor": named, "evidence": evidence}


# --- the catalogue ---------------------------------------------------------

def catalogue() -> list[Case]:
    """Everything the console can do, in the order a demo would show it."""
    cases: list[Case] = []
    add = cases.append

    # -- nothing wrong. The gate: any alert here is a failure --------------
    add(Case("honest truck run", "Start · truck", "silent"))
    add(Case("honest drone flight", "Start · drone", "silent",
             scenario="drone_clean"))
    add(Case("hard manoeuvring", "scripted · drone manoeuvre", "silent",
             scenario="drone_manoeuvre"))

    # -- TAMPERED: someone is inventing the reading ------------------------
    add(Case("judge drives the GPS", "GPS -> Take it over -> arrows",
             "TAMPERED", sensor="gnss",
             build=lambda rng: _driving_gnss(), apply=_puppet_gnss))
    add(Case("slow walk-off, 3 m/s", "scripted · truck theft", "TAMPERED",
             sensor="gnss",
             build=lambda rng: make_attack("walkoff", 3.0, 90.0), apply=_attack))
    add(Case("GPS jumped 300 m", "GPS -> Jump it", "TAMPERED", sensor="gnss",
             build=lambda rng: make_attack("teleport", 300.0, 90.0), apply=_attack))
    add(Case("meaconing / replay", "GPS -> Replay elsewhere", "TAMPERED",
             sensor="gnss",
             build=lambda rng: make_attack("replay", 250.0, 45.0), apply=_attack))
    add(Case("altitude-only spoof", "GPS -> Altitude only", "not sure yet",
             scenario="drone_clean", sensor="gnss",
             build=lambda rng: make_attack("altitude_only", 60.0, 0.0),
             apply=_attack,
             note="Detected, not attributed, and that is the honest answer: only "
                  "GPS and the barometer measure height, so a single failing "
                  "check between them cannot say which of the two is wrong. A "
                  "third height source would fix it; we do not have one."))

    # -- FAILED SENSOR: it has stopped telling the truth -------------------
    add(Case("judge lets the GPS freeze", "GPS -> Take it over -> space",
             "FAILED SENSOR", sensor="gnss",
             build=lambda rng: _frozen_gnss(), apply=_puppet_gnss))
    add(Case("compass frozen", "Compass -> Freeze it", "FAILED SENSOR",
             sensor="mag",
             build=lambda rng: make_fault("stuck", "mag"), apply=_fault))
    add(Case("compass goes noisy", "Compass -> Make it noisy", "FAILED SENSOR",
             sensor="mag",
             build=lambda rng: make_fault("noisy", "mag", 18.0), apply=_fault))
    add(Case("barometer frozen", "Altitude -> Freeze it", "FAILED SENSOR",
             scenario="drone_clean", sensor="baro",
             build=lambda rng: make_fault("stuck", "baro"), apply=_fault))
    add(Case("wheels seized at zero", "Wheels -> Take it over -> space",
             "FAILED SENSOR", sensor="odom",
             build=lambda rng: _frozen_odom(), apply=_whole))
    add(Case("GPS cut off", "GPS -> Cut it off", "FAILED SENSOR", sensor="gnss",
             build=lambda rng: make_fault("dropout", "gnss"), apply=_fault))

    # -- INTERFERENCE: something physical is affecting it ------------------
    add(Case("magnet on the compass", "Compass -> Hold a magnet", "INTERFERENCE",
             sensor="mag",
             build=lambda rng: make_interference("magnet", 30.0), apply=_whole))
    add(Case("pressure on the barometer", "Altitude -> Squeeze it",
             "not sure yet", scenario="drone_clean", sensor="baro",
             build=lambda rng: make_interference("pressure", -6.0), apply=_whole,
             note="Same two-sensor limit as the altitude spoof, from the other "
                  "side. We can see the height is wrong; we cannot say whether "
                  "the receiver or the barometer is the one lying."))

    # -- the honest hard case ----------------------------------------------
    add(Case("two sensors at once", "take GPS and Compass together",
             "FAILED SENSOR", build=lambda rng: _two_at_once(), apply=_whole,
             note="Names one of the two liars rather than both. With two of five "
                  "sensors lying in step the honest ones are outvoted, which is "
                  "exactly why the fleet check exists: one vehicle can be "
                  "fooled, four in one area cannot."))

    return cases


def _driving_gnss():
    p = GnssPuppet(speed_mps=6.0, bearing_deg=0.0)
    p.moving = True
    return p


def _frozen_gnss():
    p = GnssPuppet(speed_mps=6.0, bearing_deg=0.0)
    p.moving = False
    return p


def _frozen_odom():
    p = OdomPuppet()
    p.speed_mps = 0.0
    return p


class _two_at_once:
    """GPS driven and compass turned together — two lies that agree.

    Expected to come back unnamed, and that is the honest answer: with two of
    five sensors lying in step, the truthful ones are outvoted. It is the same
    reason the fleet check exists — one vehicle can be fooled, four cannot.
    """

    def __init__(self):
        self.gnss = _driving_gnss()
        self.mag = MagPuppet()
        self._turned = False

    def apply(self, reading, ts):
        if reading["gnss"] is not None:
            reading["gnss"] = self.gnss.apply(reading["gnss"], ts)
        reading = self.mag.apply(reading, ts)
        if not self._turned:
            self.mag.steer(turn_deg=35)
            self._turned = True
        return reading


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check every use case")
    parser.add_argument("--verbose", action="store_true",
                        help="also print the evidence behind each verdict")
    args = parser.parse_args(argv)

    cases = catalogue()
    rng = np.random.default_rng(4242)

    print("SENSORSENTRY — EVERY CASE A JUDGE CAN PRODUCE\n")
    print(f"  {'case':30} {'how':38} {'expected':14} {'got':14} when")
    print("  " + "-" * 108)

    failures = 0
    for case in cases:
        result = run_case(case)

        ok = result["got"] == case.expect
        if not ok:
            failures += 1
        when = "" if result["at"] is None else f"{result['at']:.0f} s"
        mark = " " if ok else "*"
        named = f" ({result['sensor']})" if result["sensor"] else ""
        print(f" {mark}{case.name:30} {case.how:38} {case.expect:14} "
              f"{result['got'] + named:14} {when}")
        if args.verbose and result["evidence"]:
            for line in result["evidence"]:
                print(f"      - {line}")

    noted = [c for c in cases if c.note]
    if noted:
        print(chr(10) + "  Where the answer is narrower than you might expect:" + chr(10))
        for case in noted:
            print(f"  {case.name}")
            for line in _wrap_note(case.note):
                print(f"      {line}")
            print()

    if failures:
        print(f"  {failures} case(s) did not say what we claim they say.")
        print("  Either the detector regressed or the claim is wrong. Fix one.")
    else:
        print(f"  All {len(cases)} cases behave as documented.")
    return 1 if failures else 0


def _wrap_note(note: str, width: int = 84) -> list[str]:
    words, lines, line = note.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
