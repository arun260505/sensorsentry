"""The written incident report — the one job an AI model is allowed here.

Everything else in this project is physics and arithmetic, and a language
model would be the wrong instrument for it: detection has to run on the
vehicle at sensor rate, offline, and give the same answer every time an
investigator replays it. None of that describes a network call.

Turning a finished incident into paragraphs an insurer will read is a
different kind of job, and a genuine one. It happens **after** the event, from
the stored structured record, and it changes nothing about what was detected.

## The switch, and why it is the point

`enabled=False` and the detector behaves identically — because this module is
never on the detection path at all. It reads a file that has already been
written. That is the demonstration: turn it off on stage, re-run the attack,
watch the same detection happen, and the only thing missing is the prose.

## What is actually generating this text today

A template, filled from the record. Not a language model — there is no model
in this repository and none is called.

That is deliberate rather than a shortcut: the interesting claim is the
*architecture*, that the writing layer is optional and post-hoc, and a
template proves that as well as a model would while keeping the demo offline
and identical every run. `compose()` is where a model would be handed the same
structured record, and its output would replace the same paragraphs.

Say this plainly if asked. Claiming a template is an AI is the sort of thing
that unravels badly under one follow-up question.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .evidence import Incident, read

_SENSOR = {"gnss": "the GPS receiver", "imu": "the motion sensor",
           "baro": "the altitude sensor", "mag": "the compass",
           "odom": "the wheel sensor"}

_CAUSE_SENTENCE = {
    "attack": ("This was a deliberate attack. The error was smooth and pointed "
               "consistently one way, and the sensor concerned is told its "
               "answer by radio — so a consistent error means somebody nearby "
               "was transmitting."),
    "fault": ("This was a hardware failure, not an attack. The sensor was "
              "erratic or failed its own health checks, which is how equipment "
              "fails and not how an attacker behaves."),
    "interference": ("Something close to the vehicle was disturbing the sensor. "
                     "It measures a physical field around itself, and that field "
                     "was changed — a magnet or similar, at close range."),
    "unclassified": ("The cause could not be established with confidence. The "
                     "system treated it as an attack for safety and said so."),
}

_NEXT_STEPS = {
    "attack": ["Report the location and time to the authorities.",
               "Check whether other vehicles were in the same area.",
               "Retain this record — it is the evidence for any claim."],
    "fault": ["Take the vehicle out of service and inspect the sensor.",
              "No security response is needed."],
    "interference": ["Inspect the vehicle for anything attached near the sensor.",
                     "Check the area for equipment that could disturb it."],
    "unclassified": ["Inspect the sensor, and treat the area as suspect until "
                     "ruled out."],
}


@dataclass
class Report:
    title: str
    body: str
    generated_by: str = "template"
    """What wrote this. Named honestly so nobody has to guess later."""


def compose(path: Path, *, enabled: bool = True) -> Optional[Report]:
    """Write up a stored incident. Returns None when switched off.

    A language model would be handed exactly this record and would replace the
    paragraphs below. It is not called here; nothing in this repository calls
    one.
    """
    if not enabled:
        return None

    header, frames, incidents = read(path)
    alerts = [i for i in incidents if i.state == "ALERT" and i.guilty]
    vehicle = header.get("vehicle_id", "unknown vehicle")

    if not alerts:
        return Report(
            title=f"{vehicle} — no incident",
            body=(f"Vehicle {vehicle} completed a run of {len(frames)} sensor "
                  f"frames with no sensor fault or attack detected. All "
                  f"cross-checks stayed within their normal bands throughout."),
        )

    first = alerts[0]
    guilty = _SENSOR.get(first.guilty or "", first.guilty or "a sensor")
    cause = first.cause or "unclassified"
    # To the end of the record, not to the last change of verdict. The alert
    # is usually the last thing that changes, so measuring between verdicts
    # reported every incident as lasting zero seconds.
    ended = frames[-1].get("t", first.t) if frames else first.t
    recovered = [i for i in incidents if i.t > first.t and i.state == "OK"]
    duration = (recovered[0].t if recovered else ended) - first.t

    lines = [
        f"At {first.t:.0f} seconds into the run, vehicle {vehicle} detected that "
        f"{guilty} could no longer be trusted.",
        "",
        _CAUSE_SENTENCE.get(cause, _CAUSE_SENTENCE["unclassified"]),
        "",
        "The finding rested on the vehicle's other sensors disagreeing with it:",
    ]
    lines += [f"  - {line}" for line in first.evidence] or ["  - (no detail recorded)"]
    lines += [
        "",
        f"The vehicle stopped using {guilty} and continued on its remaining "
        f"sensors. {first.action}",
        "",
        f"The condition lasted about {duration:.0f} seconds"
        + ("" if recovered else " and had not cleared when the record ended")
        + ". The full sensor "
        f"record for this run is stored and can be replayed through the "
        f"detector to reproduce this conclusion independently.",
        "",
        "Suggested next steps:",
    ]
    lines += [f"  {n}. {step}" for n, step in enumerate(_NEXT_STEPS.get(cause, []), 1)]

    return Report(
        title=f"{vehicle} — {cause} affecting {guilty}",
        body="\n".join(lines),
    )
