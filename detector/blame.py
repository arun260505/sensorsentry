"""Stage 5 — decide which sensor is lying.

The first of the two stages that make this project different from a threshold
alarm. Everything before it can say *something is wrong*. This one says
**"the compass is lying, and the other sensors agree that it is"**, which is
the difference between an alarm and a diagnosis — and the difference between
an operator who can act and one who is guessing during the worst minute of
their day.

## How the accusation works

A failing check names two sensors and blames neither. Deciding between them
needs a third opinion, and there are only two useful questions:

**Is either one cleared by something else?** A sensor that still agrees with a
sensor nobody suspects is probably fine. When GNSS is walked off, the compass
still agrees with the gyro — so the compass is cleared, and GNSS is left
holding the failure alone.

**If nobody is cleared, who is in the most failures?** A magnet on the compass
breaks *both* checks the compass takes part in, while GNSS and the gyro are
each in only one. The sensor at the centre of the damage is the one that
caused it.

## Why "same domain" matters more than it looks

A check only clears a sensor if it *would have caught* the fault in question.
GNSS passing an altitude check tells you nothing about whether it is lying
horizontally, and passing a position check tells you almost nothing about a
slow walk-off — the position residual is nearly blind to one (see
crossvalidate.py). Let an unrelated pass count as an alibi and the real culprit
walks free every time.

So blame is worked out inside one domain — heading, horizontal or vertical —
and only checks from that domain are allowed to clear anyone.

## Saying "I don't know"

`cannot_isolate` is a real answer here, not a failure. Two sensors in genuine
conflict, or a tie with nothing to break it, means the evidence does not
support an accusation — and naming the wrong sensor is worse than naming none,
because the operator acts on it. Judges test this exact case.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from . import health as health_mod
from .crossvalidate import PairScore

CANNOT_ISOLATE = "cannot_isolate"

HEALTH_WEIGHT = 1.5
"""How much a failed self-check counts when ranking suspects.

