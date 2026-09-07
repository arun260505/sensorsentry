"""Phase 9 — warn the vehicles that have not been attacked yet.

The point of locating an attacker is not the circle on the map. It is that
every other vehicle heading toward that circle can be told before it arrives.

This is where the product stops being a defence and becomes a warning network,
and it is the part a single-vehicle system cannot do at all — not because it
would be harder, but because a lone vehicle has nothing to compare itself
against and no one to tell.

It is also the answer to the obvious question about the ~2 m/s detection
floor. A walk-off too gentle for one vehicle to notice still gets caught
somewhere in a fleet, and after that nobody else has to detect it at all —
they are simply told to keep away.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .cluster import AttackZone, metres_between

APPROACH_MARGIN_M = 1500.0
"""Minimum distance at which a vehicle is warned, regardless of speed.

A floor rather than the rule: something barely moving still deserves warning
about a zone it is sitting next to."""

APPROACH_HORIZON_S = 180.0
"""How far ahead in *time* a vehicle is warned.

Distance alone is the wrong unit. A parked van and a truck at 22 m/s are in
completely different situations two kilometres from an attacker: one has all
afternoon, the other has ninety seconds. A fixed radius either floods the slow
vehicle with warnings or reaches the fast one too late to turn off — measured,
a drone three kilometres out and closing got no warning at all.

Three minutes is enough to reroute without blanketing a fleet with warnings
about somewhere none of them are going."""


@dataclass
class Advisory:
    """A warning to one vehicle that has not been attacked yet."""

    vehicle_id: str
    zone: AttackZone
    distance_m: float
    """From the vehicle to the edge of the zone. Negative means already inside."""

    seconds_away: Optional[float] = None
    """At current speed. None when stopped or not moving toward it."""

    @property
    def inside(self) -> bool:
        return self.distance_m <= 0.0

    def message(self) -> str:
        if self.inside:
            return ("You are inside an area where other vehicles are being "
                    "attacked. Treat GPS as unreliable.")
        when = (f" — about {self.seconds_away:.0f} s away"
                if self.seconds_away is not None else "")
        return (f"GPS attack reported {self.distance_m / 1000:.1f} km ahead"
                f"{when}. Consider rerouting.")


@dataclass
class VehicleState:
    """What the fleet service knows about a vehicle right now."""

    vehicle_id: str
    lat: float
    lon: float
    speed_mps: float = 0.0
    under_attack: bool = False
    """Already attacked vehicles are not warned about the zone they are in —
    they have a far more urgent message already."""


def advise(
    vehicles: Iterable[VehicleState],
    zones: Iterable[AttackZone],
    *,
    margin_m: float = APPROACH_MARGIN_M,
) -> list[Advisory]:
    """One advisory per vehicle, for the zone that matters most to it."""
    zones = list(zones)
    if not zones:
        return []

    out: list[Advisory] = []
    for vehicle in vehicles:
        if vehicle.under_attack:
            continue

        best: Optional[Advisory] = None
        for zone in zones:
            if vehicle.vehicle_id in zone.vehicles:
                continue
            centre = metres_between(vehicle.lat, vehicle.lon, zone.lat, zone.lon)
            to_edge = centre - zone.radius_m
            # Whichever reaches further: a fixed floor, or how far this vehicle
            # travels in the warning horizon.
            reach = max(margin_m, vehicle.speed_mps * APPROACH_HORIZON_S)
            if to_edge > reach:
                continue

            seconds = (to_edge / vehicle.speed_mps
                       if vehicle.speed_mps > 1.0 and to_edge > 0 else None)
            advisory = Advisory(vehicle.vehicle_id, zone, to_edge, seconds)
            if best is None or advisory.distance_m < best.distance_m:
                best = advisory

        if best is not None:
            out.append(best)

    out.sort(key=lambda a: a.distance_m)
    return out
