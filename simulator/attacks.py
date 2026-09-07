"""
attacks.py — GPS spoofing attack injectors for the SensorSentry simulator.

Each class mutates the *output* of sensors.py — specifically the gnss dict.
The vehicle continues flying its real route through real air; only the
number the GPS reports is a lie.

Rule: never put any truth or attack metadata into a frame.
Log to the truth-log file instead (run.py handles that).

Attack classes
--------------
WalkOff      Drag the reported position slowly along a bearing.
Teleport     Jump instantly, then hold the offset.
AltitudeOnly Change only the altitude report. Horizontal stays honest.
Replay       Position copied from a different location + stale timestamp.

Usage (in run.py / control.py)
-------------------------------
    injector = WalkOff(speed_mps=2.0, bearing_deg=90.0)
    ...
    sensor_data = sensors.update(vehicle)
    if injector and sensor_data["gnss"] is not None:
        sensor_data["gnss"] = injector.apply(sensor_data["gnss"], t_since_start)
"""

import math

# Degrees-to-metres conversion at the Coimbatore origin latitude (~11°N).
# 1 degree of latitude  ≈ 111 320 m everywhere.
# 1 degree of longitude ≈ 111 320 × cos(lat) m.
_LAT_M_PER_DEG  = 111_320.0
_LON_M_PER_DEG  = 111_320.0 * math.cos(math.radians(11.0168))

# Spoofer transmits louder than real satellites → cn0_mean rises.
# Three dB above the natural ceiling is a reliable secondary indicator.
_SPOOF_CN0_BOOST = 3.0


def _offset_latlon(lat: float, lon: float,
                   east_m: float, north_m: float):
    """Shift a lat/lon position by (east_m, north_m) metres."""
    return (
        lat  + north_m / _LAT_M_PER_DEG,
        lon  + east_m  / _LON_M_PER_DEG,
    )


def _bearing_to_en(bearing_deg: float):
    """Convert a compass bearing (N=0, E=90, clockwise) to unit (east, north)."""
    rad = math.radians(bearing_deg)
    return math.sin(rad), math.cos(rad)   # east, north


# ---------------------------------------------------------------------------
# ManualDrift — a walk-off the attacker steers while it is running
# ---------------------------------------------------------------------------
class ManualDrift:
    """A walk-off whose direction can be changed mid-attack.

    `WalkOff` commits to a bearing when it is built and computes its offset
    from the time elapsed. That makes re-aiming impossible: building a new one
    restarts its clock at zero, which snaps the fake position back onto the
    truth. On screen the spoofed track jumps home every time you turn — and it
    is wrong as well as ugly. An attacker who turns the wheel does not undo the
    distance already covered.

    So the offset lives here and only ever accumulates. Steering changes where
    the next metre goes, never where the last one went.

    This is what a judge drives with the arrow keys.
    """

    def __init__(self, speed_mps: float = 3.0, bearing_deg: float = 90.0):
        if speed_mps < 0:
            raise ValueError("speed_mps must be >= 0")
        self.speed_mps   = float(speed_mps)
        self.bearing_deg = float(bearing_deg) % 360.0
        self._east  = 0.0
        self._north = 0.0
        self._last_t: float | None = None

    def steer(self, *, speed_mps: float | None = None,
              bearing_deg: float | None = None, **_ignored) -> None:
        """Change where the drift is heading, keeping what it has already built."""
        if speed_mps is not None:
            if speed_mps < 0:
                raise ValueError("speed_mps must be >= 0")
            self.speed_mps = float(speed_mps)
        if bearing_deg is not None:
            self.bearing_deg = float(bearing_deg) % 360.0

    @property
    def offset_m(self) -> float:
        return math.hypot(self._east, self._north)

    def apply(self, gnss: dict, t_since_start: float) -> dict:
        # Integrate against elapsed time rather than counting frames, so the
        # drift is the same distance whether the loop ran fast or slow.
        dt = 0.0 if self._last_t is None else max(0.0, t_since_start - self._last_t)
        self._last_t = t_since_start

        self._east  += self.speed_mps * dt * _bearing_to_en(self.bearing_deg)[0]
        self._north += self.speed_mps * dt * _bearing_to_en(self.bearing_deg)[1]

        if self._east == 0.0 and self._north == 0.0:
            return gnss  # nothing applied yet — provably identical

        new_lat, new_lon = _offset_latlon(gnss["lat"], gnss["lon"],
                                          self._east, self._north)
        out = dict(gnss)
        out["lat"] = round(new_lat, 6)
        out["lon"] = round(new_lon, 6)
        out["cn0_mean"] = round(min(gnss["cn0_mean"] + _SPOOF_CN0_BOOST, 55.0), 1)
        return out


