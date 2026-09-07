"""Test runner. Plain asserts, no pytest — no dev dependency to install.

    python -m tests.run_all
"""

from __future__ import annotations

import math
import sys
import traceback
from typing import Callable

import numpy as np

from detector import health, profiles
from detector.deadreckon import DeadReckoner
from detector.geo import ENU, Origin, enu_from_llh, heading_from_yaw, llh_from_enu, wrap_pi, yaw_from_heading
from detector.ingest import FrameStream, SchemaViolation, validate_frame
from detector import blame as blame_mod
from detector import classify as classify_mod
from detector import evidence as evidence_mod
from detector import fusion as fusion_mod
from detector import report as report_mod
from detector.crossvalidate import CrossValidator, PairScore
from detector.pipeline import Pipeline
from detector.residual import ResidualTracker
from fleet import advisory as adv_mod
from fleet import cluster as cluster_mod
from harness import fixtures
from simulator.attacks import AltitudeOnly, Replay, Teleport, WalkOff
from simulator.faults import Bias, Dropout, Noisy, Stuck
from simulator.interference import Magnet, Pressure
from simulator.roads import distance_to_nearest_road, nearest_road_name
from simulator.scenarios import get_scenario
from simulator.sensors import SensorSuite
from simulator.vehicle import make_vehicle

DT = 1.0 / 20.0

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


# --- blame ----------------------------------------------------------------

def _pair(a, b, kind, domain, ratio, valid=True):
    return PairScore(a=a, b=b, label=f"{a} vs {b}", kind=kind, domain=domain,
                     ratio=ratio, valid=valid)


@test
def blame_names_gnss_when_the_compass_is_corroborated() -> None:
    """A walk-off breaks course-vs-compass. The compass still agrees with the
    gyro, so it is cleared and GNSS is left holding the failure."""
    pairs = [
        _pair("gnss", "mag", "course_mag", "heading", 4.0),
        _pair("mag", "imu", "heading_offset", "heading", 1.1),
    ]
    states = {pairs[0].key: "ALERT", pairs[1].key: "OK"}
    verdict = blame_mod.assign(pairs, states)
    assert verdict.guilty == "gnss", verdict.guilty
    assert any("mag" in c for c in verdict.cleared), verdict.cleared


@test
def blame_names_the_compass_when_it_fails_everything() -> None:
    """A magnet breaks both checks the compass is in, while GNSS and the gyro
    are each in only one."""
    pairs = [
        _pair("gnss", "mag", "course_mag", "heading", 9.0),
        _pair("mag", "imu", "heading_offset", "heading", 3.0),
    ]
    states = {p.key: "ALERT" for p in pairs}
    verdict = blame_mod.assign(pairs, states)
    assert verdict.guilty == "mag", verdict.guilty


@test
def blame_refuses_to_guess_from_a_single_check() -> None:
    """One failing check names two sensors and gives no way to choose. The
    system is allowed to say it does not know — judges test this exact case."""
    pairs = [_pair("gnss", "mag", "course_mag", "heading", 5.0)]
    states = {pairs[0].key: "ALERT"}
    verdict = blame_mod.assign(pairs, states)
    assert verdict.guilty == blame_mod.CANNOT_ISOLATE, verdict.guilty
    assert not verdict.isolated


@test
def blame_stays_silent_when_nothing_is_failing() -> None:
    pairs = [_pair("gnss", "mag", "course_mag", "heading", 1.0)]
    verdict = blame_mod.assign(pairs, {pairs[0].key: "OK"})
    assert verdict.guilty is None
    assert not verdict.isolated


@test
def blame_will_not_clear_a_sensor_on_unrelated_evidence() -> None:
    """GNSS passing an altitude check says nothing about it lying
    horizontally. Cross-domain alibis let the real culprit walk free."""
    pairs = [
        _pair("gnss", "mag", "course_mag", "heading", 6.0),
        _pair("gnss", "baro", "altitude", "vertical", 1.0),
        _pair("mag", "imu", "heading_offset", "heading", 1.0),
    ]
    states = {pairs[0].key: "ALERT", pairs[1].key: "OK", pairs[2].key: "OK"}
    verdict = blame_mod.assign(pairs, states)
    assert verdict.guilty == "gnss", verdict.guilty
    assert all("baro" not in c for c in verdict.cleared), (
        "an altitude check cleared a horizontal suspect"
    )


