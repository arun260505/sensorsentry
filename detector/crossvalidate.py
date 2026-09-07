"""Stage 4 — score every sensor pair against every other.

The problem statement asks for a system that *cross-validates*, and this is
that word. Not GNSS against one reference: every pair that can disagree,
scored independently, so stage 5 can find the sensor that disagrees with
everyone rather than merely noticing that something is wrong.

Each pair reports its disagreement in its own natural unit — degrees for a
heading check, metres for an altitude check — divided by what that
disagreement looks like when nothing is wrong. That ratio is comparable across
pairs even though the quantities are not.

**Why this stage carries the detection, and not the position residual.**
Integrating an IMU is a poor way to catch a slow walk-off. A constant pitch
error of half a degree — well inside what a complementary filter leaves
behind — leaks enough gravity to build a 6 m/s velocity error inside a minute,
which swamps a 2 m/s attack no matter how the uncertainty is modelled. That is
a known limit of inertial-only spoofing detection, not a bug we can tune out.

Direction is a different matter. Pull a vehicle moving at 12 m/s sideways at
2 m/s and its course over ground swings about 9 degrees, while the compass
still reads the way the airframe is pointing. The compass is good to 1.5
degrees. That is a signal several times its own noise, from a sensor a radio
attack cannot reach — measured at 2.4 degrees on a clean flight against 10.6
degrees under a 2 m/s walk-off.

So the sensitive checks are the ones comparing *what kind of motion* each
sensor describes, not the ones comparing accumulated position.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from . import profiles
from .deadreckon import Witness
from .geo import ENU, Origin, enu_from_llh, wrap_pi
from .ingest import Frame

# --- tunables, all measured against simulator/ ----------------------------

COURSE_WINDOW_S = 4.0
"""Seconds of travel used to measure course over ground.

Long enough that GNSS position noise averages down — 1.5 m of jitter over 50 m
of travel is under 2 degrees — and short enough to still be inside the attack
rather than smeared across a turn."""

COURSE_STRAIGHT_RATE = 0.05
"""Highest turn rate, rad/s, at which the course check still means anything.

During a turn the vehicle's course legitimately differs from where it points,
so the check is simply not valid there — ungated it reads 23 degrees of error
on honest flying, which is worse than the attack. Gated it reads 2.2."""

COURSE_MIN_SPEED = 8.0
"""Minimum speed, m/s, for the course check. Course over ground is
meaningless when barely moving: the direction of a 2 m step is mostly noise."""

COURSE_SIGMA_DEG = 3.0
"""Expected course-vs-compass disagreement on an honest flight, in degrees.
Measured 2.2-2.6 mean, 5.3-6.1 at the 95th percentile, across both scenarios
and several seeds."""

HEADING_SIGMA_DEG = 6.0
"""Expected compass-vs-gyro disagreement, in degrees.

Fixed rather than growing, now that the gyro heading is re-seeded and so
cannot drift away on its own. Measured on honest flights including hard
manoeuvring."""

GYRO_RESEED_TAU_S = 45.0
"""Time constant for pulling the gyro heading back toward the compass.

Left alone, an integrated gyro heading drifts without bound — scale error
accumulates over every turn — so comparing it against the compass across a
whole flight measures our own drift more than anything else. Growing the
tolerance to cover that just makes the check blind: a compass wandering by 35
degrees scored 1.0x normal and raised nothing at all.

So the gyro heading is slowly re-seeded from the compass. Drift is absorbed;
anything faster than the time constant is not. A magnet arriving shows up for
about a minute before being absorbed, which is far longer than needed, and a
compass that keeps wandering never stops showing.

The honest cost, and it is the same trade as the GNSS aiding: a compass error
that creeps in more slowly than 45 seconds is invisible to this check."""

ALT_SIGMA_M = 5.0
"""Expected GNSS-vs-barometric altitude disagreement, in metres. GNSS altitude
noise alone is about 3 m, and the barometer has its own slow drift."""

POSITION_SIGMA_FLOOR_M = 4.0
"""Floor for the position pair. See residual.py — that pair is kept for the
map and the operator's error budget, and is deliberately not what decides."""


