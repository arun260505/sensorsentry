"""Stage 7 — require sustained evidence before changing state.

The cross-checks in stage 4 are noisy by nature: they compare independent
sensors, each with its own noise, over short windows. On an honest three-minute
flight a handful of samples out of several thousand will cross any threshold
worth setting — measured, four to ten of 3600. React to those and the operator
sees an alarm every couple of minutes on a vehicle that is completely fine.

Which is worse than useless. A detector that cries wolf gets switched off by
the person it was bought to protect, usually within the week, and then it
protects nobody at all. Rule 3 in CLAUDE.md exists for this reason: zero false
alarms is a gate, not a goal.

So a state change costs time, not a sample. The evidence has to persist before
we escalate, and it has to stay away before we relax. Real attacks last tens of
seconds and clear the bar easily; noise spikes last a fraction of one and never
do.

Deliberately asymmetric: quick to worry, slow to reassure. Coming down from an
alert takes longer than going up, so a spoofer cannot get a free window by
letting the attack breathe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

OK, WATCH, ALERT = "OK", "WATCH", "ALERT"

_RANK = {OK: 0, WATCH: 1, ALERT: 2}

RISE_S = 2.0
"""Seconds a worse reading must persist before the state escalates.

Two seconds is 40 frames at 20 Hz, and roughly ten GNSS fixes. Long enough
that no measured noise spike survives it; short enough that a real attack is
still caught within seconds of becoming visible."""

FALL_S = 6.0
"""Seconds a better reading must persist before the state relaxes.

Three times the rise, on purpose. An attacker who pauses briefly should not
get the system back to OK, and an operator watching a borderline case should
not see the badge flickering between states while they try to read it."""


LEAK = 0.5
"""How fast the case for changing state decays when evidence stops, relative
to how fast it builds.

Half speed. Evidence that appears more than a third of the time still gets
there eventually, which is what a noisy sensor looks like; evidence that
appears in one sample of a thousand never does, which is what noise looks
like."""


@dataclass
class Hysteresis:
    """Turns an instantaneous reading into a state that means something.

    Feed it what the checks say right now; it returns what the system should
    actually be telling the operator.
    """

    rise_s: float = RISE_S
    fall_s: float = FALL_S
    state: str = OK

    _held_s: float = 0.0
    """How long the instantaneous reading has disagreed with the current
    state, in the direction it is currently disagreeing."""

    steady_s: float = 0.0
    """How long this check has been in the state it is in.

    Stage 5 needs it, and for a reason that only shows up on an intermittent
    fault. A check that has *just this instant* gone quiet is not evidence that
    a sensor has been behaving; it is evidence that it is behaving right now.
    Clearing a sensor on that basis, of a failure measured over the last four
    seconds, blames whoever else was standing there."""

    def reset(self) -> None:
        self.state = OK
        self._held_s = 0.0
        self.steady_s = 0.0

    def update(self, dt: float, instant: Optional[str]) -> str:
        """`instant` of None means the checks could not be evaluated at all.

        That is not the same as "everything is fine", and treating it as such
        was quietly fatal: the most sensitive check only applies while the
        vehicle flies straight, so every turn reported OK, reset the evidence
        timer, and an attack in progress could never accumulate the seconds it
        needed to be believed. Detection went to zero while the numbers
        underneath were screaming.

        With no evidence we hold: neither escalate nor relax, and keep the
        partial case intact for when the check becomes available again.
        """
        if dt <= 0.0:
            return self.state

        # Time in state is wall-clock, and counted before the unevaluable
        # bail-out below on purpose. The settled state persists whether or not
        # the check could be read this frame — a check that has been quiet for
        # twenty seconds has been quiet for twenty seconds, however often we
        # happened to look at it.
        #
        # Counting only evaluable frames made this a quarter of real time for
        # anything involving GNSS, which arrives at 5 Hz against 20 Hz frames.
        # Stage 5 reads it as seconds, so a seized odometer that used to be
        # named in 7 s took 39: the alibi it was waiting on had been steady all
        # along and the clock said otherwise.
        self.steady_s += dt
        if instant is None:
            return self.state

        here, there = _RANK[self.state], _RANK[instant]

        if there == here:
            # Agrees with where we are: the case for moving leaks away, but it
            # does not vanish. Demanding strictly *continuous* evidence looked
            # tidier and quietly missed the most obvious fault there is — a
            # compass gone noisy crosses back under the threshold between
            # samples, resetting the timer forever, so a sensor reading 17x
            # normal raised nothing at all. Intermittent evidence is still
            # evidence; a single spike is not.
            self._held_s = max(0.0, self._held_s - dt * LEAK)
            return self.state

        self._held_s += dt
        threshold = self.rise_s if there > here else self.fall_s
        if self._held_s >= threshold:
            self.state = instant
            self._held_s = 0.0
            self.steady_s = 0.0
        return self.state

    @property
    def settling(self) -> bool:
        """True while evidence is building but has not yet moved the state.
        The console uses this to show something is being considered, rather
        than looking frozen during the two seconds before an alert."""
        return self._held_s > 0.0


def classify(ratio: float, watch: float, alert: float) -> str:
    """Turn one pair's ratio into an instantaneous level."""
    if ratio >= alert:
        return ALERT
    if ratio >= watch:
        return WATCH
    return OK


