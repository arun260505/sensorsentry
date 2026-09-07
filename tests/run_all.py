"""Test runner. Plain asserts, no pytest — no dev dependency to install.

    python -m tests.run_all
"""

from __future__ import annotations

import math
import sys
import traceback
from typing import Callable

from detector import health, profiles
from detector.deadreckon import DeadReckoner
from detector.geo import ENU, Origin, enu_from_llh, heading_from_yaw, llh_from_enu, wrap_pi, yaw_from_heading
from detector.ingest import FrameStream, SchemaViolation, validate_frame
from detector.crossvalidate import CrossValidator
from detector.residual import ResidualTracker
from harness import fixtures

_TESTS: list[tuple[str, Callable[[], None]]] = []


def test(fn: Callable[[], None]) -> Callable[[], None]:
    _TESTS.append((fn.__name__, fn))
    return fn


def close(a: float, b: float, tol: float, what: str = "") -> None:
    assert abs(a - b) <= tol, f"{what}: {a!r} != {b!r} within {tol}"


# --- geo ------------------------------------------------------------------

@test
def enu_round_trips() -> None:
    origin = Origin(11.0168, 76.9558, 411.0)
    for e, n, u in [(0, 0, 0), (1500, -800, 60), (-40, 40, -12)]:
        lat, lon, alt = llh_from_enu(ENU(e, n, u), origin)
        back = enu_from_llh(lat, lon, alt, origin)
        close(back.e, e, 0.01, "east")
        close(back.n, n, 0.01, "north")
        close(back.u, u, 0.01, "up")


@test
def heading_and_yaw_are_inverse() -> None:
    for heading in (0.0, 45.0, 90.0, 180.0, 271.5, 359.9):
        close(heading_from_yaw(yaw_from_heading(heading)), heading, 1e-9, "heading")
    # North is +north in ENU: yaw pi/2.
    close(yaw_from_heading(0.0), math.pi / 2, 1e-9, "north yaw")
    # East is +east: yaw 0.
    close(yaw_from_heading(90.0), 0.0, 1e-9, "east yaw")


@test
def angle_difference_wraps_at_north() -> None:
    # 359 deg vs 1 deg is a 2 degree error, not 358.
    diff = wrap_pi(math.radians(1.0) - math.radians(359.0))
    close(math.degrees(diff), 2.0, 1e-9, "wrapped difference")


# --- ingest ---------------------------------------------------------------

def _good_frame(**over) -> dict:
    frame = {
        "vehicle_id": "T-1", "t": 1.0, "seq": 20,
        "imu": {"ax": 0.0, "ay": 0.0, "az": 9.8, "gx": 0.0, "gy": 0.0, "gz": 0.0},
        "baro": {"pressure_hpa": 964.0},
        "mag": {"heading_deg": 45.0},
        "gnss": None, "odom": None,
    }
    frame.update(over)
    return frame


@test
def valid_frame_passes() -> None:
    validate_frame(_good_frame())


@test
def truth_in_a_frame_is_rejected() -> None:
    for leak in ({"true_lat": 11.0}, {"attack_active": True}, {"scenario": "x"}):
        try:
            validate_frame(_good_frame(**leak))
        except SchemaViolation:
            continue
        raise AssertionError(f"forbidden field {list(leak)} was accepted")


@test
def truth_nested_deep_is_rejected() -> None:
    frame = _good_frame()
    frame["gnss"] = {"lat": 1.0, "lon": 2.0, "alt": 3.0, "truth": {"lat": 1.0}}
    try:
        validate_frame(frame)
    except SchemaViolation:
        return
    raise AssertionError("nested forbidden field was accepted")


@test
def unknown_field_is_rejected() -> None:
    try:
        validate_frame(_good_frame(extra_thing=1))
    except SchemaViolation:
        return
    raise AssertionError("unknown field was accepted")


