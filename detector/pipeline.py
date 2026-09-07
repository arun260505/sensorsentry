"""The detector stages wired together, so nothing has to duplicate the wiring.

`run.py` prints this to a terminal and `server.py` streams it to a browser;
both see exactly the same state object, so what the console shows is what the
detector actually decided.

Stages 1-3 plus the residual are real. Stages 4-10 arrive in later phases and
are declared here as placeholders rather than faked, so it is obvious from the
output what exists and what does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import health, profiles
from .crossvalidate import CrossValidator, PairScore
from .deadreckon import DeadReckoner, Witness
from .geo import ENU, llh_from_enu
from .ingest import Frame, FrameStream, RunHeader
from .residual import Residual, ResidualTracker
from .trust import PairTrust

# --- PROVISIONAL ----------------------------------------------------------
# A threshold on the worst cross-validation pair, standing in until phases 5-7
# land. It is now driven by the pair scores rather than the position residual
# alone, because the position residual cannot see a slow walk-off at all (see
# crossvalidate.py). Measured against the simulator: honest flights sit at
# 1.1-1.7x, a 2 m/s walk-off reaches 4.0x, a magnet 9.3x.
# It is enough to make the console show something real, and it is deliberately
# not called a verdict anywhere the operator can see. The actual decision needs
# blame assignment (which sensor), classification (attack or fault) and
# hysteresis (sustained, not instantaneous) — none of which exist yet.
WATCH_RATIO = 2.0
ALERT_RATIO = 2.5
# --------------------------------------------------------------------------

OK, WATCH, ALERT = "OK", "WATCH", "ALERT"


@dataclass
class State:
    """Everything known about the vehicle this cycle."""

    header: RunHeader
    profile_name: str
    t: float
    seq: int

    witness: Witness
    residual: Optional[Residual]
    health: dict[str, health.SensorHealth]
    pairs: list[PairScore] = field(default_factory=list)

    state: str = OK
    """What the operator is told, after hysteresis."""

    instant_state: str = OK
    """Last usable reading from the checks, before hysteresis. Holds its
    previous value on frames where nothing could be checked."""
    """What the checks say this very frame, before hysteresis. Kept so the
    console can show evidence building without the badge flickering."""

    pair_states: dict[str, str] = field(default_factory=dict)
    """Each cross-check's settled state, after its own hysteresis."""

    anchored: bool = False

    gnss_enu: Optional[ENU] = None
    witness_enu: Optional[ENU] = None

    frames_seen: int = 0
    frames_dropped: int = 0

    def to_json(self) -> dict[str, Any]:
        """Shape sent to the console. See docs/schema.md."""
        residual = self.residual
        return {
            "run_id": self.header.run_id,
            "vehicle_id": self.header.vehicle_id,
            "vehicle_type": self.profile_name,
            "t": round(self.t, 2),
            "seq": self.seq,
            "anchored": self.anchored,
            "state": self.state,
            "instant_state": self.instant_state,
            "pair_states": dict(self.pair_states),
            "residual": None if residual is None else {
                "horizontal_m": round(residual.horizontal_m, 2),
                "vertical_m": round(residual.vertical_m, 2),
                "sigma_m": round(residual.sigma_m, 2),
                "ratio": round(residual.ratio, 3),
            },
            "witness": {
                "e": round(self.witness_enu.e, 2) if self.witness_enu else None,
                "n": round(self.witness_enu.n, 2) if self.witness_enu else None,
                "u": round(self.witness_enu.u, 2) if self.witness_enu else None,
                "speed_mps": round(
                    (self.witness.velocity.e ** 2 + self.witness.velocity.n ** 2) ** 0.5, 2
                ),
                "distance_m": round(self.witness.distance_travelled_m, 1),
                "sigma_m": round(self.witness.sigma_m, 1),
                "elapsed_s": round(self.witness.elapsed_s, 1),
            },
            "gnss": None if self.gnss_enu is None else {
                "e": round(self.gnss_enu.e, 2),
                "n": round(self.gnss_enu.n, 2),
                "u": round(self.gnss_enu.u, 2),
            },
            "pairs": [
                {
                    "a": p.a, "b": p.b, "label": p.label,
                    "value": round(p.value, 2), "unit": p.unit,
                    "sigma": round(p.sigma, 2), "ratio": round(p.ratio, 2),
                    "valid": p.valid, "reason": p.reason,
                }
                for p in self.pairs
            ],
            "health": {
                name: {"healthy": h.healthy, "flags": list(h.flags)}
                for name, h in self.health.items()
            },
            "frames": {"seen": self.frames_seen, "dropped": self.frames_dropped},
        }