class PairTrust:
    """Hysteresis kept independently for every cross-check.

    One shared timer across all pairs does not work, and the reason is worth
    recording. The pairs are not available at the same moments: the course
    check needs a GNSS fix and straight flight, the compass check runs every
    frame. Take the worst pair each frame and the answer flickers with
    *availability* rather than with evidence — three frames in four have no
    GNSS, the quiet pairs win, the timer resets, and an attack that is plainly
    visible in the numbers can never accumulate the seconds it needs.

    So each pair carries its own case. A pair that cannot be evaluated holds
    what it had; a pair that can, builds or decays on its own schedule. The
    reported state is the worst of them.

    Stage 5 wants this shape anyway: to accuse a sensor you need to know which
    checks have been failing and for how long, not merely which one is loudest
    right now.
    """

    def __init__(self, watch_ratio: float, alert_ratio: float,
                 rise_s: float = RISE_S, fall_s: float = FALL_S):
        self.watch_ratio = watch_ratio
        self.alert_ratio = alert_ratio
        self.rise_s = rise_s
        self.fall_s = fall_s
        self._per_pair: dict[str, Hysteresis] = {}

    def reset(self) -> None:
        self._per_pair.clear()

    def steady(self) -> dict[str, float]:
        """How long each check has held its settled state, in seconds.

        Stage 5 uses it to refuse an alibi from a check that has only just
        stopped failing."""
        return {key: hyst.steady_s for key, hyst in self._per_pair.items()}

    def update(self, dt: float, pairs, health=None) -> tuple[str, dict[str, str]]:
        """Returns the overall state and each signal's settled state.

        A sensor's own health is treated as a signal in its own right, not
        merely as supporting evidence. A barometer frozen on one value is a
        fault whether or not any cross-check happens to notice — and one may
        well not, if the vehicle is holding altitude at the time. Waiting for
        a disagreement to appear before admitting a sensor is broken means the
        obvious failures are the ones that get missed.
        """
        settled: dict[str, str] = {}
        for sensor, report in (health or {}).items():
            key = f"health:{sensor}"
            hyst = self._per_pair.get(key)
            if hyst is None:
                hyst = self._per_pair[key] = Hysteresis(self.rise_s, self.fall_s)
            settled[key] = hyst.update(dt, ALERT if not report.healthy else OK)

        for pair in pairs:
            key = pair.key
            hyst = self._per_pair.get(key)
            if hyst is None:
                hyst = self._per_pair[key] = Hysteresis(self.rise_s, self.fall_s)
            instant = classify(pair.ratio, self.watch_ratio, self.alert_ratio) if pair.valid else None
            settled[key] = hyst.update(dt, instant)

        worst = OK
        for state in settled.values():
            if _RANK[state] > _RANK[worst]:
                worst = state
        return worst, settled

    def failing(self) -> list[str]:
        """Pairs currently settled above OK — the evidence list for stage 5."""
        return [k for k, h in self._per_pair.items() if h.state != OK]