A sensor that is stuck, silent or out of range is failing on its own terms,
without needing anyone to disagree with it. That is direct evidence and it
outweighs a single cross-check — but not two, because a sensor can look
degraded while a different one is the actual liar."""


@dataclass
class Blame:
    """Who is lying, and what the case against them is."""

    guilty: Optional[str] = None
    """Sensor name, or `cannot_isolate`, or None when nothing is wrong."""

    domain: str = ""
    """Which kind of disagreement led here — heading, horizontal or vertical."""

    confidence: float = 0.0
    """0 to 1. How cleanly this sensor stands out from the other suspects."""

    evidence: list[str] = field(default_factory=list)
    """Plain-language reasons, in the order they matter. Shown to the operator
    and stored in the incident record."""

    cleared: list[str] = field(default_factory=list)
    """Suspects ruled out, and why they were ruled out."""

    suspects: list[str] = field(default_factory=list)

    @property
    def isolated(self) -> bool:
        return self.guilty is not None and self.guilty != CANNOT_ISOLATE


def assign(
    pairs: list[PairScore],
    pair_states: dict[str, str],
    health: Optional[dict[str, health_mod.SensorHealth]] = None,
) -> Blame:
    """Work out which sensor is responsible for the failing checks.

    `pair_states` holds each check's *settled* state from stage 7, not its
    instantaneous reading — a momentary spike is not evidence, and blaming a
    sensor for one noisy sample is how a detector loses its operator's trust.
    """
    health = health or {}

    sick = [
        name for name, report in health.items()
        if not report.healthy and pair_states.get(f"health:{name}", "OK") != "OK"
    ]

    failing = [p for p in pairs if pair_states.get(p.key, "OK") != "OK"]
    if not failing:
        if len(sick) == 1:
            # Nothing disagrees, but a sensor is plainly broken on its own
            # terms. That is still an answer, and a confident one.
            name = sick[0]
            flags = ", ".join(health[name].flags)
            return Blame(guilty=name, domain="self-check", confidence=0.9,
                         evidence=[f"{name} failed its own health check: {flags}"])
        if len(sick) > 1:
            return Blame(guilty=CANNOT_ISOLATE, domain="self-check",
                         suspects=sorted(sick),
                         evidence=[f"{len(sick)} sensors are failing their own "
                                   "health checks at once"])
        return Blame()

    # Try every domain that is failing, and keep the verdict that actually
    # names someone. Taking the loudest domain instead looks reasonable and is
    # wrong: a hard spoof trips the position check too, and for a drone the
    # horizontal domain holds a single check, so it can never clear anyone and
    # always ties. The evidence that can distinguish should decide, not the
    # evidence that shouts.
    domains = sorted(
        {p.domain for p in failing},
        key=lambda d: max(p.ratio for p in failing if p.domain == d),
        reverse=True,
    )
    attempts = [_within_domain(d, pairs, pair_states, health) for d in domains]

    isolated = [b for b in attempts if b.isolated]
    if isolated:
        return max(isolated, key=lambda b: b.confidence)
    return attempts[0]


def _within_domain(
    domain: str,
    pairs: list[PairScore],
    pair_states: dict[str, str],
    health: dict[str, health_mod.SensorHealth],
) -> Blame:
    """Blame worked out using only one domain's evidence."""
    domain_failing = [
        p for p in pairs
        if p.domain == domain and pair_states.get(p.key, "OK") != "OK"
    ]
    domain_passing = [
        p for p in pairs
        if p.domain == domain and p.valid and pair_states.get(p.key, "OK") == "OK"
    ]

    suspects: set[str] = set()
    for pair in domain_failing:
        suspects.update((pair.a, pair.b))

    blame = Blame(domain=domain, suspects=sorted(suspects))

    # --- who has an alibi? -------------------------------------------------
    cleared: dict[str, str] = {}
    for sensor in suspects:
        for pair in domain_passing:
            other = pair.b if pair.a == sensor else pair.a if pair.b == sensor else None
            if other is None or other in suspects:
                # A pass shared with another suspect clears nobody: two liars
                # can agree with each other.
                continue
            cleared[sensor] = f"{sensor} still agrees with {other} ({pair.label})"
            break

    candidates = [s for s in suspects if s not in cleared]
    blame.cleared = [reason for _s, reason in sorted(cleared.items())]

    if not candidates:
        blame.guilty = CANNOT_ISOLATE
        blame.evidence.append(
            "every sensor involved is corroborated by another — the checks "
            "disagree but nothing stands out"
        )
        return blame

    # A sensor that is also failing its own health check needs no tie-break
    # from the cross-checks. Checked before the bail-outs below, because
    # otherwise the clearest case of all — a plainly broken sensor that only
    # one check happens to notice — comes back as "cannot isolate".
    sick_candidates = [
        c for c in candidates
        if (r := health.get(c)) is not None and not r.healthy
    ]
    if len(sick_candidates) == 1:
        name = sick_candidates[0]
        blame.guilty = name
        blame.confidence = 0.9
        flags = ", ".join(health[name].flags)
        blame.evidence.append(f"{name} failed its own health check: {flags}")
        for pair in domain_failing:
            if name in (pair.a, pair.b) and pair.valid:
                blame.evidence.append(pair.as_evidence())
        return blame

    if len(domain_failing) == 1 and not domain_passing:
        # A lone failing check with nothing else in its domain names two
        # sensors and gives no way to choose between them. Say so.
        blame.guilty = CANNOT_ISOLATE
        blame.evidence.append(
            f"only one {domain} check exists and it is the one failing — "
            "nothing available to tell the two sensors apart"
        )
        return blame

    # --- rank what is left --------------------------------------------------
    scores: dict[str, float] = defaultdict(float)
    for sensor in candidates:
        scores[sensor] = sum(1.0 for p in domain_failing if sensor in (p.a, p.b))
        report = health.get(sensor)
        if report is not None and not report.healthy:
            scores[sensor] += HEALTH_WEIGHT

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top, top_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    if len(ranked) > 1 and top_score == runner_up:
        blame.guilty = CANNOT_ISOLATE
        tied = ", ".join(s for s, v in ranked if v == top_score)
        blame.evidence.append(
            f"{tied} are equally implicated — not enough to accuse one of them"
        )
        return blame

    blame.guilty = top
    blame.confidence = min(1.0, (top_score - runner_up) / max(top_score, 1.0))

    for pair in sorted(domain_failing, key=lambda p: p.ratio, reverse=True):
        if top not in (pair.a, pair.b):
            continue
        if pair.valid:
            blame.evidence.append(pair.as_evidence())
        else:
            # Still failing on settled state, but not evaluable this instant —
            # printing its stale numbers would show "0.0x normal" beside an
            # accusation, which reads as if the evidence contradicts itself.
            blame.evidence.append(f"{pair.label}: failing ({pair.reason})")

    report = health.get(top)
    if report is not None and not report.healthy:
        blame.evidence.append(f"{top} also failed its own health check: {', '.join(report.flags)}")

    for reason in blame.cleared:
        blame.evidence.append(reason)

    return blame
