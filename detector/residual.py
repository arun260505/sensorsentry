"""The GNSS-versus-witness comparison, and the anchor that makes it possible.

deadreckon.py deliberately has no idea where on Earth it is — it only reports
displacement. Somebody has to hold the starting point, and it is here, kept
apart on purpose so that "the witness never reads GNSS" stays checkable by
opening one file.

The anchor is taken **once**, from the first GNSS fix of a run, before any
attack exists, and never refreshed. Re-anchoring mid-run would silently adopt
a spoofed position as truth.

Stage 4 proper — every sensor pair scored against every other — builds on this
in crossvalidate.py. This module covers the single most important pair, which
is what phase 2 needs to be finished.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .deadreckon import Witness
from .geo import ENU, Origin, enu_from_llh
from .ingest import Frame

# --- tunables -------------------------------------------------------------
SIGMA_FLOOR_M = 4.0
"""Smallest uncertainty we will ever claim, in metres. GNSS itself is good to
a couple of metres and the witness is never perfect, so a residual below this
means nothing regardless of what the drift model says."""

DRIFT_RATE_MPS = 1.8
"""How fast the witness's uncertainty grows, in metres per second elapsed.

Linear rather than quadratic, and that is measured rather than convenient: the
complementary filter absorbs a steady horizontal accelerometer bias into a
small pitch offset that cancels it (see deadreckon.py), so the textbook
b*t^2/2 bound does not apply to us. What remains is heading error and
manoeuvre transients.

**Recalibrated against the real simulator, 7 Sep 2026.** Was 0.5, taken from
the straight-line test fixture, which drifts about 12 m at 60 s. The fixture
turned out to be the optimistic one — it flies in a straight line, and turns
are where dead reckoning actually suffers. Measured worst case over five seeds
on simulator/, which sits inside the 20-120 m band the handover asked for:

    scenario           30 s        60 s        90 s       120 s
    drone_clean         19 m        60 m       189 m       516 m
    drone_manoeuvre     33 m       103 m       293 m       590 m

1.8 m/s covers the worst 60-second case with a little margin.

**Known limit, and it is real.** Drift is not linear — the implied rate climbs
from 0.6 m/s at 30 s to 4.3 m/s at 120 s — so beyond roughly 90 seconds of
*free-running* the model under-covers and a clean flight will raise a false
alarm. Inflating this constant to cover 120 s is not the fix: it would take
sigma past 200 m and make a 2 m/s walk-off invisible, which is the whole
attack we exist to catch.

The fix is phase 7. While GNSS is trusted it should aid the witness, so drift
stops growing without bound and this residual becomes a filter innovation
rather than a free-run error. Free-running for three minutes is a phase 2
simplification, not the product. Until phase 7 lands, the clean-run gate holds
for about the first 90 seconds and not beyond — recorded here rather than
hidden behind a bigger constant.
"""

ANCHOR_SETTLE_S = 2.0
"""How long to let attitude settle before taking the anchor. Anchoring on the
very first frame captures a levelling transient and biases the whole run."""

AIDING_TAU_S = 25.0
"""Time constant for correcting the witness from GNSS, in seconds.

This one number decides whether the whole approach works, so it is worth
being clear about what it trades.

A free-running witness drifts without bound — 60 m by one minute, 500 m by
two — so eventually any honest uncertainty grows past the size of the attacks
we are trying to see, and a clean flight starts raising alarms. Bounding that
means letting GNSS pull the witness back.

But GNSS is the thing we are trying to catch lying. Pull too eagerly and a
spoofer simply drags the witness along with the fake position, and the
disagreement we exist to measure quietly disappears.

So the correction is deliberately slow. Drift, which accumulates steadily,
gets absorbed. A walk-off, which pushes in one direction, cannot be absorbed
fast enough and stands off as a sustained error of roughly
`attack_speed * AIDING_TAU_S` — at 2 m/s that is about 50 m, well clear of
the noise. Meanwhile our own drift is held near `DRIFT_RATE_MPS * TAU`
instead of growing forever.

25 s keeps the bound near 45 m while leaving a 1 m/s attack visible. Both
those numbers come from measurement against the simulator, not from theory.

The moment trust in GNSS drops, aiding stops (see `freeze_aiding`) and the
witness free-runs on the vehicle's own senses alone. That is the fallback the
demo shows: it stops believing GPS and keeps flying.
"""

DRIFT_VEL_TAU_S = 60.0
"""Time constant for learning how fast the witness is drifting, in seconds.

Correcting position alone is not enough. The witness does not just sit at a
fixed offset — it drifts at a *rate*, and that rate grows as attitude error
accumulates. A position-only correction is always chasing it, so the gap it
leaves behind keeps widening and a clean flight eventually raises an alarm.

