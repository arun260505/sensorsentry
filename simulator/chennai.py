"""The stretch of road the demo drives: Sriperumbudur to Oragadam.

A buyer does not recognise "NH-544" and a grid. They recognise the road their
own lorries take, so the demo drives a real one — the last leg of the Chennai
Port container run, where NH-48 reaches Sriperumbudur and the traffic turns
south into the SIPCOT industrial estates around Oragadam.

## Why this leg and not the whole route

The full run is Chennai Port to Oragadam, about 45 km. A lorry covers 3 km in
the three minutes a demo lasts, so a corridor-length map would spend the whole
run in Royapuram and never reach anything worth naming.

This leg is about 2 km, which is exactly one run — and it is the part of the
journey where the theft actually happens. A container is not stolen on an open
highway with cameras; it is diverted after the turn-off, into an estate, into
a yard. Everything the story needs is here: the highway, the junction, the
industrial road, and somewhere to park a lorry out of sight.

## On the geometry

Real places, in the right relationship to each other, at about half true
scale so that one three-minute delivery covers the whole leg. At full scale a
lorry spends the entire demo approaching the junction and never turns off,
which loses the only part of the journey that matters.

Not surveyed centrelines either. The
detector's road tolerance is set wide enough to allow for that. An operator
needs to recognise the route; they do not need it accurate to the kerb.

Everything is in local metres east and north of the Sriperumbudur junction,
which is what the detector works in. `vehicle.py` converts to latitude and
longitude at the edge.
"""

from __future__ import annotations

import json
from pathlib import Path

_BAKED = Path(__file__).with_name("chennai_map.json")

# The junction where NH-48 meets the old Chennai-Bangalore road at
# Sriperumbudur. Every position below is metres from here.
#
# Surveyed, not chosen. The first origin was picked off a map by eye and no
# main road actually met NH-48 within a kilometre of it — so the "turn-off"
# the whole cargo-theft story depends on was not a junction at all. These
# coordinates come out of the OpenStreetMap extract: it is where two real
# highways really cross, and the road the lorry turns onto runs 2.7 km from
# there.
ORIGIN_LAT = 12.97519
ORIGIN_LON = 79.95740
ORIGIN_ALT = 60.0
"""Sriperumbudur sits around 60 m above sea level. It matters: barometric
altitude is measured against this origin, and the old network assumed a 412 m
plateau that does not exist here."""


def _load_baked():
    """Real surveyed roads, and the leg the truck drives along them.

    Baked once from OpenStreetMap by `bake_map.py` and committed, so the demo
    reads it off disk and never goes near the network. If the file is missing
    the drawn fallback below is used, and everything still runs.

    Only the roads a lorry would actually be on are handed to the detector's
    road check. Eight hundred ways including every service lane and driveway
    would make "is this position on a road?" true almost everywhere, which
    would quietly destroy the check that makes a truck easier to protect than
    a drone.
    """
    if not _BAKED.is_file():
        return None
    try:
        data = json.loads(_BAKED.read_text(encoding="utf-8"))
    except Exception:
        return None

    roads = [(r["name"] or r["cls"], [tuple(p) for p in r["points"]])
             for r in data.get("roads", []) if r.get("rank", 0) >= 2]
    route = data["route"]
    path = [tuple(p) for p in route["approach"] + route["leg"]]
    junction = tuple(route["junction"])
    if not roads or len(path) < 4:
        return None
    return {"roads": roads, "route": path, "junction": junction}


_BAKED_MAP = _load_baked()

ROUTE = None if _BAKED_MAP is None else _BAKED_MAP["route"]
"""The real carriageway the truck follows, as local metres. None when running
on the drawn fallback."""

_FALLBACK_ROADS = [
    (
        "NH-48",
        [
            (-700, 250),     # coming in from Chennai
            (-350, 125),
            (0, 0),           # Sriperumbudur junction
            (300, -75),      # carries on toward Bangalore
        ],
    ),
    (
        "Oragadam Road",
        [
            (0, 0),           # leaves NH-48 at the junction
            (100, -350),
            (225, -700),
            (350, -1000),     # SIPCOT industrial estate
        ],
    ),
    (
        "Estate access road",
        [
            (350, -1000),
            (525, -1125),
            (650, -1200),    # the yard
        ],
    ),
]

ROADS = _FALLBACK_ROADS if _BAKED_MAP is None else _BAKED_MAP["roads"]


def _places():
    """Named points, placed on the route that is actually in use."""
    if _BAKED_MAP is None:
        return [
            ("Sriperumbudur junction", (0, 0), "waypoint"),
            ("SIPCOT Oragadam", (350, -1000), "building"),
            ("Yard", (650, -1200), "building"),
            ("to Chennai", (-700, 250), "waypoint"),
            ("to Bangalore", (300, -75), "waypoint"),
        ]
    route = _BAKED_MAP["route"]
    return [
        ("Sriperumbudur junction", _BAKED_MAP["junction"], "waypoint"),
        ("to Chennai", route[0], "waypoint"),
        ("Yard", route[-1], "building"),
    ]


PLACES = _places()
"""Named so an operator follows the story without being told it. When the
reported position keeps running down NH-48 toward Bangalore while the lorry is
sitting in a yard off the estate access road, both places are on the screen
with the names they actually have."""
