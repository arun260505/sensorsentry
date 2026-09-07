"""
faults.py — Sensor fault injectors for the SensorSentry simulator.

Each class wraps the raw sensor dict from sensors.py and corrupts one
sensor's output in a way that mimics a real hardware failure.

Design principle: faults must look DIFFERENT from attacks.
  - Attacks: smooth, directional, one-way drift
  - Faults:  random, jumpy, noisy, or simply dead

The detector (blame.py / classify.py) uses this difference to tell them
apart. If a fault looks tidy and consistent, it'll be misclassified.

Supported sensors: 'gnss', 'imu', 'baro', 'mag', 'odom'

Classes
-------
Stuck    Freeze on the last-seen value, with occasional random jumps.
Noisy    Multiply noise by a factor; output is wild and unpredictable.
Dropout  Return null / zeroed data — sensor has no signal.
Bias     Add a slowly growing ramp to the reading.

Usage (in run.py / control.py)
-------------------------------
    fault = Stuck(sensor='gnss')
    ...
    sensor_data = sensors.update(vehicle)
    sensor_data = fault.apply(sensor_data, t_since_start, rng)
"""

import math
import copy
import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sensor_present(sensor_data: dict, sensor: str) -> bool:
    """Return True if the sensor key exists and is not None."""
    return sensor_data.get(sensor) is not None


def _all_numeric_keys(d: dict):
    """Return all keys in d whose value is numeric."""
    return [k for k, v in d.items() if isinstance(v, (int, float))]


# ---------------------------------------------------------------------------
# Stuck — freeze on last value
# ---------------------------------------------------------------------------
class Stuck:
    """
    Freeze the sensor on its last valid reading.

    Real broken sensors rarely produce a perfectly constant value; they
    tend to flicker randomly around the stuck point. We add occasional
    large random spikes to make the fault look messy and clearly broken
    (not like a smooth attack offset).

    spike_prob  : probability per frame of a random spike
    spike_scale : magnitude of spikes as a multiple of the field value
    """

    def __init__(self, sensor: str, spike_prob: float = 0.08,
                 spike_scale: float = 0.15):
        self._sensor      = sensor
        self._last        = None      # last-seen reading snapshot
        self._spike_prob  = spike_prob
        self._spike_scale = spike_scale

    def apply(self, sensor_data: dict, t_since_start: float,
              rng: np.random.Generator) -> dict:
        out = dict(sensor_data)
        s = self._sensor

        if not _sensor_present(sensor_data, s):
            return out   # sensor already null this frame, nothing to freeze

        reading = sensor_data[s]

        # Capture first valid reading
        if self._last is None:
            self._last = copy.deepcopy(reading)

        # Decide whether to emit a spike this frame
        if rng.random() < self._spike_prob:
            # Spike: perturb each numeric field randomly
            spiked = copy.deepcopy(self._last)
            for k in _all_numeric_keys(spiked):
                base = abs(spiked[k]) if spiked[k] != 0 else 1.0
                spiked[k] = round(
                    spiked[k] + rng.normal(0, self._spike_scale * base), 4
                )
            out[s] = spiked
        else:
            out[s] = copy.deepcopy(self._last)

        return out


# ---------------------------------------------------------------------------
# Noisy — multiply noise aggressively
# ---------------------------------------------------------------------------
class Noisy:
    """
    Multiply the noise on every numeric field of the sensor by `multiplier`.

    A multiplier of 10 makes the sensor wildly erratic — every reading
    differs from the last by 10x the normal sigma. Clearly broken, clearly
    different from a smooth GPS walk-off.
    """

    def __init__(self, sensor: str, multiplier: float = 10.0):
        self._sensor     = sensor
        self._multiplier = float(multiplier)

    # Typical 1-sigma noise per field, used to scale injected noise.
    # If a field isn't listed, we use 1% of its absolute value as a fallback.
    _FIELD_SIGMA = {
        # GNSS
        "lat": 1.5 / 111_320,
        "lon": 1.5 / 111_320,
        "alt": 3.0,
        "hdop": 0.05,
        "cn0_mean": 0.5,
        # IMU
        "ax": 0.02, "ay": 0.02, "az": 0.02,
        "gx": 0.002, "gy": 0.002, "gz": 0.002,
        # Baro
        "pressure_hpa": 0.08,
        # Mag
        "heading_deg": 1.5,
        # Odom
        "wheel_speed_mps": 0.05,
    }

    def apply(self, sensor_data: dict, t_since_start: float,
              rng: np.random.Generator) -> dict:
        out = dict(sensor_data)
        s = self._sensor

        if not _sensor_present(sensor_data, s):
            return out

        reading = copy.deepcopy(sensor_data[s])
        for k in _all_numeric_keys(reading):
            sigma = self._FIELD_SIGMA.get(k, abs(reading[k]) * 0.01 + 1e-6)
            reading[k] = round(
                reading[k] + rng.normal(0, sigma * self._multiplier), 4
            )
        out[s] = reading
        return out


