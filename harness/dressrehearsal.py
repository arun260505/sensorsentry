"""The whole demo, driven end to end against the running servers.

    python -m detector.server        # terminal 1
    python -m simulator.control      # terminal 2
    python -m harness.dressrehearsal # terminal 3

Every other check in `harness/` drives `Pipeline` directly. This one presses
the buttons: it goes through the browser's own endpoints, in the order the
demo runs, and reports how long each beat took to land.

That gap is where the last several days of real faults lived — a stale server
answering with last week's map, a console that ignored the arrow keys, a
height row on a lorry that has no barometer. None of them were visible to the
sixty-six tests, because none of them were in the pipeline.

It is not a substitute for standing up and saying the words. It is the part a
machine can do, so that when you do stand up, the only thing left to go wrong
is you.

Exits non-zero if any beat misses, so it can sit in front of a commit.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"

SETTLE_S = 18.0
"""How long the vehicle is given before anything is done to it. Under about
fifteen it is still pulling away, and the sharpest check needs road speed on a
straight stretch — attacking early tests nothing and looks like a failure."""


def post(path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())


def snapshot() -> dict:
    with urllib.request.urlopen(BASE + "/snapshot", timeout=6) as response:
        return json.loads(response.read())


def vehicle_state() -> dict | None:
    vehicles = snapshot().get("vehicles") or {}
    return next(iter(vehicles.values()))["state"] if vehicles else None


WORD = {"attack": "TAMPERED", "fault": "FAILED SENSOR",
        "interference": "INTERFERENCE"}


def verdict(state: dict) -> tuple[str, str | None]:
    guilty = state["blame"]["guilty"]
    cause = state["cause"]["label"]
    if not guilty or guilty == "cannot_isolate":
        return "not sure yet", None
    return WORD.get(cause, "not sure yet"), guilty


def wait_for(predicate, budget_s: float, poll_s: float = 0.3):
    """Wait for something to become true. Returns seconds taken, or None."""
    start = time.time()
    while time.time() - start < budget_s:
        state = vehicle_state()
        if state is not None and predicate(state):
            return time.time() - start, state
        time.sleep(poll_s)
    return None, vehicle_state()


class Rehearsal:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.failures: list[str] = []
        self.beats: list[tuple[str, str, float | None]] = []

    def beat(self, name: str, detail: str, seconds: float | None,
             ok: bool) -> None:
        self.beats.append((name, detail, seconds))
        when = "" if seconds is None else f"{seconds:5.1f}s"
        print(f"  {'ok  ' if ok else 'MISS'} {name:34} {detail:34} {when}")
        if not ok:
            self.failures.append(name)

    def reset(self) -> None:
        post("/control/clear")
        time.sleep(1.0)

    def start(self, scenario: str) -> None:
        """Clear, start, and wait until the new run is genuinely being judged.

        A fixed sleep is not enough, and the way it fails is the worst way
        available: this harness exists to say whether a build is safe to show,
        and if it reports a false alarm on a clean run because the *previous*
        beat's alert was still on screen, the operator goes looking for a fault
        that is not there — or, worse, learns to shrug at a red gate.

        So wait for the new run's own frames, and for the verdict to be OK,
        before starting any clock. Both, because a run can be streaming
        while the state still carries the last incident.
        """
        self.reset()
        post("/control/start", {"scenario": scenario})
        time.sleep(SETTLE_S)

        deadline = time.time() + 12.0
        while time.time() < deadline:
            state = vehicle_state()
            if state is not None and state.get("state") == "OK" \
                    and (state.get("t") or 0.0) > 0.5:
                return
            time.sleep(0.3)
        # Fall through rather than raise: a beat reporting what it actually
        # saw is more use than the harness dying halfway down the run sheet.


def scene_quiet(run: Rehearsal, scenario: str, watch_s: float = 25.0) -> None:
    """Nothing is wrong, and the screen must agree. The gate."""
    run.start(scenario)
    took, state = wait_for(lambda s: s["state"] == "ALERT", watch_s)
    run.beat(f"{scenario} stays quiet",
             "alerted" if took else "silent throughout", None, took is None)


def scene_attack(run: Rehearsal, name: str, scenario: str, inject: dict,
                 steer: dict | None, want: str, budget_s: float) -> None:
    """Do something to a sensor and wait for the verdict to settle on it."""
    run.start(scenario)
    post("/control/inject", inject)
    if steer:
        time.sleep(0.6)
        post("/control/steer", steer)

    took, state = wait_for(
        lambda s: s["state"] == "ALERT" and verdict(s)[0] == want, budget_s)
    if took is None:
        got = verdict(state)[0] if state else "no vehicle"
        run.beat(name, f"wanted {want}, got {got}", None, False)
        return
    run.beat(name, f"{want} · {verdict(state)[1]}", took, True)


def scene_parked(run: Rehearsal) -> None:
    """The closing beat: park a spoofed lorry and let the numbers speak."""
    run.start("truck_clean")
    post("/control/inject", {"kind": "attack", "type": "walkoff",
                             "strength": 4.0, "bearing_deg": 90.0})
    time.sleep(8)
    post("/control/hold", {"hold": True})

    # Wait for the wheels to reach zero — that is the event — and then look at
    # once. Measured over three runs: the wheels are at exactly 0.00 within
    # eight seconds of the hold, while the dead-reckoned estimate bottoms out
    # around 0.4-1.2 m/s and then **climbs slowly back**, because that is
    # accelerometer bias integrating with nothing to correct it.
    #
    #   since hold      8s     30s
    #   wheels        0.00    0.00
    #   own estimate  1.16    1.75
    #
    # So waiting longer makes this beat *less* likely to pass, which is how a
    # fixed fourteen-second sleep failed at 2.04 against a 2.0 threshold — and
    # why the first attempt at fixing it, waiting even longer, was exactly
    # backwards. Same on stage: make the point promptly. If the parked lorry
    # is left on screen for a minute, our own estimate visibly creeps, which is
    # the dead-reckoning drift already on the card and worth naming rather than
    # being caught by.
    deadline = time.time() + 20.0
    while time.time() < deadline:
        wheels_now = (snapshot().get("raw") or {}).get("odom") or {}
        if (wheels_now.get("wheel_speed_mps") or 1.0) < 0.1:
            break
        time.sleep(0.3)

    state = vehicle_state()
    if state is None:
        run.beat("park the spoofed lorry", "no vehicle", None, False)
        return
    wheels = (snapshot().get("raw") or {}).get("odom") or {}
    speed = wheels.get("wheel_speed_mps")
    own = (state.get("witness") or {}).get("speed_mps")
    stopped = speed is not None and speed < 0.5 and own is not None and own < 2.0
    run.beat("park the spoofed lorry",
             f"wheels {speed:.1f} m/s, own {own:.1f} m/s" if stopped
             else f"still moving ({speed}, {own})", None, stopped)
    post("/control/hold", {"hold": False})


def scene_reset(run: Rehearsal) -> None:
    """Phase 12 asks for a reset under two seconds. Measure it."""
    run.start("truck_clean")
    post("/control/inject", {"kind": "puppet", "sensor": "gnss"})
    time.sleep(3)
    started = time.time()
    post("/control/clear")
    elapsed = time.time() - started
    time.sleep(1.5)
    empty = not (snapshot().get("vehicles") or {})
    run.beat("clear stops the run", "screen empty" if empty else "still streaming",
             elapsed, empty and elapsed < 2.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the demo end to end")
    parser.add_argument("--quick", action="store_true",
                        help="skip the slow walk-off beat")
    args = parser.parse_args(argv)

    try:
        basemap = json.loads(
            urllib.request.urlopen(BASE + "/basemap", timeout=6).read())
    except Exception as exc:
        print(f"nothing answering on {BASE}: {exc}")
        print("start both servers first — see docs/RUN-SHEET.md")
        return 2

    print("SENSORSENTRY — DRESS REHEARSAL\n")
    print(f"  map: {len(basemap.get('roads', []))} real roads, "
          f"route {len(basemap.get('route', []))} points\n")

    run = Rehearsal(verbose=False)

    print("Scene 1 — nothing is wrong")
    scene_quiet(run, "truck_clean")
    scene_quiet(run, "drone_clean")

    print("\nScene 2 — the judge drives the GPS, then lets go")
    scene_attack(run, "drive the GPS away", "truck_clean",
                 {"kind": "puppet", "sensor": "gnss"},
                 {"target": "gps", "bearing_deg": 0, "moving": True},
                 "TAMPERED", 45)
    scene_attack(run, "let the GPS freeze", "truck_clean",
                 {"kind": "puppet", "sensor": "gnss"},
                 {"target": "gps", "moving": False},
                 "FAILED SENSOR", 30)

    print("\nScene 3 — the other three sensors")
    scene_attack(run, "magnet on the compass", "truck_clean",
                 {"kind": "interference", "type": "magnet", "strength": 30},
                 None, "INTERFERENCE", 40)
    scene_attack(run, "wheels seized at zero", "truck_clean",
                 {"kind": "puppet", "sensor": "odom"},
                 {"target": "wheels", "speed_mps": 0}, "FAILED SENSOR", 40)
    scene_attack(run, "barometer frozen", "drone_clean",
                 {"kind": "fault", "type": "stuck", "sensor": "baro"},
                 None, "FAILED SENSOR", 40)

    if not args.quick:
        print("\nScene 4 — the slow one, the way a thief would do it")
        scene_attack(run, "slow walk-off, 3 m/s", "truck_clean",
                     {"kind": "attack", "type": "walkoff",
                      "strength": 3.0, "bearing_deg": 90.0},
                     None, "TAMPERED", 110)

    print("\nScene 5 — park it")
    scene_parked(run)

    print("\nScene 6 — reset for the next question")
    scene_reset(run)

    post("/control/clear")
    print()
    if run.failures:
        print(f"  {len(run.failures)} beat(s) missed: {', '.join(run.failures)}")
        print("  Do not run the demo on this build.")
        return 1
    print(f"  All {len(run.beats)} beats landed. The machine half is rehearsed;")
    print("  now do it out loud, with the wifi off, from docs/RUN-SHEET.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
