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

DRIFT_RATE_MPS = 0.5
"""How fast the witness's uncertainty grows, in metres per second elapsed.

Linear, not quadratic, and that is a measured result rather than a modelling
convenience. The textbook bound for dead reckoning is an unknown accelerometer
bias integrating twice into b*t^2/2, but our complementary filter absorbs a
steady horizontal bias into a small pitch offset that cancels it (see
deadreckon.py). What is left — heading error and manoeuvre transients —
accumulates roughly linearly.

Measured on the test fixture: 3.3 m at 20 s, 7.7 m at 40 s, 12.2 m at 60 s.
That is about 0.2 m/s. We use 0.5 m/s to leave margin for turns, which the
fixture does not have and which are where dead reckoning really suffers.

The quadratic bound was not merely conservative, it was unusable: it reached
80 m while the witness was 12 m off, so every residual divided out to a ratio
near 1.0 and a live 2 m/s walk-off was invisible.

    *** RECALIBRATE IN PHASE 11 ***
    against Abishek's simulator, with real routes and real manoeuvres. These
    numbers come from straight-line fixture motion and will be optimistic.
    Phase 7 changes the picture again: once GNSS aids the witness while it is
    trusted, this stops being free-run drift and becomes a filter innovation.
"""

ANCHOR_SETTLE_S = 2.0
"""How long to let attitude settle before taking the anchor. Anchoring on the
very first frame captures a levelling transient and biases the whole run."""


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
        self.last: Optional[Residual] = None

    def reset(self) -> None:
        self.origin = None
        self.anchored_at_t = None
        self._witness_at_anchor = None
        self._anchor_elapsed = None
        self._first_frame_t = None
        self.last = None

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
            return None

        gnss_pos = enu_from_llh(lat, lon, alt, self.origin)
        # Both sides now measured from the same instant and the same place.
        witness_pos = witness.displacement - self._witness_at_anchor

        horizontal = (gnss_pos - witness_pos).horizontal_norm()
        vertical = gnss_pos.u - witness_pos.u

        # Drift is counted from the anchor, not from when the reckoner booted.
        since_anchor = witness.elapsed_s - (self._anchor_elapsed or 0.0)
        sigma = SIGMA_FLOOR_M + DRIFT_RATE_MPS * since_anchor
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
