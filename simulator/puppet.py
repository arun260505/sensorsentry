"""Sensors the operator drives instead of the vehicle.

An injector *offsets* a sensor: the reading is still fundamentally the
vehicle's, nudged. A puppet *replaces* it. The vehicle carries on driving its
real route and the sensor reports whatever the person holding the arrow keys
says it reports.

That difference is what makes the demo un-fakeable. A judge holding the
controller does not have to be persuaded the run is live.

## The two cases, from one control

The problem statement names three failures and expects most teams to cover
one. A judge produces two of them here with the same four keys, and the only
difference is how they press them:

- **Drive it** while the vehicle goes straight, and the position is moving in
  a way the vehicle never moved. Something is inventing it → an attack.
- **Let go**, and the position stops dead while the wheels still turn and the
  accelerometer still feels the road. Nothing is inventing it; the sensor has
  simply stopped → a fault.

Neither of those is a special case anywhere in the detector. They are ordinary
bad readings, caught by the checks that were already there.

## Frozen has to mean frozen

`GnssPuppet` returns byte-identical coordinates when it is not being driven.
Not nearly identical — identical. `health.py` calls GNSS stuck when the
position does not move *at all* across five fixes, on the grounds that a real
receiver dithers in the last decimal even sitting still. That check is the
entire fault case, so a puppet that quietly wobbled would break it while
looking perfectly correct on screen.

Nothing here is ever told to the detector. It receives ordinary frames on the
ordinary socket, and there is no field in the contract that could carry the
fact that a person is driving.
"""

from __future__ import annotations

import math

_LAT_M_PER_DEG = 111_320.0
_LON_M_PER_DEG = 108_000.0   # near Chennai's latitude; matches attacks.py


class _Puppet:
    """Shared timing. Puppets integrate against sim time, not frame count, so
    a slow loop does not change how far anything travels."""

    sensor = ""

    def __init__(self) -> None:
        self._taken = False
        self._last_t: float | None = None

    def _dt(self, t_since_start: float) -> float:
        dt = 0.0 if self._last_t is None else max(0.0, t_since_start - self._last_t)
        self._last_t = t_since_start
        return dt

    def steer(self, **_kwargs) -> None:      # overridden
        raise NotImplementedError

    def readout(self) -> dict:
        """What the console shows on the D-pad."""
        return {}


class GnssPuppet(_Puppet):
    """The judge is the GPS receiver.

    Holds its own position. Driven, it walks that position along a bearing at
    the chosen speed. Undriven, it reports exactly the same coordinates every
    frame while the vehicle drives away underneath it.
    """

    sensor = "gnss"

    def __init__(self, speed_mps: float = 6.0, bearing_deg: float = 0.0):
        super().__init__()
        self.speed_mps   = float(speed_mps)
        self.bearing_deg = float(bearing_deg) % 360.0
        self.moving      = False
        self._lat: float | None = None
        self._lon: float | None = None

    def steer(self, *, speed_mps=None, bearing_deg=None, moving=None, **_ignored):
        if speed_mps is not None:
            self.speed_mps = max(0.0, float(speed_mps))
        if bearing_deg is not None:
            self.bearing_deg = float(bearing_deg) % 360.0
            self.moving = True          # pressing a direction is how you drive
        if moving is not None:
            self.moving = bool(moving)

    def readout(self) -> dict:
        return {"bearing_deg": self.bearing_deg, "speed_mps": self.speed_mps,
                "moving": self.moving}

    def apply(self, gnss: dict, t_since_start: float) -> dict:
        dt = self._dt(t_since_start)
        if gnss is None:
            return None

        if self._lat is None:           # take over from where it really was,
            self._lat = float(gnss["lat"])   # so there is no give-away jump
            self._lon = float(gnss["lon"])

        if self.moving and self.speed_mps > 0.0:
            rad = math.radians(self.bearing_deg)
            self._lat += (self.speed_mps * dt * math.cos(rad)) / _LAT_M_PER_DEG
            self._lon += (self.speed_mps * dt * math.sin(rad)) / _LON_M_PER_DEG

        out = dict(gnss)
        # Rounded to a fixed 6 dp so that a stationary puppet reports the
        # SAME digits every frame. health.py's stuck check needs exactly that,
        # and it is what turns "the judge let go" into "the sensor failed".
        out["lat"] = round(self._lat, 6)
        out["lon"] = round(self._lon, 6)
        return out


