"""The optional language model behind the report switch.

This is the one place in the project where a model is allowed, and the rules
around it are the point rather than a formality.

## What it may touch

A stored incident record, after the event, to turn structured facts into
paragraphs. That is it. It is never on the detection path — nothing here is
imported by `pipeline.py` or by any stage — and it cannot change a verdict,
because by the time it runs the verdict has already been written to disk.

Turn it off on stage, re-run the attack, and the detection is identical. Not
"close enough": identical, because the code that decided never called this.
`tests/run_all.py` asserts it.

## Why raw HTTP rather than the SDK

CLAUDE.md rule 6: NumPy only. A demo that needs `pip install anthropic` on the
morning is a demo with a new way to fail, and the whole argument for this
system is that it runs offline on a laptop. The Messages API is one POST; the
standard library can make one POST.

## What happens with no key, or no wifi

It falls back to the template and says so. Every path here returns something
useful, because the alternative is a blank panel in front of judges when the
venue's network eats the request.

The report carries `generated_by`, and the console shows it. A template that
claims to be a model is the sort of thing that unravels under one follow-up
question.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

ENDPOINT = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-5"
API_VERSION = "2023-06-01"

TIMEOUT_S = 20.0
"""Short on purpose. This runs while somebody is standing in front of an
audience; a minute of silence is worse than a template."""

MAX_TOKENS = 900

SYSTEM = """You write incident reports for a fleet operator whose vehicles \
carry a sensor-integrity monitor.

You are given the monitor's own structured findings. Turn them into a short \
report for a non-technical reader — an insurer, a fleet manager, a police \
officer.

Rules, and they matter more than style:

- Report only what the findings state. If the record says the guilty sensor \
could not be isolated, say that plainly; do not pick one.
- Do not invent times, places, part numbers, causes or consequences.
- No hedging language about your own confidence. The monitor's confidence is \
in the record; yours is not relevant.
- Three short paragraphs at most: what happened, what the evidence was, what \
should be done now.
- Plain English. No jargon that is not in the findings."""


def available() -> bool:
    """Is a key configured? Does not touch the network."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def narrate(findings: dict, *, timeout_s: float = TIMEOUT_S) -> Optional[str]:
    """Ask the model to write up one incident. None if it cannot.

    Returns None rather than raising for every failure — no key, no network,
    a rate limit, a refusal, a malformed reply. The caller falls back to the
    template, which is always available, and the reader is told which one they
    are looking at.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None

    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        # A short factual write-up from a record: low effort is the right
        # setting and costs a fraction of the default.
        "output_config": {"effort": "low"},
        "messages": [{
            "role": "user",
            "content": ("Write the report from these findings.\n\n"
                        + json.dumps(findings, indent=2, sort_keys=True)),
        }],
    }

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": API_VERSION,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None

    # A safety decline is a 200 with stop_reason "refusal" and no usable text,
    # so the status code alone does not tell you whether there is a report.
    if body.get("stop_reason") == "refusal":
        return None

    text = "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if block.get("type") == "text"
    ).strip()
    return text or None


def findings_from(incident, header: dict, frames: int,
                  duration_s: float) -> dict:
    """The record, as the model receives it.

    Deliberately narrow. It gets the monitor's conclusions and nothing else —
    no raw frames, no true position, nothing it could use to form a view of
    its own about what happened. Its job is to write down a finding, not to
    reach one.
    """
    return {
        "vehicle": header.get("vehicle_id", "unknown"),
        "vehicle_type": header.get("vehicle_type", "unknown"),
        "run_id": header.get("run_id", "unknown"),
        "frames_recorded": frames,
        "detected_at_s": round(float(incident.t), 1),
        "lasted_s": round(float(duration_s), 1),
        "guilty_sensor": incident.guilty,
        "cause": incident.cause,
        "confidence": getattr(incident, "confidence", None),
        "evidence": list(getattr(incident, "evidence", []) or []),
    }
