"""Synthetic frames for testing the detector in isolation.

NOT the simulator. This is a few dozen lines of straight-line motion with
noise, so detector modules can be unit-tested without a running simulator and
without waiting on anyone. The real simulator (routes, manoeuvres, the attack
library, the scenario runner) lives in `simulator/` and is Abishek's.

If you find yourself adding a flight model here, stop — it belongs there.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterator, Optional

from detector.geo import EARTH_RADIUS_M

GRAVITY = 9.80665

# Matches the simulator's sensor models, see docs/handover/01-abishek-simulator.md
ACCEL_NOISE = 0.02
GYRO_NOISE = 0.002
BARO_NOISE = 0.08
MAG_NOISE = 1.5
GNSS_H_NOISE = 1.5
GNSS_V_NOISE = 3.0

ORIGIN_LAT = 11.0168
ORIGIN_LON = 76.9558
ORIGIN_ALT = 411.0
SEA_LEVEL_HPA = 1013.25


@dataclass
class Track:
    """A straight-line vehicle: accelerate from rest, then hold speed.

    The ramp is not decoration. Dead reckoning can only learn a velocity the
    accelerometer actually felt, so a fixture that begins already at speed is
    physically impossible and the witness is right to refuse it. Every vehicle
    starts at rest.
    """

    speed_mps: float = 12.0
    bearing_deg: float = 45.0
    climb_mps: float = 0.0
    hold_s: float = 3.0
    """Seconds stationary before moving off. Gravity is the only thing that can
    tell the filter which way is down, and it can only do so while the vehicle
    is unaccelerated — so a real vehicle sitting still before it departs is not
    a detail, it is what makes the initial attitude knowable at all."""

    ramp_s: float = 5.0
    """Seconds spent accelerating from rest up to `speed_mps`."""

    accel_bias: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """Constant accelerometer bias in body axes. This is what makes dead
    reckoning drift; set it to exercise the uncertainty model."""

    def _yaw(self) -> float:
        return math.radians(90.0 - self.bearing_deg)

    def speed_at(self, t: float) -> tuple[float, float]:
        """Horizontal speed and climb rate at time t."""
        tau = max(0.0, t - self.hold_s)
        fraction = min(tau / self.ramp_s, 1.0) if self.ramp_s > 0 else 1.0
        return self.speed_mps * fraction, self.climb_mps * fraction

    def accel_at(self, t: float) -> tuple[float, float]:
        """Forward and vertical acceleration at time t, m/s^2."""
        tau = t - self.hold_s
        if self.ramp_s <= 0 or tau < 0.0 or tau >= self.ramp_s:
            return 0.0, 0.0
        return self.speed_mps / self.ramp_s, self.climb_mps / self.ramp_s

    def velocity_enu(self, t: float) -> tuple[float, float, float]:
        horizontal, climb = self.speed_at(t)
        yaw = self._yaw()
        return horizontal * math.cos(yaw), horizontal * math.sin(yaw), climb

    def position_enu(self, t: float) -> tuple[float, float, float]:
        """Exact integral of the velocity profile: still, then 0.5*a*tau^2
        while ramping, then linear."""
        tau = t - self.hold_s
        if tau <= 0.0:
            scale = 0.0
        elif self.ramp_s > 0 and tau < self.ramp_s:
            scale = 0.5 * tau * tau / self.ramp_s
        else:
            scale = 0.5 * self.ramp_s + (tau - self.ramp_s)
        yaw = self._yaw()
        return (
            self.speed_mps * scale * math.cos(yaw),
            self.speed_mps * scale * math.sin(yaw),
            self.climb_mps * scale,
        )


@dataclass
class Spoof:
    """A walk-off applied to the reported GNSS position only."""

    start_t: float = math.inf
    speed_mps: float = 0.0
    bearing_deg: float = 0.0

    def offset(self, t: float) -> tuple[float, float]:
        if t < self.start_t or self.speed_mps == 0.0:
            return (0.0, 0.0)
        drift = self.speed_mps * (t - self.start_t)
        yaw = math.radians(90.0 - self.bearing_deg)
        return (drift * math.cos(yaw), drift * math.sin(yaw))


def _pressure_at(alt_m: float) -> float:
    return SEA_LEVEL_HPA * (1.0 - alt_m / 44330.0) ** (1.0 / 0.190295)


def frames(
    duration_s: float = 60.0,
    rate_hz: float = 20.0,
    gnss_rate_hz: float = 5.0,
    track: Optional[Track] = None,
    spoof: Optional[Spoof] = None,
    vehicle_id: str = "TEST-1",
    seed: int = 12345,
    noise: bool = True,
) -> Iterator[dict]:
    """Yield a run_start header followed by sensor frames.

    Frames match docs/schema.md exactly, including `gnss` being null on three
    frames out of four, so anything that consumes real frames consumes these.
    """
    track = track or Track()
    spoof = spoof or Spoof()
    rng = random.Random(seed)

    def jitter(sigma: float) -> float:
        return rng.gauss(0.0, sigma) if noise else 0.0

    yield {
        "type": "run_start",
        "run_id": f"test-{seed}",
        "vehicle_id": vehicle_id,
        "vehicle_type": "drone",
        "seed": seed,
        "rate_hz": rate_hz,
        "gnss_rate_hz": gnss_rate_hz,
        "t0": 0.0,
    }

    dt = 1.0 / rate_hz
    gnss_every = max(1, round(rate_hz / gnss_rate_hz))
    bx, by, bz = track.accel_bias
    n_frames = int(duration_s * rate_hz)

    for i in range(n_frames):
        t = i * dt
        e, n, u = track.position_enu(t)
        forward_a, vertical_a = track.accel_at(t)

        # Specific force in a body frame with x forward, z up. The vehicle
        # stays level and does not turn, so rotating ENU acceleration into the
        # body frame collapses to forward on x and gravity plus climb on z.
        imu = {
            "ax": forward_a + bx + jitter(ACCEL_NOISE),
            "ay": by + jitter(ACCEL_NOISE),
            "az": GRAVITY + vertical_a + bz + jitter(ACCEL_NOISE),
            "gx": jitter(GYRO_NOISE),
            "gy": jitter(GYRO_NOISE),
            "gz": jitter(GYRO_NOISE),
        }

        frame = {
            "vehicle_id": vehicle_id,
            "t": t,
            "seq": i,
            "imu": imu,
            "baro": {"pressure_hpa": _pressure_at(ORIGIN_ALT + u) + jitter(BARO_NOISE)},
            "mag": {"heading_deg": (track.bearing_deg + jitter(MAG_NOISE)) % 360.0},
            "gnss": None,
            "odom": None,
        }

        if i % gnss_every == 0:
            off_e, off_n = spoof.offset(t)
            re, rn = e + off_e + jitter(GNSS_H_NOISE), n + off_n + jitter(GNSS_H_NOISE)
            lat0 = math.radians(ORIGIN_LAT)
            frame["gnss"] = {
                "lat": ORIGIN_LAT + math.degrees(rn / EARTH_RADIUS_M),
                "lon": ORIGIN_LON + math.degrees(re / (EARTH_RADIUS_M * math.cos(lat0))),
                "alt": ORIGIN_ALT + u + jitter(GNSS_V_NOISE),
                "fix": 3,
                "sats": 11,
                "hdop": 0.9,
                "cn0_mean": 41.0,
            }

        yield frame