@dataclass
class PairScore:
    """One cross-check between two sensors, this cycle."""

    a: str
    b: str
    label: str
    """Plain words, because this reaches the operator's evidence list."""

    kind: str = ""
    """Which comparison this is. Two sensors can be checked more than one way,
    so the kind — not the sensor names — is what identifies a check."""

    domain: str = ""
    """What the check is sensitive to: heading, horizontal or vertical. Blame
    only lets a sensor be cleared by a check from the same domain."""

    value: float = 0.0
    """Measured disagreement, in `unit`. Always positive."""

    signed: float = 0.0
    """The same disagreement, keeping its direction.

    Stage 6 needs the sign. A failing sensor wanders either way at random; an
    attacker pulls one way, because he wants the vehicle somewhere specific.
    Take the absolute value first and that distinction is gone for good."""

    unit: str = "m"
    sigma: float = 1.0
    """What this disagreement looks like when nothing is wrong."""

    ratio: float = 0.0
    """value / sigma. Comparable across pairs even though the units are not."""

    valid: bool = False
    """False when the check could not be evaluated this cycle."""

    reason: str = ""
    """Why it could not — shown so a blank check never looks like a pass."""

    @property
    def key(self) -> str:
        return f"{self.a}-{self.b}:{self.kind}"

    def as_evidence(self) -> str:
        return f"{self.label}: {self.value:.1f} {self.unit} ({self.ratio:.1f}x normal)"


