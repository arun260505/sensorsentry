"""Stage 3 — the witness: a position estimate built without GNSS.

This is the module the whole project rests on. An attacker with a radio can
forge the GNSS fix; he cannot forge what the vehicle physically felt. So we
integrate the vehicle's own motion and get a second opinion he never touched.

    *** THIS FILE MUST NEVER READ GNSS. ***

Not for initialisation, not for a sanity check, not temporarily. To keep that
verifiable rather than merely intended, the reckoner reports *displacement
since its anchor*, never an absolute position — it has no concept of where on
Earth it is, so there is nothing for GNSS to leak into. Whoever needs an
absolute position adds the anchor outside (see residual.py).

Body frame: x forward, y left, z up. At rest the accelerometer reads
az = +9.81, matching the simulator in docs/schema.md.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import profiles
from .geo import ENU, wrap_pi, yaw_from_heading
from .ingest import Frame

GRAVITY = 9.80665

# --- tunables -------------------------------------------------------------
ACCEL_TILT_GAIN = 0.02
"""How hard gravity corrects roll and pitch each cycle. Small: the
accelerometer only tells the truth about tilt when the vehicle is not
accelerating, so we lean on it gently and constantly rather than sharply."""

MAG_YAW_GAIN = 0.05
"""How hard the compass corrects yaw. Larger than the tilt gain because
integrated gyro yaw drifts fastest and has nothing else to check it."""

MAX_INTEGRATION_GAP_S = 0.5
"""Longest frame gap we will integrate across.

Above this the motion between samples is guesswork, so we stop integrating and
only count the time. At 20 Hz this is ten consecutive lost frames."""

TILT_TRUST_BAND = 0.25
"""How far |accel| may sit from gravity, in m/s^2, before we stop believing it
represents tilt.

Tight on purpose, and the tightness is the whole point. An accelerometer
cannot distinguish tilting from accelerating, and horizontal acceleration
barely changes the total magnitude: 2.4 m/s^2 forward raises |a| from 9.81 to
only 10.10. A loose band lets that through, the filter reads it as 14 degrees
of pitch, and the witness integrates *backwards* down the wrong axis.

So we only trust gravity when the vehicle is very nearly unaccelerated. During
a manoeuvre the gyro carries attitude alone, which is exactly what it is for.
"""

GYRO_TRUST_RATE = 0.15
"""Rotation rate, rad/s, above which gravity is not trusted for tilt either.
Turning throws centripetal acceleration sideways, which reads as bank."""

MOTION_WINDOW = 20
"""Samples averaged before deciding the vehicle is unaccelerated. One second.

Testing the instantaneous magnitude is not enough: with the accelerometer
noisy at 0.02 m/s^2 and a ramp sitting only 0.29 m/s^2 above gravity, single
samples dip under any workable threshold constantly. Each one that slips
through steals a little forward acceleration into false pitch, and the witness
quietly under-reads distance — 20 m lost over 45 s, systematic, and invisible
until you notice a forward bias *improving* accuracy.
"""


@dataclass
class Witness:
    """What the vehicle's own senses say, independent of any radio."""

    displacement: ENU
    """Metres travelled since the anchor was set."""

    velocity: ENU
    """Current velocity in local ENU, m/s."""

    yaw: float
    """Heading in ENU radians (from east, counter-clockwise)."""

    roll: float
    pitch: float

    sigma_m: float
    """One-sigma horizontal uncertainty of `displacement`, in metres.

    Grows with time since the anchor, because unknown accelerometer bias
    integrates twice. This is the honest error budget shown to the operator,
    and it is what stops us accusing GNSS of a disagreement smaller than our
    own drift."""

    elapsed_s: float
    """Seconds since the anchor was set."""

    lost_s: float
    """Of those seconds, how many fell inside gaps too long to integrate."""

    distance_travelled_m: float
    """Path length since the anchor, not straight-line displacement. Compared
    against wheel odometry, which also measures path length."""