@test
def stream_computes_dt_and_spots_gaps() -> None:
    stream = FrameStream()
    assert stream.accept({
        "type": "run_start", "run_id": "r1", "vehicle_id": "T-1",
        "vehicle_type": "drone", "seed": 7, "rate_hz": 20, "gnss_rate_hz": 5, "t0": 0.0,
    }) is None
    assert stream.header is not None and stream.header.seed == 7

    first = stream.accept(_good_frame(t=0.00, seq=0))
    assert first is not None and first.dt == 0.0

    second = stream.accept(_good_frame(t=0.05, seq=1))
    assert second is not None
    close(second.dt, 0.05, 1e-9, "dt")
    assert second.dropped == 0

    # seq jumps 1 -> 4, so two frames went missing.
    third = stream.accept(_good_frame(t=0.20, seq=4))
    assert third is not None and third.dropped == 2, third.dropped
    assert stream.frames_dropped == 2


@test
def out_of_order_frame_is_discarded() -> None:
    stream = FrameStream()
    stream.accept(_good_frame(t=1.0, seq=20))
    assert stream.accept(_good_frame(t=0.9, seq=19)) is None


# --- health ---------------------------------------------------------------

@test
def clean_run_reports_no_faults() -> None:
    monitor = health.HealthMonitor(profiles.DRONE)
    stream = FrameStream()
    unhealthy = 0
    for raw in fixtures.frames(duration_s=20.0):
        frame = stream.accept(raw)
        if frame is None:
            continue
        report = monitor.update(frame)
        unhealthy += sum(0 if h.healthy else 1 for h in report.values())
    assert unhealthy == 0, f"{unhealthy} health flags on a clean run"


@test
def frozen_sensor_is_caught() -> None:
    monitor = health.HealthMonitor(profiles.DRONE)
    stream = FrameStream()
    flagged = False
    for raw in fixtures.frames(duration_s=10.0):
        if raw.get("type") == "run_start":
            stream.accept(raw)
            continue
        if raw["t"] > 3.0:
            raw["baro"]["pressure_hpa"] = 964.0  # stuck to the bit
        frame = stream.accept(raw)
        assert frame is not None
        report = monitor.update(frame)
        if health.STUCK in report[profiles.BARO].flags:
            flagged = True
    assert flagged, "a frozen barometer was not detected"


@test
def out_of_range_reading_is_caught() -> None:
    monitor = health.HealthMonitor(profiles.DRONE)
    stream = FrameStream()
    frame = stream.accept(_good_frame(imu={
        "ax": 500.0, "ay": 0.0, "az": 9.8, "gx": 0.0, "gy": 0.0, "gz": 0.0
    }))
    assert frame is not None
    report = monitor.update(frame)
    assert health.OUT_OF_RANGE in report[profiles.IMU].flags


# --- dead reckoning -------------------------------------------------------

def _run_witness(duration_s: float, track: fixtures.Track, seed: int = 7):
    stream = FrameStream()
    reckoner = DeadReckoner(profiles.DRONE)
    witness = None
    for raw in fixtures.frames(duration_s=duration_s, track=track, seed=seed):
        frame = stream.accept(raw)
        if frame is None:
            continue
        witness = reckoner.update(frame)
    assert witness is not None
    return witness


def _witness_error(witness, track) -> float:
    """How far the witness is from where the vehicle actually went."""
    true_e, true_n, _ = track.position_enu(witness.elapsed_s)
    return math.hypot(witness.displacement.e - true_e, witness.displacement.n - true_n)


@test
def witness_tracks_a_clean_straight_line() -> None:
    track = fixtures.Track(speed_mps=12.0, bearing_deg=45.0)
    witness = _run_witness(30.0, track)
    error = _witness_error(witness, track)
    # Sensor noise alone, no bias: tens of metres over 30 s would mean the
    # integration is wrong, not merely noisy.
    assert error < 30.0, f"witness drifted {error:.1f} m with no bias applied"


@test
def witness_uncertainty_grows_with_time() -> None:
    track = fixtures.Track()
    short = _run_witness(10.0, track)
    long = _run_witness(60.0, track)
    assert long.sigma_m > short.sigma_m * 4.0, "sigma should grow with t^2"
    # The band the simulator is tuned against: see the handover self-check.
    assert 20.0 <= long.sigma_m <= 200.0, f"sigma at 60 s = {long.sigma_m:.1f} m"