@test
def a_failed_self_check_strengthens_the_case() -> None:
    """A sensor failing on its own terms outweighs one cross-check."""
    pairs = [
        _pair("gnss", "mag", "course_mag", "heading", 4.0),
        _pair("mag", "imu", "heading_offset", "heading", 4.0),
    ]
    states = {p.key: "ALERT" for p in pairs}
    sick = health.SensorHealth("mag")
    sick.flag(health.STUCK)
    verdict = blame_mod.assign(pairs, states, {"mag": sick})
    assert verdict.guilty == "mag", verdict.guilty
    assert any("health check" in e for e in verdict.evidence), verdict.evidence


@test
def blame_spots_the_sensor_failing_in_two_different_ways() -> None:
    """A drifting IMU breaks the heading check against the compass and the
    position check against GNSS. Neither domain can accuse anyone on its own —
    both see a two-way tie — but only the IMU is in both."""
    pairs = [
        _pair("mag", "imu", "heading_offset", "heading", 20.0),
        _pair("gnss", "imu", "position", "horizontal", 26.0),
    ]
    states = {p.key: "ALERT" for p in pairs}
    verdict = blame_mod.assign(pairs, states)
    assert verdict.guilty == "imu", verdict.guilty
    assert verdict.domain == "multiple"


@test
def one_domain_alone_is_not_enough_to_count_across_domains() -> None:
    """The counting argument only applies when several kinds of check fail.
    Inside one domain the corroboration rule is stronger and must win."""
    pairs = [
        _pair("gnss", "mag", "course_mag", "heading", 6.0),
        _pair("mag", "imu", "heading_offset", "heading", 1.0),
    ]
    states = {pairs[0].key: "ALERT", pairs[1].key: "OK"}
    verdict = blame_mod.assign(pairs, states)
    assert verdict.guilty == "gnss", verdict.guilty


# --- classify -------------------------------------------------------------

def _classified(guilty, series, domain="heading", health=None, kind="heading_offset"):
    """Push a synthetic error series through the classifier and read the cause."""
    other = "imu" if guilty != "imu" else "mag"
    pair = _pair(guilty, other, kind, domain, 5.0)
    verdict = blame_mod.Blame(guilty=guilty, domain=domain, confidence=1.0)
    engine = classify_mod.Classifier()
    cause = classify_mod.Cause()
    for i, value in enumerate(series):
        pair.signed = value
        cause = engine.update([pair], verdict, health, i * 0.05)
    return cause


@test
def a_steady_one_way_pull_on_gps_is_an_attack() -> None:
    """GPS is told its answer by radio, so a purposeful error means somebody
    is transmitting."""
    ramp = [0.4 * i for i in range(120)]
    cause = _classified("gnss", ramp, domain="heading", kind="course_mag")
    assert cause.label == classify_mod.ATTACK, (cause.label, cause.features)


@test
def an_offset_that_arrives_at_once_is_interference() -> None:
    """A compass measures the field around it, and nobody transmits a magnetic
    field from orbit — so an offset means something is next to it.

    It has to *arrive*, though. Something placed beside a sensor appears
    between one sample and the next; a sensor going bad ramps in, because
    degradation has no reason to happen in fifty milliseconds. The series here
    sits at zero, jumps, and stays."""
    arrives = [0.0] * 45 + [40.0 + (0.3 if i % 2 else -0.3) for i in range(120)]
    cause = _classified("mag", arrives)
    assert cause.label == classify_mod.INTERFERENCE, (cause.label, cause.features)


@test
def an_offset_that_creeps_in_is_the_sensor() -> None:
    """Same size, same direction, no arrival — that is degradation."""
    creeps = [40.0 * (i / 160.0) for i in range(160)]
    cause = _classified("mag", creeps)
    assert cause.label == classify_mod.FAULT, (cause.label, cause.features)


@test
def an_error_thrashing_both_ways_is_a_fault() -> None:
    """Hardware fails messily. An attacker is trying to get somewhere."""
    thrash = [(30.0 if i % 2 else -30.0) for i in range(120)]
    cause = _classified("mag", thrash)
    assert cause.label == classify_mod.FAULT, (cause.label, cause.features)


