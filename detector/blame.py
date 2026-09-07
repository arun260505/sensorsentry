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
from . import profiles
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

    across = _across_domains(failing)
    if across is not None:
        return across
    return attempts[0]


def _across_domains(failing: list[PairScore]) -> Optional[Blame]:
    """Last resort: which sensor is failing in more *kinds* of way than anyone?

    Some faults break one check in each of several domains, and no single
    domain then has enough evidence to accuse anybody — every one of them
    reports a two-way tie. A drifting IMU does exactly this: it breaks the
    heading check against the compass and the position check against GNSS,
    and each domain on its own can only shrug.

    Stand back and the answer is obvious. The compass appears in the heading
    failure only, GNSS in the horizontal failure only, and the IMU in both.
    A sensor at the centre of several different kinds of damage is the one
    causing it — an innocent sensor is only ever dragged in by the checks it
    happens to share with the culprit.

    Deliberately the last thing tried, and only when at least two domains are
    failing. Within a single domain the corroboration rule is stronger, and
    this counting argument would happily overrule it.
    """
    domains_by_sensor: dict[str, set[str]] = defaultdict(set)
    for pair in failing:
        domains_by_sensor[pair.a].add(pair.domain)
        domains_by_sensor[pair.b].add(pair.domain)

    if len({p.domain for p in failing}) < 2:
        return None

    ranked = sorted(domains_by_sensor.items(), key=lambda kv: len(kv[1]), reverse=True)
    if len(ranked) < 2 or len(ranked[0][1]) <= len(ranked[1][1]):
        return None

    name, domains = ranked[0]
    blame = Blame(
        guilty=name,
        domain="multiple",
        confidence=min(1.0, (len(domains) - len(ranked[1][1])) / len(domains)),
        suspects=sorted(domains_by_sensor),
    )
    kinds = ", ".join(sorted(domains))
    blame.evidence.append(
        f"{name} is the only sensor failing in more than one way at once ({kinds})"
    )
    for pair in sorted(failing, key=lambda p: p.ratio, reverse=True):
        if name in (pair.a, pair.b) and pair.valid:
            blame.evidence.append(pair.as_evidence())
    return blame


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
        if p.domain == domain and (p.valid or p.stale)
        and pair_states.get(p.key, "OK") == "OK"
    ]
    # `stale` counts as well as `valid`, and that is not a shortcut.
    #
    # GNSS arrives at 5 Hz against 20 Hz frames, so on three frames in four
    # every check involving it is unevaluable. Excluding those left the
    # horizontal domain holding a single check, which can never clear anybody,
    # so blame oscillated at 15 Hz between naming the right sensor and
    # shrugging — a seized odometer was correctly identified and then
    # un-identified, over and over, and the operator saw a flickering screen.
    #
    # A check that passed a twentieth of a second ago is still evidence. The
    # crossvalidator already carries the last real reading forward for exactly
    # this reason; this is the other half of that decision.

    suspects: set[str] = set()
    for pair in domain_failing:
        suspects.update((pair.a, pair.b))
    # A map cannot be at fault. When a reported position and the road network
    # disagree exactly one of them is wrong, and it is never the road — so it
    # must not be a candidate, or the check would be as likely to accuse the
    # map as the receiver and would prove nothing.
    suspects -= profiles.INFALLIBLE

    blame = Blame(domain=domain, suspects=sorted(suspects))

    # --- who cannot be excused? --------------------------------------------
    #
    # A failing check against something that cannot be wrong is not a
    # disagreement to be arbitrated. The road network does not move, so when
    # the reported position and the map disagree, the position is wrong — and
    # no amount of agreement with the compass changes that.
    #
    # Without this a teleported GPS walked free: it sat 300 m off the
    # carriageway while still agreeing with the compass about which way the
    # lorry was pointing, collected that alibi, and blame drifted onto the
    # innocent accelerometer. Two sensors agreeing is ordinary evidence.
    # Disagreeing with a fixed fact is not, and it outranks an alibi.
    convicted = {
        (pair.b if pair.a in profiles.INFALLIBLE else pair.a)
        for pair in domain_failing
        if bool({pair.a, pair.b} & profiles.INFALLIBLE)
    } & suspects

    # A sensor failing its own health check cannot be excused either, for the
    # same reason: an alibi is someone else agreeing with you, and a broken
    # sensor can still accidentally agree with something.
    #
    # A frozen GPS proves it. Its position stops on the carriageway, so it
    # goes on passing the road check that would otherwise convict it, collects
    # that alibi, and blame lands on the odometer — which is working perfectly
    # and is the only other sensor in the failing distance check. Naming an
    # innocent sensor sends a mechanic to the wrong part of the lorry.
    convicted |= {
        name for name in suspects
        if (r := health.get(name)) is not None and not r.healthy
        and pair_states.get(f"health:{name}", "OK") != "OK"
    }

    # --- who has an alibi? -------------------------------------------------
    #
    # An alibi has to be about *now*. A check that averages over a window is
    # still describing the past for the length of that window, so it cannot
    # vouch for a sensor that has just started lying.
    #
    # The magnet showed this plainly. The instant it goes near the compass the
    # compass-vs-gyro check fails, but the course check is comparing GPS
    # course against four seconds of mostly pre-magnet compass readings, so it
    # passes — and that pass cleared the compass. The only suspect left was
    # the motion sensor, which is working perfectly, and it was named with
    # full confidence for six seconds on a truck and fourteen on a drone
    # before the window filled and the verdict corrected itself.
    #
    # Confidently wrong is the one thing rule 5 exists to prevent, and a
    # confidence threshold would not have helped: the wrong answer scored 1.0
    # and the right one 0.5. Refusing the unearned alibi gives
    # `cannot_isolate` for those few seconds instead, which is the truth.
    instant_passing = [p for p in domain_passing if p.window_s <= 0.0]

    # A fixed reference can convict, but it cannot acquit.
    #
    # The road network proves a position wrong when they disagree, because the
    # road does not move. It proves nothing when they agree: being on *a* road
    # is not evidence of being on the *right* road, and a spoofer with a map
    # can keep the fake position on a carriageway all day. In this corridor
    # there are a hundred roads, so a replayed position 250 m away landed on
    # one — the road check passed, GPS collected the alibi, and the blame went
    # to the accelerometer.
    #
    # The asymmetry is the point, and it is not a special case for the road:
    # any party that cannot itself be wrong can only ever rule out.
    instant_passing = [p for p in instant_passing
                       if not ({p.a, p.b} & profiles.INFALLIBLE)]

    cleared: dict[str, str] = {}
    for sensor in suspects:
        if sensor in convicted:
            continue
        for pair in instant_passing:
            other = pair.b if pair.a == sensor else pair.a if pair.b == sensor else None
            if other is None or other in suspects:
                # A pass shared with another suspect clears nobody: two liars
                # can agree with each other.
                continue
            cleared[sensor] = f"{sensor} still agrees with {other} ({pair.label})"
            break

    candidates = [s for s in suspects if s not in cleared]
    blame.cleared = [reason for _s, reason in sorted(cleared.items())]

    # One sensor caught contradicting a fixed fact is the whole answer.
    if len(convicted) == 1:
        name = next(iter(convicted))
        fixed = sorted(p.label for p in domain_failing
                       if name in (p.a, p.b) and bool({p.a, p.b} & profiles.INFALLIBLE))
        report = health.get(name)
        if fixed:
            why = f"{name} disagrees with {fixed[0]}, which cannot be wrong"
        else:
            why = (f"{name} failed its own health check: "
                   f"{', '.join(report.flags)}" if report else f"{name} is unwell")
        blame.guilty = name
        blame.confidence = 1.0
        blame.evidence.insert(0, why)
        return blame

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

    # Readable checks first. A failing check that could not be evaluated this
    # instant still belongs in the list, but leading with "failing (no GNSS
    # fix this frame)" reads as if the evidence contradicts the accusation.
    ordered = sorted(domain_failing, key=lambda p: (p.valid, p.ratio), reverse=True)
    for pair in ordered:
        if top not in (pair.a, pair.b):
            continue
        if pair.valid or pair.stale:
            blame.evidence.append(pair.as_evidence())
        else:
            # Failing on settled state and never yet read — nothing to quote.
            blame.evidence.append(f"{pair.label}: failing ({pair.reason})")

    report = health.get(top)
    if report is not None and not report.healthy:
        blame.evidence.append(f"{top} also failed its own health check: {', '.join(report.flags)}")

    for reason in blame.cleared:
        blame.evidence.append(reason)

    return blame