So we estimate the drift rate too, and let it extrapolate between fixes.
Slower than the position correction, because a rate estimate built from noisy
fixes is itself noisy, and this one is allowed to move the answer.
"""

MAX_DRIFT_VEL_MPS = 1.0
"""Ceiling on the learned drift rate, in metres per second.

This is the line between absorbing our own error and absorbing the attack, and
it is the reason estimating drift rate is safe at all.

Position error from drift and position error from spoofing look *identical* in
a single fix. Nothing in the geometry separates them. What separates them is
physics: our inertial drift is bounded by the quality of the IMU, and we have
measured it — around 0.8 m/s worst case on this hardware. A spoofer dragging
the vehicle faster than that is doing something our own sensors cannot.

So the estimate is clamped here. Drift below the ceiling is learned and
cancelled, which is what keeps clean flights quiet. Anything above it cannot
be absorbed no matter how long it persists, and stands off as a growing
residual — which is exactly what we detect.

The cost is honest and worth saying out loud on stage: **a walk-off slower
than about 1 m/s is indistinguishable from our own drift, and we will not
catch it.** Raising this ceiling would hide real attacks; lowering it would
raise false alarms on clean flights. It is set to the measured drift rate
because that is what the physics allows, not what we would prefer.
"""


def _clamp_horizontal(v: ENU, limit: float) -> ENU:
    """Cap a vector's horizontal magnitude, leaving its direction alone."""
    speed = math.hypot(v.e, v.n)
    if speed <= limit or speed == 0.0:
        return v
    scale = limit / speed
    return ENU(v.e * scale, v.n * scale, v.u)


@dataclass
class Residual:
    """How far apart GNSS and the vehicle's own senses are, right now."""

    horizontal_m: float
    """Distance between the two positions in the horizontal plane."""

    vertical_m: float
    """Signed difference in altitude: GNSS minus witness. Kept separate
    because GNSS altitude is several times noisier than its horizontal fix,
    and because altitude-only spoofing is its own attack."""

    sigma_m: float
    """What a residual this size would be if nothing were wrong — the combined
    uncertainty of GNSS and the witness."""

    ratio: float
    """horizontal_m / sigma_m. The number that actually matters.

    A raw distance means nothing on its own: 40 m is alarming after 5 seconds
    and unremarkable after two minutes of dead reckoning. Dividing by the
    honest uncertainty is what lets one threshold hold for a whole flight.
    """

    gnss_pos: ENU
    witness_pos: ENU
    t: float

    def exceeds(self, ratio_threshold: float) -> bool:
        return self.ratio >= ratio_threshold