class MagPuppet(_Puppet):
    """The judge is the compass."""

    sensor = "mag"

    def __init__(self, heading_deg: float | None = None):
        super().__init__()
        self._heading = None if heading_deg is None else float(heading_deg) % 360.0

    def steer(self, *, turn_deg=None, heading_deg=None, **_ignored):
        if heading_deg is not None:
            self._heading = float(heading_deg) % 360.0
        if turn_deg is not None and self._heading is not None:
            self._heading = (self._heading + float(turn_deg)) % 360.0

    def readout(self) -> dict:
        return {"heading_deg": None if self._heading is None else round(self._heading, 1)}

    def apply(self, sensor_data: dict, t_since_start: float) -> dict:
        self._dt(t_since_start)
        out = dict(sensor_data)
        if out.get("mag") is None:
            return out
        mag = dict(out["mag"])
        if self._heading is None:
            self._heading = float(mag["heading_deg"]) % 360.0
        mag["heading_deg"] = round(self._heading, 2)
        # The gyro is deliberately untouched: compass says we turned, gyro says
        # we did not, and that disagreement is the whole detection.
        out["mag"] = mag
        return out


class BaroPuppet(_Puppet):
    """The judge is the barometer. Driven in metres of apparent height;
    ~1 hPa is about 8.5 m, and nobody thinks in hectopascals."""

    sensor = "baro"
    _HPA_PER_M = 1.0 / 8.5

    def __init__(self, height_offset_m: float = 0.0):
        super().__init__()
        self.height_offset_m = float(height_offset_m)
        self._base: float | None = None

    def steer(self, *, height_offset_m=None, step_m=None, **_ignored):
        if height_offset_m is not None:
            self.height_offset_m = float(height_offset_m)
        if step_m is not None:
            self.height_offset_m += float(step_m)

    def readout(self) -> dict:
        return {"height_offset_m": round(self.height_offset_m, 1)}

    def apply(self, sensor_data: dict, t_since_start: float) -> dict:
        self._dt(t_since_start)
        out = dict(sensor_data)
        if out.get("baro") is None:
            return out
        baro = dict(out["baro"])
        if self._base is None:
            self._base = float(baro["pressure_hpa"])
        # Higher apparent altitude means lower pressure, hence the minus.
        baro["pressure_hpa"] = round(
            float(baro["pressure_hpa"]) - self.height_offset_m * self._HPA_PER_M, 3)
        out["baro"] = baro
        return out


class OdomPuppet(_Puppet):
    """The judge is the wheel sensor. Holding it at zero while the truck
    drives is how a seized odometer reads."""

    sensor = "odom"

    def __init__(self, speed_mps: float | None = None):
        super().__init__()
        self.speed_mps = speed_mps if speed_mps is None else float(speed_mps)

    def steer(self, *, speed_mps=None, step_mps=None, **_ignored):
        if speed_mps is not None:
            self.speed_mps = max(0.0, float(speed_mps))
        if step_mps is not None and self.speed_mps is not None:
            self.speed_mps = max(0.0, self.speed_mps + float(step_mps))

    def readout(self) -> dict:
        return {"speed_mps": None if self.speed_mps is None else round(self.speed_mps, 1)}

    def apply(self, sensor_data: dict, t_since_start: float) -> dict:
        self._dt(t_since_start)
        out = dict(sensor_data)
        if out.get("odom") is None:
            return out
        odom = dict(out["odom"])
        if self.speed_mps is None:
            self.speed_mps = float(odom.get("wheel_speed_mps") or 0.0)
        odom["wheel_speed_mps"] = round(self.speed_mps, 3)
        out["odom"] = odom
        return out


_PUPPETS = {
    "gnss": GnssPuppet,
    "mag":  MagPuppet,
    "baro": BaroPuppet,
    "odom": OdomPuppet,
}


def make_puppet(sensor: str):
    """Factory used by control.py."""
    key = (sensor or "").lower()
    if key not in _PUPPETS:
        raise ValueError(
            f"cannot take over {sensor!r} — try one of {sorted(_PUPPETS)}")
    return _PUPPETS[key]()