@test
def a_restless_offset_is_the_sensor_not_the_world() -> None:
    """Coherent, and it wandered in rather than arriving.

    This test used to assert that *steadiness* separated a magnet from a dying
    compass — the idea being that a magnet holds its offset. Measured against
    the simulator that is untrue and backwards: the magnet varied more than the
    failing compass, because once a suspect compass stops re-seeding the gyro
    the reference itself free-runs. What separates them is how the error began.
    """
    restless = [20.0 + 25.0 * math.sin(i / 40.0) for i in range(160)]
    cause = _classified("mag", restless)
    assert cause.label == classify_mod.FAULT, (cause.label, cause.features)


@test
def a_failed_self_check_settles_it_immediately() -> None:
    """A stuck sensor is a broken part, whatever shape its disagreement has —
    and it is judged without waiting for history, because a frozen sensor may
    not be producing a disagreement at all."""
    sick = health.SensorHealth("baro")
    sick.flag(health.STUCK)
    cause = _classified("baro", [1.0], domain="vertical", health={"baro": sick})
    assert cause.label == classify_mod.FAULT
    assert "health check" in cause.reason


@test
def every_cause_carries_a_different_action() -> None:
    """The whole point of stage 6: the three causes demand opposite responses,
    so each must tell the operator something different to do."""
    actions = {classify_mod._ACTIONS[c] for c in
               (classify_mod.ATTACK, classify_mod.FAULT, classify_mod.INTERFERENCE)}
    assert len(actions) == 3


@test
def nothing_wrong_means_no_cause() -> None:
    cause = classify_mod.Classifier().update([], blame_mod.Blame(), {})
    assert cause.label == classify_mod.UNCLASSIFIED
    assert cause.reason == ""


# --- fusion ---------------------------------------------------------------

def _nav_after(guilty, free_s=0.0, state="ALERT"):
    """Drive Fusion with a stubbed witness and tracker."""
    class _Tracker:
        origin = Origin(11.0, 76.9, 400.0)
        correction = ENU(0.0, 0.0, 0.0)
        aiding = guilty != "gnss"
        unaided_s = free_s
        _witness_at_anchor = ENU(0.0, 0.0, 0.0)
        def freeze_aiding(self): self.aiding = False
        def resume_aiding(self): self.aiding = True

    witness = DeadReckoner(profiles.DRONE)._witness()
    tracker = _Tracker()
    blame = blame_mod.Blame(guilty=guilty, domain="heading", confidence=1.0)
    engine = fusion_mod.Fusion(profiles.DRONE)
    reckoner = DeadReckoner(profiles.DRONE)
    nav = engine.update(blame, state, witness, reckoner, tracker)
    return nav, reckoner, tracker


@test
def spoofed_gps_stops_correcting_the_witness() -> None:
    """The whole fallback rests on this. Left aiding, the spoofed fix keeps
    dragging the estimate along and the fallback is decoration."""
    nav, _reckoner, tracker = _nav_after("gnss")
    assert nav is not None
    assert tracker.aiding is False
    assert nav.source == fusion_mod.DEAD_RECKONING
    assert "gnss" in nav.dropped


@test
def a_lying_compass_is_dropped_from_the_witness() -> None:
    """Otherwise the witness steers by a magnetised compass and follows the
    very error it exists to expose."""
    _nav, reckoner, _t = _nav_after("mag")
    assert reckoner.use_compass is False
    assert reckoner.use_baro is True


@test
def a_stuck_barometer_is_dropped_but_the_compass_is_not() -> None:
    _nav, reckoner, _t = _nav_after("baro")
    assert reckoner.use_baro is False
    assert reckoner.use_compass is True


@test
def nothing_is_dropped_while_the_state_is_ok() -> None:
    nav, reckoner, tracker = _nav_after("gnss", state="OK")
    assert nav is not None and nav.source == fusion_mod.GNSS_FUSED
    assert nav.dropped == []
    assert tracker.aiding is True
    assert reckoner.use_compass and reckoner.use_baro