class Pipeline:
    """One vehicle's detector. Feed it decoded messages, get State back."""

    def __init__(self, vehicle_type_override: Optional[str] = None):
        self.override = vehicle_type_override
        self.stream = FrameStream()
        self.profile: Optional[profiles.Profile] = None
        self.monitor: Optional[health.HealthMonitor] = None
        self.reckoner: Optional[DeadReckoner] = None
        self.tracker: Optional[ResidualTracker] = None
        self.crossvalidator: Optional[CrossValidator] = None
        self.trust = PairTrust(WATCH_RATIO, ALERT_RATIO)
        self.last_state: Optional[State] = None
        self.started = False

    def accept(self, message: dict[str, Any]) -> Optional[State]:
        """Returns State for a sensor frame, or None for a header or a frame
        arriving before any run has started."""
        frame = self.stream.accept(message)

        if frame is None:
            header = self.stream.header
            if header is not None and not self._matches(header):
                self._begin(header)
            return None

        if self.reckoner is None or self.monitor is None or self.tracker is None:
            # Frames before a run_start: we attached mid-run and cannot know
            # which profile applies. Wait for the next run rather than guess.
            return None

        report = self.monitor.update(frame)
        witness = self.reckoner.update(frame)
        residual = self.tracker.update(frame, witness)
        if residual is None:
            residual = self.tracker.last

        pairs: list[PairScore] = []
        if self.crossvalidator is not None:
            pairs = self.crossvalidator.update(frame, witness, self.tracker.origin)

        state = State(
            header=self.stream.header,          # type: ignore[arg-type]
            profile_name=self.profile.name,     # type: ignore[union-attr]
            t=frame.t,
            seq=frame.seq,
            witness=witness,
            residual=residual,
            health=report,
            pairs=pairs,
            anchored=self.tracker.anchored,
            frames_seen=self.stream.frames_seen,
            frames_dropped=self.stream.frames_dropped,
        )

        if self.tracker.anchored and residual is not None:
            state.gnss_enu = residual.gnss_pos
            state.witness_enu = residual.witness_pos
            instant = self._instant_state(pairs)
            if instant is not None:
                state.instant_state = instant
            state.state, state.pair_states = self.trust.update(frame.dt, pairs)

        self.last_state = state
        return state

    # --- internals ---------------------------------------------------------

    def _matches(self, header: RunHeader) -> bool:
        return (
            self.profile is not None
            and self.last_state is not None
            and self.last_state.header.run_id == header.run_id
        )

    def _begin(self, header: RunHeader) -> None:
        self.profile = profiles.get(self.override or header.vehicle_type)
        self.monitor = health.HealthMonitor(self.profile)
        self.reckoner = DeadReckoner(self.profile)
        self.tracker = ResidualTracker(self.profile.accel_bias_sigma)
        self.crossvalidator = CrossValidator(self.profile)
        self.trust.reset()
        self.last_state = None
        self.started = True

    @staticmethod
    def _instant_state(pairs: list[PairScore]) -> Optional[str]:
        """Worst valid pair decides, for now.

        Deliberately crude: it takes the loudest disagreement and thresholds
        it. It does not know *which* sensor is at fault, cannot tell an attack
        from a failure, and reacts to a single sample. Those are stages 5, 6
        and 7 — the parts that make this a diagnosis rather than an alarm.
        """
        usable = [p.ratio for p in pairs if p.valid]
        if not usable:
            # Nothing could be checked this frame. Say so rather than
            # reporting OK — see Hysteresis.update.
            return None
        worst = max(usable)
        if worst >= ALERT_RATIO:
            return ALERT
        if worst >= WATCH_RATIO:
            return WATCH
        return OK