class CrossValidator:
    """Runs every applicable pair check, once per frame.

    One instance per vehicle. Which pairs run comes from the vehicle profile,
    so a truck picks up its wheels and drops its barometer without a line of
    code changing here.
    """

    def __init__(self, profile: profiles.Profile):
        self.profile = profile
        self._origin: Optional[Origin] = None
        # (t, gnss ENU, compass heading deg, gyro-integrated heading deg)
        self._history: deque[tuple[float, ENU, float, float]] = deque(maxlen=600)
        self._turn_rates: deque[float] = deque(maxlen=200)
        self._gyro_heading_deg = 0.0
        self._gyro_seeded = False
        self.compass_trusted = True
        """Whether the compass may still correct the gyro heading.

        Cleared once the compass is under suspicion. Otherwise the re-seeding
        quietly absorbs the very offset that raised the suspicion: a magnet was
        correctly blamed for fifty seconds, then the gyro caught up with the
        corrupted compass, the two agreed again, and the accusation moved to
        GPS — which was innocent. A reference must stop following a sensor the
        moment that sensor stops being trustworthy."""

        self._total_turn_deg = 0.0
        """How far the vehicle has turned in total. Gyro heading error is
        mostly scale error, so it accumulates with rotation, not with time."""

    def reset(self) -> None:
        self._origin = None
        self._history.clear()
        self._turn_rates.clear()
        self._gyro_heading_deg = 0.0
        self._gyro_seeded = False
        self._total_turn_deg = 0.0

    # --- main entry ---------------------------------------------------------

    def update(self, frame: Frame, witness: Witness, origin: Optional[Origin]) -> list[PairScore]:
        """Score every applicable pair. Returns one PairScore per pair, always
        the same list in the same order, valid or not — stage 5 needs to know
        which checks were unavailable as much as which ones failed."""
        self._origin = origin
        self._track_gyro_heading(frame, witness)
        self._record(frame)

        scores: list[PairScore] = []
        for pair in self.profile.pairs:
            scores.append(self._score(pair, frame, witness))
        return scores

    # --- bookkeeping --------------------------------------------------------

    def _track_gyro_heading(self, frame: Frame, witness: Witness) -> None:
        """Integrate the gyro into a heading of its own.

        Deliberately kept separate from the compass so the two can be compared.
        A magnet near the compass moves one and not the other, and that
        difference is the whole signature of environmental interference."""
        rate = abs(float(frame.imu.get("gz", 0.0)))
        self._turn_rates.append(rate)

        if frame.mag is not None and "heading_deg" in frame.mag and not self._gyro_seeded:
            self._gyro_heading_deg = float(frame.mag["heading_deg"])
            self._gyro_seeded = True
            return

        if frame.dt > 0.0 and self._gyro_seeded:
            # Heading rate is not the gyro's z reading. Only a level vehicle
            # turns about its own z axis; banked over, part of the turn shows
            # up on y and the rest is foreshortened by pitch. Integrating gz
            # raw under-reads every banked turn by about 13 percent at 30
            # degrees, which accumulated into 171 degrees of phantom offset
            # across a manoeuvring flight — read as a failing compass on a
            # vehicle that was completely fine.
            roll, pitch = witness.roll, witness.pitch
            cos_pitch = math.cos(pitch)
            if abs(cos_pitch) < 1e-4:
                cos_pitch = math.copysign(1e-4, cos_pitch or 1.0)
            yaw_rate = (
                math.sin(roll) * float(frame.imu.get("gy", 0.0))
                + math.cos(roll) * float(frame.imu.get("gz", 0.0))
            ) / cos_pitch

            # Positive yaw is counter-clockwise in ENU; compass heading runs
            # clockwise from north, so the sign flips.
            step = math.degrees(yaw_rate) * frame.dt
            heading = self._gyro_heading_deg - step
            self._total_turn_deg += abs(step)

            # Pull slowly back toward the compass. Without this the integrated
            # heading drifts without bound and the check ends up measuring our
            # own error; with it, drift is absorbed while anything faster than
            # the time constant still stands off.
            if (self.compass_trusted and frame.mag is not None
                    and "heading_deg" in frame.mag):
                gain = min(1.0, frame.dt / GYRO_RESEED_TAU_S)
                heading += gain * math.degrees(
                    wrap_pi(math.radians(float(frame.mag["heading_deg"]) - heading))
                )

            self._gyro_heading_deg = heading % 360.0

    def _record(self, frame: Frame) -> None:
        if not frame.has_gnss() or self._origin is None:
            return
        gnss = frame.gnss or {}
        pos = enu_from_llh(
            float(gnss["lat"]), float(gnss["lon"]), float(gnss["alt"]), self._origin
        )
        heading = float(frame.mag["heading_deg"]) if frame.mag else 0.0
        self._history.append((frame.t, pos, heading, self._gyro_heading_deg))

    def _sample_before(self, t: float, window: float):
        cutoff = t - window
        chosen = None
        for entry in self._history:
            if entry[0] <= cutoff:
                chosen = entry
            else:
                break
        return chosen

    def _turning(self, window: float, dt: float) -> bool:
        """True if the vehicle turned at any point in the window.

        The worst sample, not the average: a check that is invalid for part of
        a window is invalid for the window."""
        if dt <= 0.0:
            return True
        needed = int(window / dt)
        if len(self._turn_rates) < needed or needed == 0:
            return True
        recent = list(self._turn_rates)[-needed:]
        return max(recent) > COURSE_STRAIGHT_RATE

    # --- the checks ---------------------------------------------------------

    def _score(self, pair: profiles.Pair, frame: Frame, witness: Witness) -> PairScore:
        score = PairScore(a=pair.a, b=pair.b, label=pair.label,
                          kind=pair.kind, domain=pair.domain)

        if pair.kind == "course_mag":
            return self._course_vs_heading(score, frame, use_compass=True)
        if pair.kind == "heading_offset":
            return self._compass_vs_gyro(score, frame)
        if pair.kind == "altitude":
            return self._gnss_alt_vs_baro(score, frame, witness)
        if pair.kind == "position":
            return self._position_vs_witness(score, frame, witness)

        score.reason = "not implemented yet"
        return score

    def _course_vs_heading(self, score: PairScore, frame: Frame,
                           *, use_compass: bool) -> PairScore:
        """Which way GNSS says we are travelling, against which way we point.

        The most sensitive check we have against a walk-off: dragging a vehicle
        sideways swings its course over the ground while the airframe carries on
        pointing where it was pointing.

        Compared against the compass, which gives an absolute heading. Running
        the same check against the gyro-integrated heading was tried and
        removed: a gyro has no absolute reference, so its heading accumulates
        scale error over every turn and reads 51x normal on an honest
        manoeuvring flight — far worse than any attack. Isolating GNSS instead
        comes from the compass being corroborated by the gyro over the same
        window, which is what `heading_offset` measures.
        """
        score.unit = "deg"
        score.sigma = COURSE_SIGMA_DEG

        if not frame.has_gnss() or self._origin is None:
            score.reason = "no GNSS fix this frame"
            return score
        if use_compass and frame.mag is None:
            score.reason = "no compass"
            return score
        if not use_compass and not self._gyro_seeded:
            score.reason = "gyro heading not established yet"
            return score
        if self._turning(COURSE_WINDOW_S, frame.dt):
            score.reason = "turning — course and heading legitimately differ"
            return score

        past = self._sample_before(frame.t, COURSE_WINDOW_S)
        if past is None:
            score.reason = "not enough history yet"
            return score

        _t0, pos0, _h0, _g0 = past
        now = self._history[-1]
        de, dn = now[1].e - pos0.e, now[1].n - pos0.n
        travelled = math.hypot(de, dn)
        if travelled < COURSE_MIN_SPEED * COURSE_WINDOW_S * 0.6:
            score.reason = "too slow for course to mean anything"
            return score

        course_deg = (90.0 - math.degrees(math.atan2(dn, de))) % 360.0
        heading_deg = now[2] if use_compass else self._gyro_heading_deg
        signed = math.degrees(wrap_pi(math.radians(course_deg - heading_deg)))
        error = abs(signed)
        score.signed = signed
        score.value = error
        score.ratio = error / score.sigma
        score.valid = True
        return score

    def _compass_vs_gyro(self, score: PairScore, frame: Frame) -> PairScore:
        """Does the compass still point where the gyro says it should?

        Compared as absolute headings, not as turns over a window. A magnet
        laid beside the compass shifts it by a constant and then leaves it
        alone, so the *turn* it reports stays perfectly normal — a windowed
        comparison sees only a blip as the magnet arrives and nothing after.
        The offset is the signature, so the offset is what we measure.

        This works because a seeded gyro holds heading remarkably well: at the
        bias of a commercial IMU it wanders well under a degree a minute. It is
        turns that cost it, through scale error, so the tolerance grows with
        distance turned rather than with time.

        Nothing here touches GNSS, so it keeps working while GNSS is being
        spoofed — which is what lets stage 5 tell a lying compass apart from a
        lying receiver.
        """
        score.unit = "deg"

        if frame.mag is None or not self._gyro_seeded:
            score.reason = "no compass"
            return score
        if self._total_turn_deg == 0.0 and not self._history:
            score.reason = "not enough history yet"
            return score

        compass_now = float(frame.mag["heading_deg"])
        offset = math.degrees(
            wrap_pi(math.radians(compass_now - self._gyro_heading_deg))
        )

        score.sigma = HEADING_SIGMA_DEG
        score.signed = offset
        score.value = abs(offset)
        score.ratio = score.value / score.sigma
        score.valid = True
        return score

    def _gnss_alt_vs_baro(self, score: PairScore, frame: Frame, witness: Witness) -> PairScore:
        """GNSS altitude against the barometer.

        The vertical channel is where altitude-only spoofing shows up, and a
        spoofer's radio does not reach a pressure sensor.
        """
        score.unit = "m"
        score.sigma = ALT_SIGMA_M

        if not frame.has_gnss() or self._origin is None:
            score.reason = "no GNSS fix this frame"
            return score
        if frame.baro is None:
            score.reason = "no barometer"
            return score

        gnss_alt_change = float((frame.gnss or {})["alt"]) - self._origin.alt
        signed = gnss_alt_change - witness.displacement.u
        error = abs(signed)
        score.signed = signed
        score.value = error
        score.ratio = error / score.sigma
        score.valid = True
        return score

    def _position_vs_witness(self, score: PairScore, frame: Frame, witness: Witness) -> PairScore:
        """GNSS position against the inertial estimate.

        Kept because it is what the operator sees on the map, and because it
        catches a teleport instantly. It is deliberately *not* the sensitive
        check for a slow walk-off — see the note at the top of this file.
        """
        score.unit = "m"
        if not frame.has_gnss() or self._origin is None:
            score.reason = "no GNSS fix this frame"
            return score

        gnss = frame.gnss or {}
        pos = enu_from_llh(
            float(gnss["lat"]), float(gnss["lon"]), float(gnss["alt"]), self._origin
        )
        error = (pos - witness.displacement).horizontal_norm()
        score.sigma = max(POSITION_SIGMA_FLOOR_M, witness.sigma_m)
        # A distance has no sign; coherence for this check comes from whether
        # it grows steadily, which stage 6 measures as a trend instead.
        score.signed = error
        score.value = error
        score.ratio = error / score.sigma
        score.valid = True
        return score