@test
def the_error_budget_only_grows_while_free_running() -> None:
    """And it must never promise better than it delivers — an operator
    deciding whether to press on is entitled to a pessimistic number."""
    early, _r, _t = _nav_after("gnss", free_s=10.0)
    late, _r2, _t2 = _nav_after("gnss", free_s=60.0)
    assert late.error_budget_m > early.error_budget_m * 3
    assert late.seconds_remaining < early.seconds_remaining


@test
def the_operator_is_told_to_stop_once_the_budget_runs_out() -> None:
    nav, _r, _t = _nav_after("gnss", free_s=600.0)
    assert nav.seconds_remaining == 0
    assert "stop" in nav.note.lower() or "land" in nav.note.lower()


@test
def positions_leave_the_detector_as_lat_lon_too() -> None:
    """The fleet map depends on this and cannot detect its absence.

    Every vehicle anchors its own local origin on its own first fix, so local
    metres from two vehicles mean different things. Drawing a fleet from them
    stacks four vehicles on one spot and puts one three kilometres away next
    door — which is exactly what happened. lat/lon is the only frame they
    share.
    """
    stream = FrameStream()
    reckoner = DeadReckoner(profiles.DRONE)
    tracker = ResidualTracker(profiles.DRONE.accel_bias_sigma)
    pipeline = None
    from detector.pipeline import Pipeline
    pipeline = Pipeline()
    payload = None
    for raw in fixtures.frames(duration_s=30.0, seed=5):
        state = pipeline.accept(raw)
        if state is not None and state.gnss_enu is not None:
            payload = state.to_json()
    assert payload is not None, "never anchored"
    for key in ("gnss", "witness"):
        assert payload[key].get("lat") is not None, f"{key} has no lat"
        assert payload[key].get("lon") is not None, f"{key} has no lon"
    # And they must be real coordinates, not zeros left by a missing origin.
    assert abs(payload["gnss"]["lat"]) > 1.0
    assert abs(payload["gnss"]["lon"]) > 1.0


# --- evidence -------------------------------------------------------------

def _record_a_run(directory, spoof_mps=3.0, duration_s=90.0):
    """Fly a spoofed run and write it down exactly as the server would."""
    from detector.pipeline import Pipeline
    recorder = evidence_mod.Recorder(directory)
    pipeline = Pipeline()
    spoof = fixtures.Spoof(start_t=25.0, speed_mps=spoof_mps, bearing_deg=135.0)
    for raw in fixtures.frames(duration_s=duration_s, spoof=spoof, seed=31):
        if raw.get("type") == "run_start":
            recorder.note_run(raw)
        else:
            recorder.note_frame(raw)
        state = pipeline.accept(raw)
        if state is not None:
            recorder.note_state(state.to_json())
    recorder.close()
    return recorder.path


@test
def a_run_is_written_down_frame_by_frame() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _record_a_run(tmp)
        header, frames, incidents = evidence_mod.read(path)
        assert header["seed"] == 31, header
        assert len(frames) > 1000, len(frames)
        assert incidents, "no verdict was ever recorded"


@test
def the_record_holds_no_truth_and_no_attack_state() -> None:
    """It is the frames as they arrived, so replaying it proves the verdict
    came out of sensor data and nothing else — the same argument the demo
    makes live. A record containing the answer would prove nothing."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _record_a_run(tmp)
        _header, frames, _incidents = evidence_mod.read(path)
        for frame in frames:
            validate_frame(frame)      # raises on truth or attack fields


@test
def an_incident_replays_to_the_same_verdict() -> None:
    """An investigator must be able to reach our conclusion themselves rather
    than take our word for it. This is also how we catch ourselves: a change
    that would have called an old incident differently shows up here."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _record_a_run(tmp)
        _header, _frames, recorded = evidence_mod.read(path)
        replayed = evidence_mod.replay(path)
        assert evidence_mod.verdicts_match(recorded, replayed), (
            [(i.state, i.guilty, i.cause) for i in recorded],
            [(i.state, i.guilty, i.cause) for i in replayed],
        )