# ---------------------------------------------------------------------------
# Dropout — sensor goes dark
# ---------------------------------------------------------------------------
class Dropout:
    """
    The sensor stops producing data — returns null.

    For GNSS this is natural (we already null it 3/4 of the time), but a
    permanent null is a clear fault. For IMU/baro/mag we null the entire
    dict, which the detector will catch as a health failure.
    """

    def __init__(self, sensor: str):
        self._sensor = sensor

    def apply(self, sensor_data: dict, t_since_start: float,
              rng: np.random.Generator) -> dict:
        out = dict(sensor_data)
        out[self._sensor] = None
        return out


# ---------------------------------------------------------------------------
# Bias — slowly growing ramp on ONE axis
# ---------------------------------------------------------------------------
class Bias:
    """
    Add a slowly growing ramp to one axis of the sensor.

    ramp(t) = rate_per_s * t_since_start, applied to `axis` only.

    The Task 2 lesson that this fixes: ramping every numeric field at once is
    not how a real sensor fails. A bias hits one axis — the x accelerometer,
    the yaw gyro, the barometer's pressure reading — and the other outputs
    keep behaving. Ramping all six IMU axes together made the fault look like
    six independent failures and was much harder to diagnose than it should
    have been.

    `axis` is a field name inside the sensor's dict (e.g. 'ax' or 'gz'). It
    defaults to 'ax', the forward accelerometer, which is the classic
    accelerometer-bias failure.

    A bias fault is subtler than Noisy or Stuck — it takes time to show up
    and initially looks like a sensor that's just a bit off. The detector
    should catch it through cross-validation rather than health checks alone.
    """

    def __init__(self, sensor: str, rate_per_s: float, axis: str = "ax"):
        self._sensor      = sensor
        self._rate_per_s  = float(rate_per_s)
        self._axis        = axis

    def apply(self, sensor_data: dict, t_since_start: float,
              rng: np.random.Generator) -> dict:
        out = dict(sensor_data)
        s = self._sensor

        if not _sensor_present(sensor_data, s):
            return out

        ramp = self._rate_per_s * t_since_start
        reading = copy.deepcopy(sensor_data[s])
        if self._axis in reading and isinstance(reading[self._axis], (int, float)):
            reading[self._axis] = round(reading[self._axis] + ramp, 4)
        out[s] = reading
        return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def make_fault(fault_type: str, sensor: str, strength: float = 1.0,
               **kwargs):
    """
    Factory used by control.py.

    fault_type : 'stuck', 'noisy', 'dropout', 'bias'
    sensor     : 'gnss', 'imu', 'baro', 'mag', 'odom'
    strength   : multiplier for Noisy; rate_per_s for Bias; ignored for others
    axis       : single field for Bias (e.g. 'ax'); optional
    """
    t = fault_type.lower()
    if t == "stuck":
        return Stuck(sensor=sensor)
    elif t == "noisy":
        return Noisy(sensor=sensor, multiplier=max(strength, 2.0))
    elif t == "dropout":
        return Dropout(sensor=sensor)
    elif t == "bias":
        return Bias(sensor=sensor, rate_per_s=strength, axis=kwargs.get("axis", "ax"))
    else:
        raise ValueError(f"Unknown fault type: {fault_type!r}")