@test
def witness_keeps_up_through_acceleration() -> None:
    """Regression test for the worst bug found in this module.

    An accelerometer cannot tell tilting from accelerating. If the gravity
    correction fires while the vehicle is speeding up, it writes a false pitch,
    the gyro then preserves it, gravity leaks into the forward axis, and the
    witness silently under-reads speed for the rest of the run — it read
    7.1 m/s on a 12 m/s vehicle and lost 20 m of distance, with nothing in the
    output looking obviously wrong.

    So: after accelerating, the witness must agree with reality about how fast
    it is going.
    """
    track = fixtures.Track(speed_mps=12.0, ramp_s=5.0, hold_s=3.0)
    witness = _run_witness(30.0, track)
    speed = math.hypot(witness.velocity.e, witness.velocity.n)
    expected, _ = track.speed_at(witness.elapsed_s)
    assert abs(speed - expected) < 0.15 * expected, (
        f"witness thinks it is doing {speed:.1f} m/s, actually {expected:.1f} — "
        "the tilt gate is letting a correction through under acceleration"
    )


@test
def constant_horizontal_bias_is_rejected() -> None:
    """A property worth locking down, because it is easy to lose.

    The gravity correction absorbs a steady horizontal accelerometer bias into
    a small pitch offset that then cancels it. That is what a complementary
    filter is for, and it means our drift is set by manoeuvres and heading
    rather than by bias — which is why the sigma model in residual.py, built on
    bias alone, needs calibrating against the real simulator.
    """
    def error(bias) -> float:
        track = fixtures.Track(accel_bias=bias)
        return sum(
            _witness_error(_run_witness(45.0, track, seed=s), track) for s in range(6)
        ) / 6.0

    clean = error((0.0, 0.0, 0.0))
    biased = error((0.20, 0.0, 0.0))
    assert biased < clean * 2.0 + 5.0, (
        f"a steady bias should be absorbed, not integrated: {biased:.1f} vs {clean:.1f} m"
    )


@test
def witness_drift_grows_with_time() -> None:
    """Free-running dead reckoning gets worse the longer it runs. If this ever
    stops being true, something is quietly reading GNSS."""
    def mean_error(duration: float) -> float:
        track = fixtures.Track(accel_bias=(0.03, 0.0, 0.0))
        return sum(
            _witness_error(_run_witness(duration, track, seed=s), track)
            for s in range(6)
        ) / 6.0

    assert mean_error(60.0) > mean_error(20.0)


@test
def witness_never_reads_gnss() -> None:
    """Strip GNSS entirely; the witness must be bit-identical."""
    track = fixtures.Track(accel_bias=(0.03, -0.01, 0.0))

    def run(strip: bool):
        stream, reckoner, out = FrameStream(), DeadReckoner(profiles.DRONE), None
        for raw in fixtures.frames(duration_s=25.0, track=track, seed=99):
            if strip and raw.get("type") != "run_start":
                raw["gnss"] = None
            frame = stream.accept(raw)
            if frame is not None:
                out = reckoner.update(frame)
        return out

    with_gnss, without_gnss = run(False), run(True)
    assert with_gnss.displacement == without_gnss.displacement, (
        "the witness changed when GNSS was removed — it is reading GNSS "
        "somewhere, which breaks CLAUDE.md rule 2"
    )


# --- residual -------------------------------------------------------------

def _run_residual(duration_s: float, track, spoof, seed: int = 5):
    stream = FrameStream()
    reckoner = DeadReckoner(profiles.DRONE)
    tracker = ResidualTracker()
    peak_ratio, last = 0.0, None
    for raw in fixtures.frames(duration_s=duration_s, track=track, spoof=spoof, seed=seed):
        frame = stream.accept(raw)
        if frame is None:
            continue
        witness = reckoner.update(frame)
        residual = tracker.update(frame, witness)
        if residual is not None:
            peak_ratio = max(peak_ratio, residual.ratio)
            last = residual
    return peak_ratio, last