@test
def a_verdict_is_written_only_when_it_changes() -> None:
    """A line every frame would bury the few moments that matter under
    thousands that repeat them."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _record_a_run(tmp)
        _header, frames, incidents = evidence_mod.read(path)
        assert len(incidents) < 20, len(incidents)
        assert len(incidents) < len(frames) / 50


# --- written report -------------------------------------------------------

@test
def the_report_switch_cannot_touch_detection() -> None:
    """The claim the switch demonstrates, pinned as a test.

    Turning it off must change nothing about what was detected — and it
    cannot, because the report reads a file the detector has already finished
    with. If this ever fails, the on-stage demonstration is a lie.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _record_a_run(tmp)
        _h, _f, before = evidence_mod.read(path)

        off = report_mod.compose(path, enabled=False)
        assert off is None

        on = report_mod.compose(path, enabled=True)
        assert on is not None and on.body

        _h2, _f2, after = evidence_mod.read(path)
        assert [(i.state, i.guilty, i.cause) for i in before] ==                [(i.state, i.guilty, i.cause) for i in after],                "writing a report changed the record"


@test
def the_report_says_what_wrote_it() -> None:
    """A template is not a language model, and claiming otherwise unravels
    under one follow-up question."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        written = report_mod.compose(_record_a_run(tmp))
        assert written.generated_by == "template"


@test
def the_report_names_the_sensor_and_the_cause() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        written = report_mod.compose(_record_a_run(tmp))
        assert "GPS" in written.title or "GPS" in written.body
        assert "attack" in written.title.lower() or "attack" in written.body.lower()
        assert "next steps" in written.body.lower()


@test
def a_quiet_run_produces_a_quiet_report() -> None:
    """No incident must not become an incident report."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        written = report_mod.compose(_record_a_run(tmp, spoof_mps=0.0))
        assert "no incident" in written.title.lower()


# --- fleet ----------------------------------------------------------------

def _incident(vid, t, lat, lon, cause="attack"):
    return cluster_mod.Incident(vehicle_id=vid, t=t, lat=lat, lon=lon,
                                guilty="gnss", cause=cause, confidence=0.9)


@test
def one_attacked_vehicle_draws_no_zone() -> None:
    """It could be a failing receiver, and a wrongly placed circle sends people
    to look in the wrong street."""
    zones = cluster_mod.find_zones([_incident("A", 10.0, 11.00, 76.95)])
    assert zones == []


@test
def vehicles_attacked_together_become_one_zone() -> None:
    """Four in the same area inside a minute is not four broken receivers."""
    near = [
        _incident("A", 10.0, 11.0000, 76.9500),
        _incident("B", 22.0, 11.0060, 76.9530),
        _incident("C", 35.0, 11.0030, 76.9580),
        _incident("D", 48.0, 11.0010, 76.9460),
    ]
    zones = cluster_mod.find_zones(near)
    assert len(zones) == 1, zones
    zone = zones[0]
    assert sorted(zone.vehicles) == ["A", "B", "C", "D"]
    assert 400.0 < zone.radius_m < 4000.0, zone.radius_m
    # The centre should sit among the vehicles, not off in a field.
    for i in near:
        assert cluster_mod.metres_between(zone.lat, zone.lon, i.lat, i.lon) < zone.radius_m


@test
def faults_never_form_a_zone() -> None:
    """A fault belongs to one vehicle. Two compasses failing in the same week
    is coincidence, and a zone drawn round them is a wild goose chase."""
    faults = [
        _incident("A", 10.0, 11.000, 76.950, cause="fault"),
        _incident("B", 20.0, 11.002, 76.952, cause="fault"),
        _incident("C", 30.0, 11.001, 76.951, cause="fault"),
    ]
    assert cluster_mod.find_zones(faults) == []


@test
def attacks_far_apart_are_separate_events() -> None:
    spread = [
        _incident("A", 10.0, 11.000, 76.950),
        _incident("B", 12.0, 11.002, 76.952),
        _incident("C", 14.0, 11.400, 77.400),   # ~60 km away
        _incident("D", 16.0, 11.402, 77.402),
    ]
    zones = cluster_mod.find_zones(spread)
    assert len(zones) == 2, [z.vehicles for z in zones]


@test
def one_vehicle_reporting_repeatedly_cannot_invent_a_zone() -> None:
    """An alert every frame must not be able to outvote the other vehicles."""
    spam = [_incident("A", float(t), 11.000, 76.950) for t in range(0, 40, 2)]
    assert cluster_mod.find_zones(spam) == []


