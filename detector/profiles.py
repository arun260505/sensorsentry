"""Vehicle profiles: which sensors exist, and which pairs can be cross-checked.

Switching between a drone and a truck must be configuration only — no code
change anywhere in the detector. We say that on stage, so it has to be
literally true. Everything vehicle-specific lives in this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Sensor names, used as dict keys everywhere downstream.
GNSS = "gnss"
IMU = "imu"
BARO = "baro"
MAG = "mag"
ODOM = "odom"


@dataclass(frozen=True)
class Pair:
    """One cross-validation check between two sensors.

    `label` is what the operator sees in the evidence list, so it is written in
    plain words rather than field names.
    """

    a: str
    b: str
    label: str


@dataclass(frozen=True)
class Profile:
    name: str
    sensors: tuple[str, ...]
    pairs: tuple[Pair, ...]
    # Vertical channel only exists where there is a barometer.
    has_baro: bool = True
    # Accelerometer bias drives dead-reckoning growth. Truck-grade units in a
    # telematics box are typically worse than a flight controller's.
    accel_bias_sigma: float = 0.05  # m/s^2
    field_notes: str = ""


DRONE = Profile(
    name="drone",
    sensors=(GNSS, IMU, BARO, MAG),
    pairs=(
        Pair(GNSS, IMU, "GPS position vs inertial estimate"),
        Pair(GNSS, BARO, "GPS altitude vs barometric altitude"),
        Pair(GNSS, MAG, "GPS course vs compass heading"),
        Pair(MAG, IMU, "compass heading vs gyro-integrated heading"),
        Pair(BARO, IMU, "barometric climb vs vertical acceleration"),
    ),
    has_baro=True,
    accel_bias_sigma=0.05,
    field_notes="Flight controller grade IMU. No wheels, no road constraint.",
)

TRUCK = Profile(
    name="truck",
    sensors=(GNSS, IMU, MAG, ODOM),
    pairs=(
        Pair(GNSS, IMU, "GPS position vs inertial estimate"),
        Pair(GNSS, ODOM, "GPS distance vs wheel distance"),
        Pair(GNSS, MAG, "GPS course vs compass heading"),
        Pair(MAG, IMU, "compass heading vs gyro-integrated heading"),
        Pair(ODOM, IMU, "wheel speed vs inertial speed"),
    ),
    has_baro=False,
    accel_bias_sigma=0.08,
    field_notes=(
        "Telematics-box IMU, noisier than a drone's. Wheels give real ground "
        "distance and the road network constrains position — both are checks a "
        "drone does not have, which makes trucks easier to protect."
    ),
)

_BY_NAME = {p.name: p for p in (DRONE, TRUCK)}


def get(vehicle_type: str) -> Profile:
    """Look up a profile by the `vehicle_type` in the run_start header."""
    try:
        return _BY_NAME[vehicle_type]
    except KeyError:
        known = ", ".join(sorted(_BY_NAME))
        raise ValueError(
            f"unknown vehicle_type {vehicle_type!r}; known profiles: {known}"
        ) from None


def pairs_involving(profile: Profile, sensor: str) -> tuple[Pair, ...]:
    """Every check `sensor` takes part in.

    Blame assignment needs this: a sensor is only accusable if it disagrees
    across the checks it actually participates in.
    """
    return tuple(p for p in profile.pairs if sensor in (p.a, p.b))