def _peak_pair_ratios(duration_s: float, spoof) -> dict[str, float]:
    """Highest ratio each cross-check reached over a run."""
    stream = FrameStream()
    reckoner = DeadReckoner(profiles.DRONE)
    tracker = ResidualTracker(profiles.DRONE.accel_bias_sigma)
    validator = CrossValidator(profiles.DRONE)
    peaks: dict[str, float] = {}
    for raw in fixtures.frames(duration_s=duration_s, spoof=spoof, seed=5):
        frame = stream.accept(raw)
        if frame is None:
            continue
        witness = reckoner.update(frame)
        tracker.update(frame, witness)
        for score in validator.update(frame, witness, tracker.origin):
            if score.valid:
                key = f"{score.a}-{score.b}"
                peaks[key] = max(peaks.get(key, 0.0), score.ratio)
    return peaks


@test
def clean_run_keeps_residual_small() -> None:
    peak, last = _run_residual(60.0, fixtures.Track(), None)
    assert last is not None, "no residual was ever produced"
    assert peak < 3.0, f"clean run peaked at ratio {peak:.2f} — false alarms ahead"


@test
def walkoff_spoof_separates_the_paths() -> None:
    """A walk-off must show up in the cross-checks.

    Asserted on the pair scores, not on the position residual. Since GNSS aids
    the witness, a spoof drags the inertial estimate part-way along with it and
    the absolute gap between the two stops being a reliable measure — which is
    the whole reason detection moved to crossvalidate.py.
    """
    spoof = fixtures.Spoof(start_t=20.0, speed_mps=2.0, bearing_deg=135.0)
    clean = _peak_pair_ratios(60.0, None)
    attacked = _peak_pair_ratios(60.0, spoof)

    worst_clean = max(clean.values(), default=0.0)
    worst_attacked = max(attacked.values(), default=0.0)
    assert worst_attacked > worst_clean * 1.5, (
        f"attack barely moved the pairs: clean {worst_clean:.2f} "
        f"vs attacked {worst_attacked:.2f}"
    )
    # The course check is the one that should carry it.
    assert attacked.get("gnss-mag", 0.0) > clean.get("gnss-mag", 0.0), (
        "the course-vs-compass check did not react to a sideways pull"
    )


@test
def residual_grows_with_spoof_speed() -> None:
    """The monotonic relationship the demo's failure-boundary table rests on:
    harder attacks are easier to see. Measured on the residual itself, not the
    ratio — the uncertainty model it divides by is not calibrated yet."""
    measured = []
    for speed in (0.0, 1.0, 2.0, 5.0):
        _peak, last = _run_residual(
            60.0, fixtures.Track(), fixtures.Spoof(start_t=20.0, speed_mps=speed)
        )
        assert last is not None
        measured.append(last.horizontal_m)

    for slower, faster in zip(measured, measured[1:]):
        assert faster > slower, f"not monotonic: {measured}"
    assert measured[-1] > measured[0] * 10, f"5 m/s barely moved it: {measured}"


@test
def anchor_is_taken_once_and_never_moves() -> None:
    stream = FrameStream()
    reckoner = DeadReckoner(profiles.DRONE)
    tracker = ResidualTracker()
    origins = []
    spoof = fixtures.Spoof(start_t=10.0, speed_mps=4.0)
    for raw in fixtures.frames(duration_s=40.0, spoof=spoof):
        frame = stream.accept(raw)
        if frame is None:
            continue
        tracker.update(frame, reckoner.update(frame))
        if tracker.origin is not None:
            origins.append(tracker.origin)
    assert origins, "never anchored"
    assert all(o == origins[0] for o in origins), "anchor moved during the run"


# --- runner ---------------------------------------------------------------

def main() -> int:
    passed, failed = 0, []
    for name, fn in _TESTS:
        try:
            fn()
        except Exception:
            failed.append(name)
            print(f"FAIL  {name}")
            traceback.print_exc()
            print()
        else:
            passed += 1
            print(f"ok    {name}")

    print(f"\n{passed} passed, {len(failed)} failed")
    if failed:
        print("failing: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