@test
def a_vehicle_heading_for_the_zone_is_warned() -> None:
    """The point of locating the attacker is warning whoever has not reached
    him yet. This is the part a lone vehicle cannot do at all."""
    zones = cluster_mod.find_zones([
        _incident("A", 10.0, 11.000, 76.950),
        _incident("B", 20.0, 11.004, 76.954),
    ])
    assert zones
    approaching = adv_mod.VehicleState("E", 11.010, 76.960, speed_mps=20.0)
    far = adv_mod.VehicleState("F", 12.500, 78.500, speed_mps=20.0)
    out = adv_mod.advise([approaching, far], zones)
    assert [a.vehicle_id for a in out] == ["E"], [a.vehicle_id for a in out]
    assert out[0].seconds_away is None or out[0].seconds_away > 0


@test
def a_vehicle_already_under_attack_is_not_told_to_reroute() -> None:
    """It has a more urgent message already."""
    zones = cluster_mod.find_zones([
        _incident("A", 10.0, 11.000, 76.950),
        _incident("B", 20.0, 11.004, 76.954),
    ])
    victim = adv_mod.VehicleState("A", 11.000, 76.950, 15.0, under_attack=True)
    assert adv_mod.advise([victim], zones) == []

# --- truck (simulator Task 3) ----------------------------------------------


def _truck_injectors(schedule: list[dict]) -> list[dict]:
    """Map a scenario's schedule onto injector objects, exactly as run.py does."""
    registry = {
        "attack":       {cls.__name__: cls for cls in (WalkOff, Teleport, AltitudeOnly, Replay)},
        "fault":        {cls.__name__: cls for cls in (Stuck, Noisy, Dropout, Bias)},
        "interference": {cls.__name__: cls for cls in (Magnet, Pressure)},
    }
    entries = []
    for e in schedule:
        cls = registry[e["kind"]][e["cls"]]
        entries.append({"t_start": e["t_start"], "kind": e["kind"],
                        "inj": cls(**e.get("kwargs", {})), "armed": False})
    return sorted(entries, key=lambda x: x["t_start"])


def _truck_run(scenario: str, seed: int, secs: float = 180.0) -> list[tuple]:
    """Whole real simulator + whole real pipeline. Returns state transitions
    (t, state, guilty, cause) — mirrors harness/sweep but keeps the event log."""
    wps, vtype, schedule = get_scenario(scenario)
    entries = _truck_injectors(schedule)
    rng = np.random.default_rng(seed)
    vehicle = make_vehicle(vtype, wps, rng)
    sensors = SensorSuite(rng, vehicle_type=vtype)
    pipeline = Pipeline()
    pipeline.accept({
        "type": "run_start", "run_id": f"truck-test-{seed}", "vehicle_id": "TRUCK-42",
        "vehicle_type": vtype, "seed": seed, "rate_hz": 20, "gnss_rate_hz": 5, "t0": 0.0,
    })
    active: object | None = None
    active_t = 0.0
    prev = None
    events: list[tuple] = []
    for i in range(int(secs / DT)):
        sdata = sensors.update(vehicle)
        t = vehicle.t
        for e in entries:
            if not e["armed"] and t >= e["t_start"]:
                e["armed"] = True
                active, active_t = e["inj"], t
        if active is not None:
            t_since = t - active_t
            kind = next(e["kind"] for e in entries if e["inj"] is active)
            if kind == "attack":
                if sdata["gnss"] is not None:
                    sdata["gnss"] = active.apply(sdata["gnss"], t_since)
            elif kind == "fault":
                sdata = active.apply(sdata, t_since, rng)
            else:
                sdata = active.apply(sdata, t_since)
        st = pipeline.accept({
            "vehicle_id": "TRUCK-42", "t": round(t, 3), "seq": i,
            "gnss": sdata["gnss"], "imu": sdata["imu"],
            "baro": sdata["baro"], "mag": sdata["mag"], "odom": sdata["odom"],
        })
        vehicle.step()
        if st is None:
            continue
        key = (st.state, st.blame.guilty, st.cause.label)
        if key != prev:
            prev = key
            events.append((t, st.state, st.blame.guilty, st.cause.label))
    return events


