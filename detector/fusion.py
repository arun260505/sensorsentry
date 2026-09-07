"""Stage 8 — drop the liar and keep going.

Everything before this produces a verdict. This acts on it, and without it the
whole thing is a warning light: the detector announces that GPS is being
spoofed and the vehicle carries on navigating by the spoofed position, which
helps nobody.

So when a sensor loses trust it is **removed from the navigation solution**.
The vehicle keeps flying on what is left, holding its true route while the
reported position walks away. That is the moment the demo turns on — the two
paths separate on the map and the blue one, the one the vehicle is actually
steering by, stays on course.

## The part that has to stay honest

Running without GPS is not free. The remaining sensors drift, and they drift
faster the longer they run, so the estimate gets worse every second. Hiding
that would be the easy thing and the wrong one: an operator who does not know
how long they can keep going cannot decide whether to press on or land.

So alongside the position we publish an **error budget** — how far out we
might be, growing from the moment aiding stopped — and how long remains before
it exceeds what the operator called safe. When it runs out the answer is not a
better estimate, it is "stop".

## Why it does not re-anchor

The obvious shortcut is to snap back to GPS once things look calm again.
That is exactly what a patient attacker wants: hold the spoof steady, wait for
the system to relax, and be adopted as truth. Trust returns only through the
same hysteresis as everything else (stage 7), and the witness is never
re-anchored mid-run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from . import profiles
from .blame import Blame
from .deadreckon import DeadReckoner, Witness
from .geo import Origin, llh_from_enu
from .residual import DRIFT_RATE_MPS, SIGMA_FLOOR_M, ResidualTracker

GNSS_FUSED = "gnss_fused"
DEAD_RECKONING = "dead_reckoning"

FREE_RUN_BASE_M = 25.0
"""Error already present at the moment aiding stopped, in metres.

The budget cannot start from zero. By the time GPS has been distrusted the
witness has been running for a while and is already tens of metres out; the
aiding was holding that in check, not eliminating it. Starting the count at
zero made the budget optimistic during exactly the window the operator is
using it to decide whether to press on."""

FREE_RUN_ACCEL = 0.18
"""Growth of the error budget while running with no GPS, as an effective
acceleration in m/s^2, giving 0.5 * a * t^2 on top of the base above.

Quadratic, not linear, and measured rather than assumed. The linear rate used
while GPS aids the witness is fine there because aiding bounds the error. Free
running it is not.

Measured from the moment aiding stopped, against the simulator: 20 m after 14
seconds, 144 m after 44, 475 m after 74, 717 m after 99. That is a curve, and
fitting it gives about 0.17; rounded up so the number errs pessimistic.

The first attempt used 0.072, fitted to drift from the start of the run rather
than from the freeze, and claimed 190 m while the witness was 729 m out. That
is the one direction this number must never be wrong in: an operator deciding
whether to keep going is handed a figure four times better than the truth, and
keeps going."""

SAFE_ERROR_M = 150.0
"""How far out we may be before the operator should stop relying on us.

Not a physical limit — an operational one, and it belongs to the customer. A
delivery drone threading a corridor needs it far tighter than a truck on a
motorway. It exists so the console can say "about 90 seconds left" instead of
showing a number that means nothing to the person reading it."""


@dataclass
class Navigation:
    """Where the vehicle believes it is, and how much to trust that."""

    lat: float
    lon: float
    alt: float

    source: str = GNSS_FUSED
    """`gnss_fused` while GPS is trusted, `dead_reckoning` once it is not."""

    error_budget_m: float = SIGMA_FLOOR_M
    """How far out this position might be."""

    seconds_remaining: Optional[float] = None
    """Before the budget passes SAFE_ERROR_M. None while GPS is trusted, since
    then it is not growing."""

    dropped: list[str] = field(default_factory=list)
    """Sensors excluded from the solution, in the order they were dropped."""

    note: str = ""
    """One line for the operator, in their words."""


_FRIENDLY = {"gnss": "GPS", "imu": "the motion sensor", "baro": "the altitude sensor",
             "mag": "the compass", "odom": "the wheel sensor"}


class Fusion:
    """Turns a verdict into a navigation solution.

    Holds no estimate of its own. It decides which sensors are allowed to
    contribute and then reads the answer out of the witness, so there is
    exactly one position in the system and no chance of the console and the
    vehicle believing different things.
    """

    def __init__(self, profile: profiles.Profile):
        self.profile = profile
        self.dropped: list[str] = []

    def reset(self) -> None:
        self.dropped = []

    def update(
        self,
        blame: Blame,
        state: str,
        witness: Witness,
        reckoner: DeadReckoner,
        tracker: ResidualTracker,
    ) -> Optional[Navigation]:
        """Apply the verdict, then report where we are.

        Returns None before the run is anchored, when there is no position to
        report yet.
        """
        guilty = blame.guilty if (state == "ALERT" and blame.isolated) else None
        self._apply(guilty, reckoner, tracker)

        if tracker.origin is None or tracker._witness_at_anchor is None:
            return None

        position = (witness.displacement - tracker._witness_at_anchor) + tracker.correction
        lat, lon, alt = llh_from_enu(position, tracker.origin)

        if tracker.aiding:
            budget = SIGMA_FLOOR_M + DRIFT_RATE_MPS * tracker.unaided_s
            return Navigation(
                lat=lat, lon=lon, alt=alt, source=GNSS_FUSED,
                error_budget_m=budget, dropped=list(self.dropped),
                note="Navigating normally.",
            )

        # Free-running. The budget grows from when aiding stopped, and the
        # operator is told how long that leaves them.
        free_s = tracker.unaided_s
        budget = FREE_RUN_BASE_M + 0.5 * FREE_RUN_ACCEL * free_s * free_s
        safe_until = math.sqrt(max(0.0, 2.0 * (SAFE_ERROR_M - FREE_RUN_BASE_M) / FREE_RUN_ACCEL))
        remaining = max(0.0, safe_until - free_s)

        note = f"Ignoring {_FRIENDLY.get(guilty or 'gnss', guilty or 'GPS')}. "
        if remaining > 0:
            note += (f"Navigating on the vehicle's own sensors — accurate to about "
                     f"{budget:.0f} m, roughly {remaining:.0f} s before that "
                     f"exceeds the safe limit.")
        else:
            note += ("Position is no longer reliable enough to navigate on. "
                     "Stop or land.")

        return Navigation(
            lat=lat, lon=lon, alt=alt, source=DEAD_RECKONING,
            error_budget_m=budget, seconds_remaining=remaining,
            dropped=list(self.dropped), note=note,
        )

    # --- internals ---------------------------------------------------------

    def _apply(self, guilty: Optional[str], reckoner: DeadReckoner,
               tracker: ResidualTracker) -> None:
        """Let trusted sensors contribute; keep the accused one out.

        Rebuilt from the current verdict every cycle rather than latched, so a
        sensor that recovers is allowed back in — but only once stage 7's
        hysteresis has let the state fall, which takes far longer than it takes
        to be excluded. Quick to distrust, slow to forgive.
        """
        self.dropped = [guilty] if guilty else []

        if guilty == profiles.GNSS:
            # Stop letting GPS correct the witness. Without this the spoofed
            # position keeps dragging the estimate along and the fallback is
            # decoration.
            tracker.freeze_aiding()
        else:
            tracker.resume_aiding()

        reckoner.use_compass = guilty != profiles.MAG
        reckoner.use_baro = guilty != profiles.BARO
