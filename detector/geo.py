"""Coordinate helpers.

All detector maths happens in local ENU metres (east, north, up) relative to a
fixed origin. Latitude/longitude exists only at the edges: it arrives in GNSS
frames and it leaves in verdict frames for the console to draw.

Equirectangular projection. Good to well under a metre over the few kilometres
a demo route covers, and it keeps the maths readable — which matters more here
than the last centimetre.
"""

from __future__ import annotations

import math
from typing import NamedTuple

EARTH_RADIUS_M = 6_378_137.0


class Origin(NamedTuple):
    """The point local ENU coordinates are measured from."""

    lat: float
    lon: float
    alt: float


class ENU(NamedTuple):
    """A position or displacement in local metres."""

    e: float
    n: float
    u: float

    def __sub__(self, other: "ENU") -> "ENU":
        return ENU(self.e - other.e, self.n - other.n, self.u - other.u)

    def __add__(self, other: "ENU") -> "ENU":
        return ENU(self.e + other.e, self.n + other.n, self.u + other.u)

    def horizontal_norm(self) -> float:
        """Distance in the horizontal plane, ignoring altitude.

        Horizontal and vertical are kept separate throughout the detector:
        GNSS altitude is far noisier than its horizontal fix, and altitude-only
        spoofing is its own attack. Mixing them hides both.
        """
        return math.hypot(self.e, self.n)

    def norm(self) -> float:
        return math.sqrt(self.e * self.e + self.n * self.n + self.u * self.u)


def enu_from_llh(lat: float, lon: float, alt: float, origin: Origin) -> ENU:
    """Geodetic degrees to local metres."""
    lat0 = math.radians(origin.lat)
    east = math.radians(lon - origin.lon) * EARTH_RADIUS_M * math.cos(lat0)
    north = math.radians(lat - origin.lat) * EARTH_RADIUS_M
    return ENU(east, north, alt - origin.alt)


def llh_from_enu(pos: ENU, origin: Origin) -> tuple[float, float, float]:
    """Local metres back to geodetic degrees."""
    lat0 = math.radians(origin.lat)
    lat = origin.lat + math.degrees(pos.n / EARTH_RADIUS_M)
    lon = origin.lon + math.degrees(pos.e / (EARTH_RADIUS_M * math.cos(lat0)))
    return lat, lon, pos.u + origin.alt


def yaw_from_heading(heading_deg: float) -> float:
    """Compass heading to ENU yaw, in radians.

    The magnetometer reports 0 at north increasing clockwise. ENU yaw is
    measured from east, counter-clockwise. Getting this backwards silently
    mirrors every dead-reckoned track, so it lives in one place.
    """
    return math.radians(90.0 - heading_deg)


def heading_from_yaw(yaw_rad: float) -> float:
    """ENU yaw back to a compass heading in [0, 360)."""
    return (90.0 - math.degrees(yaw_rad)) % 360.0


def wrap_pi(angle_rad: float) -> float:
    """Fold an angle into (-pi, pi].

    Used wherever two angles are differenced — without it a heading comparison
    across the 0/360 boundary reports a 359 degree error instead of one.
    """
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi
