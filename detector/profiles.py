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
ROAD = "road"
"""The road network. Not a sensor — a prior about where a truck can possibly
be, and treated as a party to a check so that disagreeing with it accuses the
other side."""


@dataclass(frozen=True)
class Pair:
    """One cross-validation check between two sensors.

    `label` is what the operator sees in the evidence list, so it is written in
    plain words rather than field names.

    `kind` selects which comparison runs, and two sensors may be paired more
    than once under different kinds. That is not a technicality — it is what
    makes blame possible. GNSS position against the inertial estimate and GNSS
    course against gyro heading are both "gnss vs imu", but one is nearly blind
    to a slow walk-off and the other is not.
    """

    a: str
    b: str
    label: str
    kind: str
    domain: str
    """What the check is sensitive to: "horizontal", "vertical" or "heading".

    Blame needs this. A sensor is only cleared by a check that *would have
    caught* the fault being investigated — GNSS passing an altitude check says
    nothing about whether it is lying horizontally, and passing a position
    check says almost nothing about a slow walk-off. Allowing an unrelated
    pass to exonerate a sensor lets the real culprit walk free.
    """


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
        Pair(GNSS, IMU, "GPS position vs inertial estimate", "position", "horizontal"),
        Pair(GNSS, BARO, "GPS altitude vs barometric altitude", "altitude", "vertical"),
        Pair(GNSS, MAG, "GPS course vs compass heading", "course_mag", "heading"),
        Pair(MAG, IMU, "compass heading vs gyro-integrated heading", "heading_offset", "heading"),
    ),
    has_baro=True,
    accel_bias_sigma=0.05,
    field_notes="Flight controller grade IMU. No wheels, no road constraint.",
)

TRUCK = Profile(
    name="truck",
    sensors=(GNSS, IMU, MAG, ODOM, ROAD),
    pairs=(
        Pair(GNSS, IMU, "GPS position vs inertial estimate", "position", "horizontal"),
        Pair(GNSS, ODOM, "GPS distance vs wheel distance", "distance", "horizontal"),
        Pair(GNSS, ROAD, "GPS position vs the road network", "road", "horizontal"),
        Pair(GNSS, MAG, "GPS course vs compass heading", "course_mag", "heading"),
        Pair(MAG, IMU, "compass heading vs gyro-integrated heading", "heading_offset", "heading"),
    ),
    has_baro=False,
    accel_bias_sigma=0.08,
    field_notes=(
        "Telematics-box IMU, noisier than a drone's. Wheels give real ground "
        "distance and the road network constrains position — both are checks a "
        "drone does not have, which makes trucks easier to protect."
    ),
)

INFALLIBLE = frozenset({ROAD})
"""Parties to a check that cannot themselves be at fault.

A map does not drift, fail or get spoofed. When a reported position and the
road network disagree, exactly one of them is wrong, and it is never the road
— so blame must not be allowed to name it. Without this the road check would
be as likely to accuse the map as the receiver, and prove nothing."""


HOW_SENSED = {
    GNSS: "radio",
    MAG: "field",
    BARO: "field",
    IMU: "inertial",
    ODOM: "mechanical",
    ROAD: "prior",
}
"""How each sensor comes by its information.

Stage 6 needs this to work out what could *cause* a coherent error, and the
answer is physics rather than a lookup table of sensor names.

A radio sensor is told its answer from 20,000 km away, below the noise floor.
A steady, purposeful error in one means somebody is transmitting — an attack.

A field sensor measures something in the air immediately around it. Nobody can
transmit a magnetic field from orbit, so a steady error in one means the field
itself was changed — interference, at close range.

Inertial and mechanical sensors measure the vehicle's own body. Neither can be
reached without touching the vehicle, so a coherent error in one is far more
likely to be the sensor failing than anyone attacking it.
"""


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