@test
def truck_roads_answer_distance_and_name() -> None:
    """roads.py: a point on the highway is on a road; the middle of nowhere is not.

    Written against surveyed geometry now rather than hand-placed vertices, so
    the junction is a *junction* rather than a point that happens to be a
    vertex — it sits a few centimetres off the drawn centreline, which is what
    real road data looks like. Asserting an exact zero was asserting that the
    map was drawn by us.
    """
    from simulator.chennai import ROUTE

    assert distance_to_nearest_road(0.0, 0.0) < 2.0, "the junction is not on a road"
    assert nearest_road_name(0.0, 0.0) != "none"

    # Every point the truck is asked to drive must be on the carriageway,
    # which is the property the road check depends on.
    if ROUTE:
        worst = max(distance_to_nearest_road(e, n) for e, n in ROUTE)
        assert worst < 5.0, f"route leaves the road network by {worst:.0f} m"

    far = (9e4, -9e4)
    assert nearest_road_name(*far) == "none"
    assert distance_to_nearest_road(*far) > 50.0


@test
def truck_parks_wheels_read_zero_then_moves_on() -> None:
    """A truck stops at the signal on Oragadam Road, its wheels read exactly
    0.0 while it waits, then it pulls away and completes the delivery — and it
    never leaves the road's shape.

    A parked wheel reading exactly zero matters more than it sounds: it is
    normal, and a health check that calls a stationary lorry a frozen sensor
    raises an alarm at every red light in Chennai."""
    rng = np.random.default_rng(7)
    wps, _vtype, _schedule = get_scenario("truck_clean")[:3]
    vehicle = make_vehicle("truck", wps, rng)
    sensors = SensorSuite(rng, vehicle_type="truck")
    longest = 0.0
    cur = 0.0
    stop_wheels_zero = True
    longest_wheels_zero = True
    final_speed = 0.0
    travelled = 0.0
    previous = (float(vehicle.position_enu[0]), float(vehicle.position_enu[1]))
    for _ in range(int(180.0 / DT)):
        sdata = sensors.update(vehicle)
        wheel = sdata["odom"]["wheel_speed_mps"]
        speed = vehicle.speed_mps
        if speed < 0.01:
            cur += DT
            stop_wheels_zero = stop_wheels_zero and wheel == 0.0
        else:
            if cur > longest:
                longest = cur
                longest_wheels_zero = stop_wheels_zero
            cur = 0.0
            stop_wheels_zero = True
        final_speed = speed
        here = (float(vehicle.position_enu[0]), float(vehicle.position_enu[1]))
        travelled += math.dist(previous, here)
        previous = here
        vehicle.step()
    if cur > longest:
        longest = cur
        longest_wheels_zero = stop_wheels_zero
    assert longest >= 4.0, "the red-light hold never happened"
    assert longest_wheels_zero, "stopped truck wheel did not read exactly 0.0"
    assert final_speed > 8.0, "truck never pulled away after the stop"
    # It should have got somewhere. Distance travelled rather than easting:
    # the route now follows the real carriageway, which turns south-west at
    # the junction, so easting legitimately ends up negative — an assertion on
    # the sign of a coordinate was really an assertion about a drawn map.
    assert travelled > 800.0, f"truck barely moved ({travelled:.0f} m)"


@test
def truck_clean_three_minutes_produces_no_alerts() -> None:
    """The truck gate: a clean 3-minute run — junction, red light, service road,
    pull-away — must clear with zero alerts. If this number leaves zero, stop."""
    for seed in (0, 1, 2):
        alarming = [e for e in _truck_run("truck_clean", seed) if e[1] != "OK"]
        assert not alarming, f"truck_clean seed {seed} raised: {alarming[:5]}"


@test
def truck_theft_walkoff_blames_gps_as_attack() -> None:
    """The demo: a walk-off drags the reported path away while the real truck is
    diverted to the warehouse. The detector must name GPS and call it an attack."""
    for seed in (0, 1):
        events = _truck_run("truck_theft", seed)
        assert any(e[2] == "gnss" for e in events), f"seed {seed} never blamed gnss"
        assert any(e[3] == "attack" for e in events), f"seed {seed} never said attack"


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