class ResidualTracker:
    """Holds the anchor and produces a Residual whenever GNSS arrives."""

    def __init__(self, accel_bias_sigma: float = 0.05) -> None:
        self.accel_bias_sigma = accel_bias_sigma
        self.origin: Optional[Origin] = None
        self.anchored_at_t: Optional[float] = None
        self._witness_at_anchor: Optional[ENU] = None
        """The witness reading at the instant the anchor was taken.

        The witness counts from wherever the reckoner started; the anchor is a
        GNSS fix a couple of seconds later. Without subtracting this, the two
        are measured from different origins and every residual carries a
        constant offset that looks exactly like a spoof."""

        self._anchor_elapsed: Optional[float] = None
        self._first_frame_t: Optional[float] = None
        self._last_gnss_t: Optional[float] = None
        self.last: Optional[Residual] = None

        self.correction = ENU(0.0, 0.0, 0.0)
        """Our running estimate of how far the witness has drifted.

        Added to the witness before comparing, and nudged toward GNSS slowly
        (see AIDING_TAU_S). This is the only state in the detector that GNSS is
        allowed to influence, which is why it lives here beside the anchor and
        not inside deadreckon.py."""

        self.aiding = True
        """Whether GNSS is currently allowed to correct the witness. Cleared
        the moment GNSS is suspect, so the witness free-runs instead of being
        walked along by a spoofer."""

        self.unaided_s = 0.0
        """Seconds since aiding last ran. Drives how fast uncertainty grows."""

        self.drift_vel = ENU(0.0, 0.0, 0.0)
        """Learned rate at which the witness is drifting, m/s. Clamped to
        MAX_DRIFT_VEL_MPS so it can absorb our own error but never the
        attack."""

    def reset(self) -> None:
        self.origin = None
        self.anchored_at_t = None
        self._witness_at_anchor = None
        self._anchor_elapsed = None
        self._first_frame_t = None
        self._last_gnss_t = None
        self.last = None
        self.correction = ENU(0.0, 0.0, 0.0)
        self.drift_vel = ENU(0.0, 0.0, 0.0)
        self.aiding = True
        self.unaided_s = 0.0

    def freeze_aiding(self) -> None:
        """Stop letting GNSS correct the witness.

        Called when GNSS is no longer trusted. From here the witness runs on
        the vehicle's own senses alone and its uncertainty grows again — which
        is honest, and is what lets the console show a decaying error budget
        while the vehicle keeps flying its true route."""
        self.aiding = False

    def resume_aiding(self) -> None:
        """Trust GNSS again. Uncertainty stops growing from here."""
        self.aiding = True

    @property
    def anchored(self) -> bool:
        return self.origin is not None

    def update(self, frame: Frame, witness: Witness) -> Optional[Residual]:
        """Returns a Residual on frames carrying GNSS, otherwise None.

        GNSS arrives at 5 Hz against 20 Hz frames, so three calls in four
        return None. That is expected — callers keep the previous value.
        """
        if self._first_frame_t is None:
            self._first_frame_t = frame.t

        if not frame.has_gnss():
            return None

        gnss = frame.gnss or {}
        lat, lon, alt = float(gnss["lat"]), float(gnss["lon"]), float(gnss["alt"])

        if self.origin is None:
            if frame.t - self._first_frame_t < ANCHOR_SETTLE_S:
                return None
            self.origin = Origin(lat=lat, lon=lon, alt=alt)
            self.anchored_at_t = frame.t
            self._witness_at_anchor = witness.displacement
            self._anchor_elapsed = witness.elapsed_s
            self._last_gnss_t = frame.t
            return None

        gnss_pos = enu_from_llh(lat, lon, alt, self.origin)
        gnss_dt = frame.t - (self._last_gnss_t if self._last_gnss_t is not None else frame.t)
        self._last_gnss_t = frame.t

        # Both sides measured from the same instant and the same place, with
        # our current estimate of the witness's own drift folded in.
        raw_witness = witness.displacement - self._witness_at_anchor
        witness_pos = raw_witness + self.correction

        innovation = gnss_pos - witness_pos
        horizontal = innovation.horizontal_norm()
        vertical = innovation.u

        if self.aiding and gnss_dt > 0.0:
            # Position: a slow first-order pull toward GNSS. Fast enough to
            # absorb our own drift, far too slow to be walked along by a
            # spoofer.
            gain = min(1.0, gnss_dt / AIDING_TAU_S)
            self.correction = ENU(
                self.correction.e + gain * innovation.e,
                self.correction.n + gain * innovation.n,
                self.correction.u + gain * innovation.u,
            )

            # Rate: learn how fast the witness is drifting, so the correction
            # can keep pace instead of forever chasing. Clamped to what this
            # IMU could plausibly do — see MAX_DRIFT_VEL_MPS. That clamp is
            # what stops the same machinery quietly absorbing the attack.
            vel_gain = min(1.0, gnss_dt / DRIFT_VEL_TAU_S)
            target = ENU(
                innovation.e / AIDING_TAU_S,
                innovation.n / AIDING_TAU_S,
                innovation.u / AIDING_TAU_S,
            )
            self.drift_vel = _clamp_horizontal(
                ENU(
                    self.drift_vel.e + vel_gain * (target.e - self.drift_vel.e),
                    self.drift_vel.n + vel_gain * (target.n - self.drift_vel.n),
                    self.drift_vel.u + vel_gain * (target.u - self.drift_vel.u),
                ),
                MAX_DRIFT_VEL_MPS,
            )
            self.unaided_s = 0.0
        else:
            self.unaided_s += max(0.0, gnss_dt)

        # Carry the learned drift rate forward to the next fix. Done whether or
        # not aiding is on: once GNSS is frozen out, the last known drift rate
        # is still our best guess at where the witness is heading.
        if gnss_dt > 0.0:
            self.correction = ENU(
                self.correction.e + self.drift_vel.e * gnss_dt,
                self.correction.n + self.drift_vel.n * gnss_dt,
                self.correction.u + self.drift_vel.u * gnss_dt,
            )

        # While aided, error is bounded near one time constant of drift rather
        # than growing without limit. Once aiding stops, it grows again from
        # there — the operator sees exactly how long they can keep going.
        free_run_s = AIDING_TAU_S + self.unaided_s
        sigma = SIGMA_FLOOR_M + DRIFT_RATE_MPS * free_run_s
        residual = Residual(
            horizontal_m=horizontal,
            vertical_m=vertical,
            sigma_m=sigma,
            ratio=horizontal / sigma,
            gnss_pos=gnss_pos,
            witness_pos=witness_pos,
            t=frame.t,
        )
        self.last = residual
        return residual
