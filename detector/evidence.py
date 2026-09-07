"""Stage 9 — the record that survives the incident.

In both stories the product is sold on, the missing piece was not detection.
The drone was gone and the flight log showed a normal flight; the truck was
unloaded and the tracking history said it was on the motorway. Catching the
attack matters, but what the fleet manager and the insurer actually need
afterwards is **proof of what happened** — and nobody has it, because the only
record was written by the sensor that was lying.

So every run is written down: the frames exactly as they arrived, and every
change of verdict. Append-only, one file per run.

## Why the raw frames, and not just a summary

A summary is our word for it. Keeping the frames means an investigator can
push them back through the detector themselves and watch it reach the same
conclusion, without trusting us — which is the difference between evidence and
an assertion. It is also how we catch ourselves: if a change to the detector
would have called an old incident differently, replaying it says so.

The frames are what the detector received, so they contain no truth and
nothing about the attack. Replaying them proves the verdict came out of the
sensor data and nothing else, which is the same argument the demo makes live.

## Why the seed is in the header

The run is reproducible from it. Given the seed, the simulator regenerates the
same flight exactly — so an incident can be re-created rather than merely
re-read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

EVIDENCE_DIR = Path("evidence")

RECORD_RUN = "run"
RECORD_FRAME = "frame"
RECORD_VERDICT = "verdict"


@dataclass
class Incident:
    """One change of verdict, as written to the record."""

    t: float
    state: str
    guilty: Optional[str]
    cause: Optional[str]
    confidence: float
    evidence: list[str]
    action: str


class Recorder:
    """Writes one run to one append-only file.

    Opened lazily: a run that never starts leaves no file behind, so the
    evidence directory holds incidents rather than clutter.
    """

    def __init__(self, directory: Path = EVIDENCE_DIR):
        self.directory = Path(directory)
        self.path: Optional[Path] = None
        self._handle = None
        self._run_id: Optional[str] = None
        self._last_state: Optional[str] = None
        self.frames_written = 0
        self.verdicts_written = 0

    # --- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _open(self, header: dict[str, Any]) -> None:
        self.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._run_id = header["run_id"]
        self.path = self.directory / f"{self._run_id}.jsonl"
        self._handle = self.path.open("w", encoding="utf-8")
        self._last_state = None
        self.frames_written = 0
        self.verdicts_written = 0
        self._write({"record": RECORD_RUN, **header})

    def _write(self, obj: dict[str, Any]) -> None:
        if self._handle is None:
            return
        self._handle.write(json.dumps(obj, separators=(",", ":")) + "\n")
        # Flushed every line. A crash mid-incident must not lose the incident,
        # and a few thousand small writes a minute costs nothing next to that.
        self._handle.flush()

    # --- recording ---------------------------------------------------------

    def note_run(self, header: dict[str, Any]) -> None:
        """Start a new file. Called on `run_start`."""
        self._open({
            "run_id": header["run_id"],
            "vehicle_id": header["vehicle_id"],
            "vehicle_type": header["vehicle_type"],
            "seed": header["seed"],
            "rate_hz": header.get("rate_hz", 20),
            "gnss_rate_hz": header.get("gnss_rate_hz", 5),
            "t0": header.get("t0", 0.0),
        })

    def note_frame(self, frame: dict[str, Any]) -> None:
        """Store a sensor frame verbatim.

        Verbatim matters: an investigator replaying an altered record proves
        nothing. This is exactly what the detector saw.
        """
        if self._handle is None:
            return
        self._write({"record": RECORD_FRAME, "f": frame})
        self.frames_written += 1

    def note_state(self, payload: dict[str, Any]) -> None:
        """Write a verdict, but only when it changes.

        A line every frame would bury the four moments that matter under
        thousands that repeat them.
        """
        if self._handle is None:
            return
        state = payload.get("state")
        blame = payload.get("blame") or {}
        cause = payload.get("cause") or {}
        signature = (state, blame.get("guilty"), cause.get("label"))
        if signature == self._last_state:
            return
        self._last_state = signature

        self._write({
            "record": RECORD_VERDICT,
            "t": payload.get("t"),
            "state": state,
            "guilty": blame.get("guilty"),
            "cause": cause.get("label"),
            "confidence": cause.get("confidence", 0.0),
            "evidence": blame.get("evidence", []),
            "action": cause.get("action", ""),
        })
        self.verdicts_written += 1


# --- reading back ----------------------------------------------------------

def read(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[Incident]]:
    """Returns (header, frames, incidents) from a record."""
    header: dict[str, Any] = {}
    frames: list[dict[str, Any]] = []
    incidents: list[Incident] = []

    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            kind = row.get("record")
            if kind == RECORD_RUN:
                header = {k: v for k, v in row.items() if k != "record"}
            elif kind == RECORD_FRAME:
                frames.append(row["f"])
            elif kind == RECORD_VERDICT:
                incidents.append(Incident(
                    t=row.get("t", 0.0), state=row.get("state", "OK"),
                    guilty=row.get("guilty"), cause=row.get("cause"),
                    confidence=float(row.get("confidence") or 0.0),
                    evidence=list(row.get("evidence") or []),
                    action=row.get("action", ""),
                ))
    return header, frames, incidents


def replay(path: Path) -> list[Incident]:
    """Push a stored run back through a fresh detector.

    Imported here rather than at module scope: evidence is written from inside
    the pipeline, and importing it back at the top would be circular.
    """
    from .pipeline import Pipeline

    header, frames, _recorded = read(path)
    pipeline = Pipeline()
    pipeline.accept({"type": "run_start", **header})

    out: list[Incident] = []
    last: Optional[tuple] = None
    for frame in frames:
        state = pipeline.accept(frame)
        if state is None:
            continue
        payload = state.to_json()
        blame = payload.get("blame") or {}
        cause = payload.get("cause") or {}
        signature = (payload["state"], blame.get("guilty"), cause.get("label"))
        if signature == last:
            continue
        last = signature
        out.append(Incident(
            t=payload["t"], state=payload["state"],
            guilty=blame.get("guilty"), cause=cause.get("label"),
            confidence=float(cause.get("confidence") or 0.0),
            evidence=list(blame.get("evidence") or []),
            action=cause.get("action", ""),
        ))
    return out


def verdicts_match(recorded: list[Incident], replayed: list[Incident]) -> bool:
    """Did the replay reach the same conclusions?

    Compared on state, sensor and cause — not on timestamps or wording. The
    claim being made is that the same data yields the same *verdict*, and
    tying that to exact phrasing would make the check fail every time an
    operator-facing sentence was reworded.
    """
    def shape(items: list[Incident]) -> list[tuple]:
        return [(i.state, i.guilty, i.cause) for i in items]

    return shape(recorded) == shape(replayed)
