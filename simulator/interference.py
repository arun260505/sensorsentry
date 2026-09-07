"""
interference.py — Physical interference injectors for the SensorSentry simulator.

These model real-world environmental effects that corrupt a sensor, as
distinct from active attacks (GPS spoofing) or hardware faults.

Classes
-------
Magnet    A permanent magnet held near the compass deflects the reading.
Pressure  Localised air pressure (e.g. hot exhaust nearby) biases the baro.

Critical rule for Magnet
------------------------
Only `mag["heading_deg"]` changes. `imu["gz"]` (the gyro yaw rate) must
stay untouched. The detection signature is exactly this discrepancy:
  - compass says we turned X degrees
  - gyro says we didn't
If you also adjust gz, the evidence disappears and the case stops working.
"""

import math
import copy


# ---------------------------------------------------------------------------
# Magnet — compass deflection
# ---------------------------------------------------------------------------
class Magnet:
    """
    A strong permanent magnet held near the compass housing.

    Adds a fixed angular offset to `mag["heading_deg"]`.
    The IMU gyro (gz) is NOT touched — that independence is the detection key.

    offset_deg : signed deflection in degrees (positive = clockwise shift)
    """

    def __init__(self, offset_deg: float):
        self._offset = float(offset_deg)

    def steer(self, *, offset: float | None = None, **_ignored) -> None:
        """Turn the magnet while it is being held there.

        A magnet is a constant offset rather than something that accumulates,
        so unlike a walk-off it can simply be set — but it goes through the
        same steering call as everything else, because on the console it is
        the same pair of arrow keys.
        """
        if offset is not None:
            self._offset = float(offset)

    @property
    def offset(self) -> float:
        return self._offset

    def apply(self, sensor_data: dict, t_since_start: float) -> dict:
        out = dict(sensor_data)
        if out.get("mag") is None:
            return out
        mag = dict(out["mag"])
        # Shift heading, keep it in [0, 360)
        mag["heading_deg"] = round(
            (mag["heading_deg"] + self._offset) % 360.0, 2
        )
        out["mag"] = mag
        # imu["gz"] deliberately untouched — compass turned, gyro didn't
        return out


# ---------------------------------------------------------------------------
# Pressure — barometer bias
# ---------------------------------------------------------------------------
class Pressure:
    """
    Localised high- or low-pressure source near the barometer port
    (e.g. engine exhaust, AC vent, altitude chamber).

    Adds a fixed offset in hPa to `baro["pressure_hpa"]`.
    Positive offset → sensor reads higher pressure → thinks it's lower.

    offset_hpa : signed offset in hectopascals
                 (~1 hPa ≈ 8.5 m of apparent altitude error)
    """

    def __init__(self, offset_hpa: float):
        self._offset = float(offset_hpa)

    def steer(self, *, offset: float | None = None, **_ignored) -> None:
        """Raise or lower the apparent altitude while the run continues."""
        if offset is not None:
            self._offset = float(offset)

    @property
    def offset(self) -> float:
        return self._offset

    def apply(self, sensor_data: dict, t_since_start: float) -> dict:
        out = dict(sensor_data)
        if out.get("baro") is None:
            return out
        baro = dict(out["baro"])
        baro["pressure_hpa"] = round(
            baro["pressure_hpa"] + self._offset, 3
        )
        out["baro"] = baro
        return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def make_interference(interference_type: str, strength: float, **kwargs):
    """
    Factory used by control.py.

    interference_type : 'magnet' or 'pressure'
    strength          : degrees for magnet; hPa for pressure
    """
    t = interference_type.lower()
    if t == "magnet":
        return Magnet(offset_deg=strength)
    elif t == "pressure":
        return Pressure(offset_hpa=strength)
    else:
        raise ValueError(f"Unknown interference type: {interference_type!r}")
