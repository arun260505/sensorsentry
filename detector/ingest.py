"""Stage 1 — receive, validate, time-align.

This module is also where CLAUDE.md rule 2 stops being a promise and becomes
enforcement: a frame carrying the true position or anything about attacks is
rejected loudly, not quietly ignored. The detector must be structurally
incapable of cheating, because that is what makes the demo provable.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

DEFAULT_PORT = 5005
MAX_DATAGRAM = 8192

# Anything the simulator knows that the detector must never learn. Matching is
# on the whole key, at any nesting depth.
FORBIDDEN_KEYS = frozenset(
    {
        "true_lat", "true_lon", "true_alt", "true_heading", "true_pos",
        "truth", "ground_truth",
        "attack_active", "attack_type", "attack_strength", "attack",
        "fault_active", "fault_type", "fault",
        "is_spoofed", "spoofed", "scenario",
    }
)

TOP_LEVEL_KEYS = frozenset({"vehicle_id", "t", "seq", "gnss", "imu", "baro", "mag", "odom"})
HEADER_KEYS = frozenset(
    {"type", "run_id", "vehicle_id", "vehicle_type", "seed", "rate_hz", "gnss_rate_hz", "t0"}
)


class SchemaViolation(Exception):
    """A message did not match docs/schema.md.

    Raised rather than logged. A malformed frame during development is a bug to
    fix now; a forbidden field is a correctness failure that invalidates the
    whole demo, and neither should be survivable.
    """


@dataclass(frozen=True)
class RunHeader:
    run_id: str
    vehicle_id: str
    vehicle_type: str
    seed: int
    rate_hz: float
    gnss_rate_hz: float
    t0: float


@dataclass
class Frame:
    """One validated sensor frame, with the timing context around it."""

    vehicle_id: str
    t: float
    seq: int
    imu: dict[str, float]
    baro: Optional[dict[str, float]]
    mag: Optional[dict[str, float]]
    gnss: Optional[dict[str, Any]]
    odom: Optional[dict[str, float]]

    dt: float = 0.0
    """Seconds since the previous frame. Zero on the first frame."""

    dropped: int = 0
    """Frames missing between this one and the last, from the `seq` gap."""

    def has_gnss(self) -> bool:
        """GNSS runs at 5 Hz against 20 Hz frames, so this is False three
        times out of four. Callers must handle the gap rather than reusing the
        previous fix — that gap is real and it matters to detection."""
        return self.gnss is not None


def _scan_forbidden(obj: Any, path: str = "") -> None:
    """Walk a decoded message looking for keys the detector may not receive."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}" if path else key
            if key in FORBIDDEN_KEYS:
                raise SchemaViolation(
                    f"forbidden field {here!r} in incoming message. "
                    "The detector must never receive truth or attack state "
                    "(CLAUDE.md rule 2). Log it on the simulator side instead."
                )
            _scan_forbidden(value, here)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _scan_forbidden(value, f"{path}[{i}]")


def validate_frame(raw: dict[str, Any]) -> None:
    """Check one decoded sensor frame against docs/schema.md."""
    _scan_forbidden(raw)

    unknown = set(raw) - TOP_LEVEL_KEYS
    if unknown:
        raise SchemaViolation(
            f"unknown top-level field(s) {sorted(unknown)}. The contract is "
            "frozen — see docs/schema.md and raise it with the team."
        )

    for required in ("vehicle_id", "t", "seq", "imu"):
        if raw.get(required) is None:
            raise SchemaViolation(f"missing required field {required!r}")

    for axis in ("ax", "ay", "az", "gx", "gy", "gz"):
        if axis not in raw["imu"]:
            raise SchemaViolation(f"imu is missing {axis!r}")

    gnss = raw.get("gnss")
    if gnss is not None:
        for required in ("lat", "lon", "alt"):
            if required not in gnss:
                raise SchemaViolation(f"gnss is missing {required!r}")


def validate_header(raw: dict[str, Any]) -> None:
    _scan_forbidden(raw)
    unknown = set(raw) - HEADER_KEYS
    if unknown:
        raise SchemaViolation(f"unknown field(s) in run_start: {sorted(unknown)}")
    for required in ("run_id", "vehicle_id", "vehicle_type", "seed"):
        if raw.get(required) is None:
            raise SchemaViolation(f"run_start is missing {required!r}")


class FrameStream:
    """Turns decoded messages into an ordered run of validated frames.

    Holds only what timing needs: the last timestamp and sequence number. It
    does not buffer history — modules that need a window keep their own, so
    each one owns exactly the memory it uses.
    """

    def __init__(self) -> None:
        self.header: Optional[RunHeader] = None
        self.last_t: Optional[float] = None
        self.last_seq: Optional[int] = None
        self.frames_seen = 0
        self.frames_dropped = 0

    def reset(self) -> None:
        self.__init__()

    def accept(self, raw: dict[str, Any]) -> Optional[Frame]:
        """Feed one decoded message. Returns a Frame, or None for a header."""
        if raw.get("type") == "run_start":
            validate_header(raw)
            self.last_t = None
            self.last_seq = None
            self.frames_seen = 0
            self.frames_dropped = 0
            self.header = RunHeader(
                run_id=raw["run_id"],
                vehicle_id=raw["vehicle_id"],
                vehicle_type=raw["vehicle_type"],
                seed=int(raw["seed"]),
                rate_hz=float(raw.get("rate_hz", 20.0)),
                gnss_rate_hz=float(raw.get("gnss_rate_hz", 5.0)),
                t0=float(raw.get("t0", 0.0)),
            )
            return None

        validate_frame(raw)

        t = float(raw["t"])
        seq = int(raw["seq"])

        dt = 0.0 if self.last_t is None else t - self.last_t
        if dt < 0.0:
            # Out-of-order datagram. UDP does not promise ordering, and a stale
            # frame would rewind dead reckoning, so drop it.
            return None

        dropped = 0
        if self.last_seq is not None and seq > self.last_seq + 1:
            dropped = seq - self.last_seq - 1
            self.frames_dropped += dropped

        self.last_t = t
        self.last_seq = seq
        self.frames_seen += 1

        return Frame(
            vehicle_id=raw["vehicle_id"],
            t=t,
            seq=seq,
            imu=raw["imu"],
            baro=raw.get("baro"),
            mag=raw.get("mag"),
            gnss=raw.get("gnss"),
            odom=raw.get("odom"),
            dt=dt,
            dropped=dropped,
        )


class UdpReceiver:
    """Blocking UDP socket that yields decoded messages."""

    def __init__(self, port: int = DEFAULT_PORT, host: str = "0.0.0.0", timeout: float = 1.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.settimeout(timeout)
        self.port = port

    def messages(self) -> Iterator[dict[str, Any]]:
        """Yield messages until interrupted. Skips socket timeouts so a caller
        can keep a UI responsive between runs."""
        while True:
            try:
                data, _addr = self.sock.recvfrom(MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                yield json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SchemaViolation(f"undecodable datagram: {exc}") from exc

    def close(self) -> None:
        self.sock.close()