class DeadReckoner:
    """Strapdown integration of IMU, aided by compass and barometer.

    Deliberately a complementary filter rather than a full Kalman filter: it is
    ten lines of readable maths, every term is explicable to a judge, and at
    demo timescales it performs indistinguishably. The Kalman innovation test
    is a later upgrade for credibility, not capability (see PLAN.md phase 11).
    """

    def __init__(self, profile: profiles.Profile):
        self.profile = profile
        self.accel_bias_sigma = profile.accel_bias_sigma
        self._reset_state()

    def _reset_state(self) -> None:
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.vel = np.zeros(3)          # ENU m/s
        self.disp = np.zeros(3)         # ENU m since anchor
        self.path_m = 0.0
        self.elapsed = 0.0
        self.lost_s = 0.0
        """Seconds inside gaps too long to integrate. Reported so the console
        can say the witness coasted rather than pretending it tracked."""

        self._initialised = False
        self._baro_ref_hpa: Optional[float] = None
        self._baro_alt: Optional[float] = None
        self._excess_window: deque[float] = deque(maxlen=MOTION_WINDOW)
        self._rate_window: deque[float] = deque(maxlen=MOTION_WINDOW)

    def _unaccelerated(self, ax: float, ay: float, az: float,
                       p: float, q: float, r: float) -> bool:
        """Is the vehicle steady enough for gravity to mean 'down'?

        Judged on the *worst* sample in the last second, not the average. A
        tilt correction applied while accelerating is unrecoverable: it writes
        a false pitch that the gyro then faithfully preserves, gravity leaks
        into the forward axis, and the witness under-reads speed for the rest
        of the run. An averaged gate still opens briefly at the start of a
        manoeuvre, while the window is half full of the stationary samples that
        came before — which is enough to do the damage.

        So the gate closes on the first hint of motion and only reopens after a
        full quiet second. Refusing a correction costs a little gyro drift;
        accepting a bad one costs the whole estimate.
        """
        self._excess_window.append(abs(math.sqrt(ax * ax + ay * ay + az * az) - GRAVITY))
        self._rate_window.append(math.sqrt(p * p + q * q + r * r))
        if len(self._excess_window) < MOTION_WINDOW:
            return False
        return (
            max(self._excess_window) < TILT_TRUST_BAND
            and max(self._rate_window) < GYRO_TRUST_RATE
        )

    def anchor(self) -> None:
        """Restart the witness from zero displacement, here and now.

        Called once at the start of a run, and never again during an incident —
        re-anchoring mid-attack would quietly adopt the spoofed position as
        truth, which is precisely the failure we exist to prevent.
        """
        self._reset_state()

    # --- main loop ---------------------------------------------------------

    def update(self, frame: Frame) -> Witness:
        dt = frame.dt
        if dt <= 0.0 or dt > MAX_INTEGRATION_GAP_S:
            # First frame, or a gap long enough that integrating across it
            # would inject a large error. Level the attitude and wait.
            #
            # Time still passed, though, so `elapsed` must advance anyway.
            # Skipping it lets the vehicle coast through a dropout while we
            # keep claiming the small uncertainty we had before it — the one
            # direction this model must never err in, because it would have us
            # accusing GNSS of a disagreement our own drift could explain.
            if dt > 0.0:
                self.elapsed += dt
                self.lost_s += dt
            self._level_from(frame)
            return self._witness()

        self._integrate_attitude(frame, dt)
        self._integrate_position(frame, dt)
        self._track_baro(frame)

        self.elapsed += dt
        return self._witness()

    def _level_from(self, frame: Frame) -> None:
        """Set attitude directly from gravity and the compass. Only used before
        integration starts, when there is no prior estimate to blend with."""
        ax, ay, az = (float(frame.imu[k]) for k in ("ax", "ay", "az"))
        self.roll = math.atan2(ay, az)
        self.pitch = math.atan2(-ax, math.hypot(ay, az))
        if frame.mag is not None and "heading_deg" in frame.mag:
            self.yaw = yaw_from_heading(float(frame.mag["heading_deg"]))
        self._initialised = True

    def _integrate_attitude(self, frame: Frame, dt: float) -> None:
        if not self._initialised:
            self._level_from(frame)
            return

        p, q, r = (float(frame.imu[k]) for k in ("gx", "gy", "gz"))

        # Body rates to Euler rates (ZYX). Guard the pitch singularity — a
        # drone will not fly through vertical, but a divide by zero here would
        # take the whole run down.
        cos_pitch = math.cos(self.pitch)
        if abs(cos_pitch) < 1e-4:
            cos_pitch = math.copysign(1e-4, cos_pitch or 1.0)
        tan_pitch = math.tan(self.pitch)
        sin_roll, cos_roll = math.sin(self.roll), math.cos(self.roll)

        self.roll += dt * (p + tan_pitch * (sin_roll * q + cos_roll * r))
        self.pitch += dt * (cos_roll * q - sin_roll * r)
        self.yaw += dt * ((sin_roll * q + cos_roll * r) / cos_pitch)

        # Gravity corrects roll and pitch, but only while the accelerometer is
        # actually measuring gravity rather than a manoeuvre.
        ax, ay, az = (float(frame.imu[k]) for k in ("ax", "ay", "az"))
        if self._unaccelerated(ax, ay, az, p, q, r):
            roll_obs = math.atan2(ay, az)
            pitch_obs = math.atan2(-ax, math.hypot(ay, az))
            self.roll += ACCEL_TILT_GAIN * wrap_pi(roll_obs - self.roll)
            self.pitch += ACCEL_TILT_GAIN * wrap_pi(pitch_obs - self.pitch)

        # The compass is the only thing that bounds yaw drift.
        if frame.mag is not None and "heading_deg" in frame.mag:
            yaw_obs = yaw_from_heading(float(frame.mag["heading_deg"]))
            self.yaw += MAG_YAW_GAIN * wrap_pi(yaw_obs - self.yaw)

        self.roll = wrap_pi(self.roll)
        self.pitch = wrap_pi(self.pitch)
        self.yaw = wrap_pi(self.yaw)

    def _integrate_position(self, frame: Frame, dt: float) -> None:
        specific_force = np.array(
            [float(frame.imu["ax"]), float(frame.imu["ay"]), float(frame.imu["az"])]
        )
        accel_enu = self._body_to_enu() @ specific_force - np.array([0.0, 0.0, GRAVITY])

        # Trapezoidal on position: with a 20 Hz frame rate, plain rectangular
        # integration accumulates a visible bias during sustained acceleration.
        prev_vel = self.vel.copy()
        self.vel = prev_vel + accel_enu * dt
        step = 0.5 * (prev_vel + self.vel) * dt
        self.disp = self.disp + step
        self.path_m += float(np.linalg.norm(step))

    def _body_to_enu(self) -> np.ndarray:
        """ZYX rotation matrix, body (x fwd, y left, z up) to ENU."""
        cr, sr = math.cos(self.roll), math.sin(self.roll)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        return np.array(
            [
                [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                [-sp, cp * sr, cp * cr],
            ]
        )

    def _track_baro(self, frame: Frame) -> None:
        """Barometric altitude relative to the anchor.

        Pressure is a separate sensor from GNSS and the attacker's radio does
        not reach it, so this belongs in the witness. Only the *change* is
        used, which cancels the slow drift and the unknown sea-level offset.
        """
        if frame.baro is None or "pressure_hpa" not in frame.baro:
            return
        hpa = float(frame.baro["pressure_hpa"])
        if self._baro_ref_hpa is None:
            self._baro_ref_hpa = hpa
            self._baro_alt = 0.0
            return
        # Hypsometric approximation, accurate to well under a metre over the
        # few hundred metres a demo covers.
        self._baro_alt = 44330.0 * (1.0 - (hpa / self._baro_ref_hpa) ** 0.190295)

    # --- output ------------------------------------------------------------

    def _sigma(self) -> float:
        """Horizontal uncertainty from unmodelled accelerometer bias.

        An unknown constant bias b integrates into a position error of
        b*t^2/2. With drone-grade sigma of 0.05 m/s^2 that is about 90 m after
        60 s, which is the band the simulator is tuned to hit.
        """
        return 0.5 * self.accel_bias_sigma * self.elapsed * self.elapsed

    def _witness(self) -> Witness:
        # Prefer barometric height over doubly-integrated vertical accel: the
        # barometer measures altitude directly and does not accumulate error.
        up = self._baro_alt if self._baro_alt is not None else float(self.disp[2])
        return Witness(
            displacement=ENU(float(self.disp[0]), float(self.disp[1]), up),
            velocity=ENU(float(self.vel[0]), float(self.vel[1]), float(self.vel[2])),
            yaw=self.yaw,
            roll=self.roll,
            pitch=self.pitch,
            sigma_m=self._sigma(),
            elapsed_s=self.elapsed,
            lost_s=self.lost_s,
            distance_travelled_m=self.path_m,
        )