# ---------------------------------------------------------------------------
# WalkOff — slow drag attack
# ---------------------------------------------------------------------------
class WalkOff:
    """
    Pull the reported GPS position slowly along a bearing.

    offset = speed_mps * seconds_since_attack_started

    speed_mps == 0.0 → zero offset every frame → frame is byte-identical to
    the clean reading. We prove this live on stage; test it with an assert.

    strength range: 0.0 … 5.0 m/s (per docs/schema.md §4).
    """

    def __init__(self, speed_mps: float, bearing_deg: float):
        if speed_mps < 0:
            raise ValueError("speed_mps must be >= 0")
        self._speed   = float(speed_mps)
        self._east_u, self._north_u = _bearing_to_en(bearing_deg)

    def apply(self, gnss: dict, t_since_start: float) -> dict:
        """
        Return a mutated copy of gnss with the walk-off offset applied.
        Returns gnss unchanged if speed is 0.0 (provably clean).
        """
        if self._speed == 0.0:
            return gnss  # zero strength → nothing changes, period

        offset_m = self._speed * t_since_start
        east_m   = self._east_u  * offset_m
        north_m  = self._north_u * offset_m

        new_lat, new_lon = _offset_latlon(
            gnss["lat"], gnss["lon"], east_m, north_m
        )

        out = dict(gnss)
        out["lat"] = round(new_lat, 6)
        out["lon"] = round(new_lon, 6)
        # Boost cn0_mean: spoofing transmitter overpowers real satellites
        out["cn0_mean"] = round(
            min(gnss["cn0_mean"] + _SPOOF_CN0_BOOST, 55.0), 1
        )
        return out


# ---------------------------------------------------------------------------
# Teleport — instant position jump
# ---------------------------------------------------------------------------
class Teleport:
    """
    Jump the reported GPS position instantly by offset_m metres along bearing_deg,
    then hold that offset for the duration of the attack.
    """

    def __init__(self, offset_m: float, bearing_deg: float):
        east_u, north_u = _bearing_to_en(bearing_deg)
        self._east_m  = east_u  * float(offset_m)
        self._north_m = north_u * float(offset_m)

    def apply(self, gnss: dict, t_since_start: float) -> dict:
        new_lat, new_lon = _offset_latlon(
            gnss["lat"], gnss["lon"], self._east_m, self._north_m
        )
        out = dict(gnss)
        out["lat"] = round(new_lat, 6)
        out["lon"] = round(new_lon, 6)
        out["cn0_mean"] = round(
            min(gnss["cn0_mean"] + _SPOOF_CN0_BOOST, 55.0), 1
        )
        return out


# ---------------------------------------------------------------------------
# AltitudeOnly — vertical-only spoof
# ---------------------------------------------------------------------------
class AltitudeOnly:
    """
    Add a fixed altitude offset to the GPS report.
    Horizontal position stays honest (no lat/lon change).
    """

    def __init__(self, offset_m: float):
        self._offset_m = float(offset_m)

    def apply(self, gnss: dict, t_since_start: float) -> dict:
        out = dict(gnss)
        out["alt"] = round(gnss["alt"] + self._offset_m, 1)
        out["cn0_mean"] = round(
            min(gnss["cn0_mean"] + _SPOOF_CN0_BOOST, 55.0), 1
        )
        return out


# ---------------------------------------------------------------------------
# Replay — meaconing: position from elsewhere + clock lag
# ---------------------------------------------------------------------------
class Replay:
    """
    Meaconing attack: rebroadcast a recorded signal from a different location.

    The reported position is shifted by source_offset_m along source_bearing_deg
    (the attacker's recording location), and the timestamp in the GNSS fix is
    stale by clock_lag_s seconds.

    Note: the frame-level 't' field is the simulator clock (not GPS time) so
    the clock lag is visible through the hdop / sats values becoming stale —
    we simulate that by making hdop slowly degrade as the lag grows.
    """

    def __init__(self, source_offset_m: float, source_bearing_deg: float,
                 clock_lag_s: float):
        east_u, north_u = _bearing_to_en(source_bearing_deg)
        self._east_m    = east_u  * float(source_offset_m)
        self._north_m   = north_u * float(source_offset_m)
        self._lag_s     = float(clock_lag_s)

    def apply(self, gnss: dict, t_since_start: float) -> dict:
        new_lat, new_lon = _offset_latlon(
            gnss["lat"], gnss["lon"], self._east_m, self._north_m
        )
        out = dict(gnss)
        out["lat"] = round(new_lat, 6)
        out["lon"] = round(new_lon, 6)
        # Stale fix → hdop degrades, fewer satellites decode cleanly
        hdop_penalty = min(self._lag_s * 0.05, 2.0)
        out["hdop"] = round(min(gnss["hdop"] + hdop_penalty, 9.9), 2)
        out["sats"] = max(gnss["sats"] - int(self._lag_s // 10), 4)
        out["cn0_mean"] = round(
            min(gnss["cn0_mean"] + _SPOOF_CN0_BOOST, 55.0), 1
        )
        return out


# ---------------------------------------------------------------------------
# Registry — used by control.py to instantiate from a POST /inject body
# ---------------------------------------------------------------------------
def make_attack(kind_type: str, strength: float, bearing_deg: float,
                **kwargs):
    """
    Factory used by control.py.

    kind_type  : one of 'walkoff', 'teleport', 'altitude_only', 'replay'
    strength   : metres/s for walkoff; metres offset for others
    bearing_deg: direction of the attack
    """
    t = kind_type.lower().replace("-", "_")
    if t == "walkoff":
        return WalkOff(speed_mps=strength, bearing_deg=bearing_deg)
    elif t == "manual":
        return ManualDrift(speed_mps=strength, bearing_deg=bearing_deg)
    elif t == "teleport":
        return Teleport(offset_m=strength, bearing_deg=bearing_deg)
    elif t in ("altitude_only", "altitudeonly"):
        return AltitudeOnly(offset_m=strength)
    elif t == "replay":
        lag = kwargs.get("clock_lag_s", 30.0)
        return Replay(source_offset_m=strength,
                      source_bearing_deg=bearing_deg,
                      clock_lag_s=lag)
    else:
        raise ValueError(f"Unknown attack type: {kind_type!r}")
