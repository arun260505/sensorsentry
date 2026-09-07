"""Phase 9 — one vehicle can be fooled; a fleet cannot.

The strongest claim in the whole project, and the only one a single-vehicle
system cannot make at all — not "harder", genuinely impossible. One drone
reporting that its GPS is lying might be a broken receiver. Four vehicles in
the same square kilometre reporting it inside the same minute is somebody with
a transmitter, and the overlap of who is affected says roughly where they are
standing.

That turns the product from a defence into a warning network: once the zone is
on the map, every other vehicle heading toward it can be told before it gets
there. Detection stops being per-vehicle and starts being shared.

## Only attacks cluster

A fault is a property of one vehicle. Two lorries whose compasses fail in the
same week is coincidence, not an attack, and drawing a zone around them would
send someone to look for a transmitter that does not exist. So only incidents
stage 6 called an attack are eligible, which is a large part of why stage 6
had to exist before this one.

## Positions used are the vehicle's own, never the reported fix

Obvious once said, easy to get wrong: during a spoofing attack the reported
position is the attacker's choice. Cluster on that and the attacker decides
where you go looking. We use each vehicle's own inertial estimate — the
position it worked out for itself, which is what he cannot touch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

EARTH_RADIUS_M = 6_378_137.0

CLUSTER_RADIUS_M = 3000.0
"""How far apart two vehicles may be and still be considered the same event.

A ground transmitter strong enough to overpower GNSS reaches a few kilometres
at most. Wider than that and unrelated incidents merge into one meaningless
blob covering half a city."""

CLUSTER_WINDOW_S = 120.0
"""How far apart in time two incidents may be and still be one event. Long
enough to gather vehicles passing through, short enough that this morning's
attack does not merge with this afternoon's."""

MIN_VEHICLES = 2
"""Below this, no zone. One vehicle could be a fault, and a wrongly drawn zone
sends people to the wrong place — worse than drawing nothing."""

ZONE_MARGIN_M = 400.0
"""Added to the radius covering the affected vehicles.

The transmitter is not standing on top of any of them; it is somewhere that
reaches all of them. This is deliberately a rough answer, and the console says
so — a circle that looks too precise invites a trust it has not earned."""


@dataclass(frozen=True)
class Incident:
    """One vehicle reporting that it is under attack."""

    vehicle_id: str
    t: float
    lat: float
    lon: float
    """Where the vehicle itself believes it is — never the reported fix."""

    guilty: str
    cause: str
    confidence: float = 0.0


@dataclass
class AttackZone:
    """Roughly where the transmitter is."""

    lat: float
    lon: float
    radius_m: float
    vehicles: list[str] = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def confidence(self) -> float:
        """Grows with the number of independent vehicles agreeing.

        Two is enough to rule out a single failing receiver; four is hard to
        explain any other way. Deliberately not a probability — it is a
        statement about how many independent witnesses there are."""
        return min(1.0, 0.4 + 0.2 * (len(self.vehicles) - MIN_VEHICLES))

    def describe(self) -> str:
        n = len(self.vehicles)
        return (f"{n} vehicles attacked within {self.radius_m:.0f} m of each "
                f"other — likely a transmitter in this area")


def metres_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres. Equirectangular, which is ample at these ranges."""
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * EARTH_RADIUS_M * math.cos(mean_lat)
    dy = math.radians(lat2 - lat1) * EARTH_RADIUS_M
    return math.hypot(dx, dy)


def find_zones(
    incidents: Iterable[Incident],
    *,
    now: Optional[float] = None,
    radius_m: float = CLUSTER_RADIUS_M,
    window_s: float = CLUSTER_WINDOW_S,
    min_vehicles: int = MIN_VEHICLES,
) -> list[AttackZone]:
    """Group attack reports into zones. Returns [] when nothing groups.

    Single-link clustering: incidents join a group if they are close to *any*
    member, not to the centre. An attacker beside a road affects a string of
    vehicles along it, and insisting everything sit near one point would split
    that into fragments.
    """
    eligible = [i for i in incidents if i.cause == "attack"]
    if now is not None:
        eligible = [i for i in eligible if now - i.t <= window_s]
    if len(eligible) < min_vehicles:
        return []

    eligible.sort(key=lambda i: i.t)
    groups: list[list[Incident]] = []

    for incident in eligible:
        joined = None
        for group in groups:
            close = any(
                metres_between(incident.lat, incident.lon, other.lat, other.lon) <= radius_m
                and abs(incident.t - other.t) <= window_s
                for other in group
            )
            if close:
                if joined is None:
                    group.append(incident)
                    joined = group
                else:
                    # Bridges two groups that were only separate because
                    # nothing had linked them yet. Merge rather than duplicate.
                    joined.extend(group)
                    group.clear()
        if joined is None:
            groups.append([incident])

    zones = []
    for group in (g for g in groups if g):
        # One report per vehicle: the newest. A vehicle sending an alert every
        # frame must not be able to outvote three other vehicles.
        latest: dict[str, Incident] = {}
        for incident in group:
            if incident.vehicle_id not in latest or incident.t > latest[incident.vehicle_id].t:
                latest[incident.vehicle_id] = incident
        members = list(latest.values())
        if len(members) < min_vehicles:
            continue
        zones.append(_zone_from(members))

    zones.sort(key=lambda z: len(z.vehicles), reverse=True)
    return zones


def _zone_from(members: list[Incident]) -> AttackZone:
    centre_lat = sum(m.lat for m in members) / len(members)
    centre_lon = sum(m.lon for m in members) / len(members)
    spread = max(
        metres_between(centre_lat, centre_lon, m.lat, m.lon) for m in members
    )
    return AttackZone(
        lat=centre_lat,
        lon=centre_lon,
        radius_m=spread + ZONE_MARGIN_M,
        vehicles=sorted(m.vehicle_id for m in members),
        first_seen=min(m.t for m in members),
        last_seen=max(m.t for m in members),
    )
