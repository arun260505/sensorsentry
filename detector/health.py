"""Stage 2 — check each sensor on its own, before comparing any of them.

A sensor that is frozen, out of range or silent will poison every
cross-validation it takes part in and send blame to an innocent sensor. Catch
those here first.

This stage is also half of the answer to the problem statement's "false
readings": most plainly broken hardware is visible without comparing anything.

Every flag carries a reason code, never a bare boolean — the operator sees the
reason, and stage 6 uses it to separate a fault from an attack.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from . import profiles
from .ingest import Frame

# --- tunables -------------------------------------------------------------
STUCK_CYCLES = 20
"""Identical readings before a sensor is called frozen. One second at 20 Hz.
Real sensors always dither at the least-significant bit; a value that does not
move at all has stopped being measured."""

NOISE_WINDOW = 40
"""Samples used for the rolling spread check. Two seconds at 20 Hz."""

NOISE_MULTIPLIER = 6.0
"""How far past its rated noise a sensor may sit before being called degraded.
Deliberately loose: this check exists to catch a failing sensor, not to
second-guess an honest one during hard manoeuvres."""

GNSS_GAP_S = 1.0
"""Silence before GNSS is called dropped. GNSS runs at 5 Hz, so 1.0 s is five
missed updates — comfortably past normal jitter."""

# Rated noise, one standard deviation. Matches the simulator's sensor models
# (docs/handover/01-abishek-simulator.md); if those change, change these.
RATED_SIGMA = {
    "accel": 0.02,      # m/s^2
    "gyro": 0.002,      # rad/s
    "baro": 0.08,       # hPa
    "mag": 1.5,         # degrees
    "odom": 0.05,       # m/s
}

# Physically impossible readings. Wide on purpose — this catches broken
# hardware, not aggressive flying.
LIMITS = {
    "accel": 80.0,          # m/s^2, ~8 g
    "gyro": 35.0,           # rad/s
    "baro_hpa": (300.0, 1100.0),
    "mag_deg": (0.0, 360.0),
    "odom_mps": (-5.0, 90.0),
    "gnss_alt_m": (-500.0, 20000.0),
}

# Reason codes. These reach the operator, so they read as English.
STUCK = "stuck"
OUT_OF_RANGE = "out_of_range"
DROPPED = "dropped"
DEGRADED = "noisy"
NO_FIX = "no_fix"


@dataclass
class SensorHealth:
    """Verdict for one sensor this cycle."""

    name: str
    healthy: bool = True
    flags: list[str] = field(default_factory=list)
    detail: dict[str, float] = field(default_factory=dict)

    def flag(self, code: str, **detail: float) -> None:
        if code not in self.flags:
            self.flags.append(code)
        self.healthy = False
        self.detail.update(detail)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else 0.5 * (ordered[mid - 1] + ordered[mid])


class _Channel:
    """Rolling state for one scalar signal."""

    __slots__ = ("values", "last", "identical")

    def __init__(self) -> None:
        self.values: deque[float] = deque(maxlen=NOISE_WINDOW)
        self.last: Optional[float] = None
        self.identical = 0

    def push(self, value: float) -> None:
        if self.last is not None and value == self.last:
            self.identical += 1
        else:
            self.identical = 0
        self.last = value
        self.values.append(value)

    def noise_sigma(self) -> Optional[float]:
        """Estimated per-sample white noise, or None until the window fills.

        Measured on successive *differences*, not on the raw values. The raw
        spread of an accelerometer during hard acceleration is enormous and
        perfectly healthy — flagging that would raise an alarm on every
        manoeuvre, which is the exact failure CLAUDE.md rule 3 forbids.

        Real motion is smooth, so it barely shows up between adjacent samples;
        sensor noise is not, so it shows up fully. For white noise of sigma s,
        successive differences have sigma s*sqrt(2), hence the division.
        """
        n = len(self.values)
        if n < NOISE_WINDOW:
            return None
        diffs = [self.values[i] - self.values[i - 1] for i in range(1, n)]

        # Median absolute deviation, not standard deviation. A vehicle that
        # starts moving produces one large genuine jump, and a plain standard
        # deviation lets that single sample dominate the whole window — the
        # sensor then reads as faulty for two seconds after every throttle
        # change. MAD ignores a handful of outliers by construction, which is
        # exactly the behaviour we want from a fault detector.
        median = _median(diffs)
        mad = _median([abs(d - median) for d in diffs])
        sigma_of_diffs = 1.4826 * mad  # MAD to sigma, for Gaussian noise
        return sigma_of_diffs / math.sqrt(2.0)


class HealthMonitor:
    """Runs the per-sensor checks every cycle.

    One instance per vehicle. Keeps a short rolling window per channel and
    nothing else.
    """

    def __init__(self, profile: profiles.Profile):
        self.profile = profile
        self._channels: dict[str, _Channel] = {}
        self._last_gnss_t: Optional[float] = None
        self._first_t: Optional[float] = None

    def _channel(self, key: str) -> _Channel:
        chan = self._channels.get(key)
        if chan is None:
            chan = self._channels[key] = _Channel()
        return chan

    def update(self, frame: Frame) -> dict[str, SensorHealth]:
        if self._first_t is None:
            self._first_t = frame.t

        # The road network is a prior, not a sensor — nothing to health-check.
        report = {name: SensorHealth(name) for name in self.profile.sensors
                  if name not in profiles.INFALLIBLE}

        self._check_imu(frame, report[profiles.IMU])
        if profiles.BARO in report:
            self._check_baro(frame, report[profiles.BARO])
        if profiles.MAG in report:
            self._check_mag(frame, report[profiles.MAG])
        if profiles.ODOM in report:
            self._check_odom(frame, report[profiles.ODOM])
        self._check_gnss(frame, report[profiles.GNSS])

        return report

    # --- individual sensors ------------------------------------------------

    def _check_imu(self, frame: Frame, health: SensorHealth) -> None:
        imu = frame.imu
        for axis in ("ax", "ay", "az"):
            value = float(imu[axis])
            if abs(value) > LIMITS["accel"]:
                health.flag(OUT_OF_RANGE, **{axis: value})
            self._scalar_checks(f"imu.{axis}", value, RATED_SIGMA["accel"], health, axis)

        for axis in ("gx", "gy", "gz"):
            value = float(imu[axis])
            if abs(value) > LIMITS["gyro"]:
                health.flag(OUT_OF_RANGE, **{axis: value})
            self._scalar_checks(f"imu.{axis}", value, RATED_SIGMA["gyro"], health, axis)

    def _check_baro(self, frame: Frame, health: SensorHealth) -> None:
        if frame.baro is None or "pressure_hpa" not in frame.baro:
            health.flag(DROPPED)
            return
        value = float(frame.baro["pressure_hpa"])
        low, high = LIMITS["baro_hpa"]
        if not low <= value <= high:
            health.flag(OUT_OF_RANGE, pressure_hpa=value)
        self._scalar_checks("baro.p", value, RATED_SIGMA["baro"], health, "pressure_hpa")

    def _check_mag(self, frame: Frame, health: SensorHealth) -> None:
        if frame.mag is None or "heading_deg" not in frame.mag:
            health.flag(DROPPED)
            return
        value = float(frame.mag["heading_deg"])
        low, high = LIMITS["mag_deg"]
        if not low <= value <= high:
            health.flag(OUT_OF_RANGE, heading_deg=value)
        # Heading wraps, so a rolling spread would spike crossing north. Only
        # the stuck check is meaningful on a circular quantity here.
        chan = self._channel("mag.h")
        chan.push(value)
        if chan.identical >= STUCK_CYCLES:
            health.flag(STUCK, heading_deg=value)

    def _check_odom(self, frame: Frame, health: SensorHealth) -> None:
        if frame.odom is None or frame.odom.get("wheel_speed_mps") is None:
            health.flag(DROPPED)
            return
        value = float(frame.odom["wheel_speed_mps"])
        low, high = LIMITS["odom_mps"]
        if not low <= value <= high:
            health.flag(OUT_OF_RANGE, wheel_speed_mps=value)
        # A stationary vehicle legitimately reads exactly zero, so the stuck
        # check would fire on every red light. Only flag a frozen non-zero.
        chan = self._channel("odom.v")
        chan.push(value)
        if chan.identical >= STUCK_CYCLES and abs(value) > 0.01:
            health.flag(STUCK, wheel_speed_mps=value)

    def _check_gnss(self, frame: Frame, health: SensorHealth) -> None:
        if frame.has_gnss():
            gnss = frame.gnss or {}
            self._last_gnss_t = frame.t

            if int(gnss.get("fix", 3)) < 2:
                health.flag(NO_FIX, fix=float(gnss.get("fix", 0)))

            alt = float(gnss.get("alt", 0.0))
            low, high = LIMITS["gnss_alt_m"]
            if not low <= alt <= high:
                health.flag(OUT_OF_RANGE, alt=alt)

            # Position frozen to the bit is a receiver fault, not a stopped
            # vehicle: real fixes dither even at a standstill.
            chan = self._channel("gnss.lat")
            chan.push(float(gnss["lat"]))
            if chan.identical >= STUCK_CYCLES // 4:  # GNSS is 5 Hz, not 20
                health.flag(STUCK, lat=float(gnss["lat"]))
            return

        # No GNSS this frame is normal three times in four. Only silence is not.
        reference = self._last_gnss_t if self._last_gnss_t is not None else self._first_t
        if reference is not None and frame.t - reference > GNSS_GAP_S:
            health.flag(DROPPED, silent_for_s=frame.t - reference)

    # --- shared ------------------------------------------------------------

    def _scalar_checks(
        self, key: str, value: float, rated_sigma: float, health: SensorHealth, label: str
    ) -> None:
        chan = self._channel(key)
        chan.push(value)

        if chan.identical >= STUCK_CYCLES:
            health.flag(STUCK, **{label: value})

        sigma = chan.noise_sigma()
        if sigma is not None and sigma > rated_sigma * NOISE_MULTIPLIER:
            health.flag(DEGRADED, **{f"{label}_sigma": sigma})


def summarise(report: dict[str, SensorHealth]) -> str:
    """One-line rendering for logs and the console."""
    bad = [h for h in report.values() if not h.healthy]
    if not bad:
        return "all sensors healthy"
    return "; ".join(f"{h.name}: {','.join(h.flags)}" for h in bad)
