"""The stretch of road the demo drives: VIT Chennai towards Kelambakkam.

A buyer does not recognise "NH-544" and a grid. They recognise the road they
drove in on, so the demo drives a real one — out of VIT Chennai along the
Vandalur - Kelambakkam Road, to the Mambakkam junction where the Medavakkam
road crosses, and off it to the south.

## Why this leg and not the whole route

A lorry covers about 3 km in the three minutes a demo lasts, so a
corridor-length map would spend the whole run in one suburb and never reach
anything worth naming.

This leg is about 4 km, which is one run — and it contains the part of the
journey where a theft actually happens. A container is not stolen on an open
road with traffic on it; it is diverted after a turn-off, down a quieter road,
into a yard. Everything the story needs is here: the through road, a real
junction, the road south, and somewhere to put a lorry out of sight.

## On the geometry

Real places, in the right relationship to each other, at about half true
scale so that one three-minute delivery covers the whole leg. At full scale a
lorry spends the entire demo approaching the junction and never turns off,
which loses the only part of the journey that matters.

Not surveyed centrelines either. The
detector's road tolerance is set wide enough to allow for that. An operator
needs to recognise the route; they do not need it accurate to the kerb.

Everything is in local metres east and north of the Mambakkam junction, which
is what the detector works in. `vehicle.py` converts to latitude and longitude
at the edge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BAKED = Path(__file__).with_name("chennai_map.json")

# Mambakkam junction, where the Medavakkam - Mambakkam - Sembakkam Road meets
# the Vandalur - Kelambakkam Road, 1.9 km south-east of VIT Chennai. Every
# position below is metres from here.
#
# Surveyed, not chosen. An origin picked off a map by eye once left the demo
# with no main road meeting the highway within a kilometre of it — so the
# "turn-off" the whole cargo-theft story depends on was not a junction at all.
# These coordinates come out of the OpenStreetMap extract: it is where two
# real roads cross, and the road the lorry turns onto runs 6 km from there.
ORIGIN_LAT = 12.82765
ORIGIN_LON = 80.16513
ORIGIN_ALT = 20.0
"""The Kelambakkam plain is close to sea level — around 20 m, against the 60 m
of the Sriperumbudur corridor and the 412 m plateau an even older version
assumed. It matters: barometric altitude is measured against this origin."""


def _complain(why: str) -> None:
    """Say, loudly, that the map on screen is not the real one.

    The fallback below is a drawn sketch, not surveyed geometry, and it is a
    different shape from the corridor we talk about on stage. Falling back to
    it silently is the worst behaviour available: nothing errors, the map looks
    plausible, and you are describing one road while the screen draws another.
    Exactly the failure mode as a stale server answering on :8080 — which has
    already cost this project a rehearsal.
    """
    print(f"\n*** SENSORSENTRY MAP FALLBACK: {why}\n"
          f"*** The screen is showing a DRAWN SKETCH, not the real corridor.\n"
          f"*** Re-bake with:  python -m simulator.bake_map <extract.json>\n",
          file=sys.stderr, flush=True)


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
        _complain("chennai_map.json is missing")
        return None
    try:
        data = json.loads(_BAKED.read_text(encoding="utf-8"))
    except Exception as exc:
        _complain(f"chennai_map.json will not parse: {exc}")
        return None

    roads = [(r["name"] or r["cls"], [tuple(p) for p in r["points"]])
             for r in data.get("roads", []) if r.get("rank", 0) >= 2]
    route = data["route"]
    path = [tuple(p) for p in route["approach"] + route["leg"]]
    junction = tuple(route["junction"])
    if not roads or len(path) < 4:
        _complain("chennai_map.json has no usable roads or route")
        return None
    return {"roads": roads, "route": path, "junction": junction}


_BAKED_MAP = _load_baked()

ROUTE = None if _BAKED_MAP is None else _BAKED_MAP["route"]
"""The real carriageway the truck follows, as local metres. None when running
on the drawn fallback."""

_FALLBACK_ROADS = [
    (
        "Vandalur - Kelambakkam Road",
        [
            (-995, 910),      # coming out of VIT Chennai
            (-500, 460),
            (0, 0),           # Mambakkam junction
            (900, -830),      # carries on toward Kelambakkam
        ],
    ),
    (
        "Medavakkam - Mambakkam Road",
        [
            (0, 0),           # leaves the main road at the junction
            (-400, -600),
            (-900, -1300),
            (-1539, -2026),   # south, away from the traffic
        ],
    ),
    (
        "Yard access road",
        [
            (-1539, -2026),
            (-1350, -2150),
            (-1150, -2250),   # the yard
        ],
    ),
]

ROADS = _FALLBACK_ROADS if _BAKED_MAP is None else _BAKED_MAP["roads"]


def _places():
    """Named points, placed on the route that is actually in use."""
    if _BAKED_MAP is None:
        return [
            ("Mambakkam junction", (0, 0), "waypoint"),
            ("Yard", (650, -1200), "building"),
            ("from VIT Chennai", (-995, 910), "waypoint"),
            ("to Kelambakkam", (900, -830), "waypoint"),
        ]
    route = _BAKED_MAP["route"]
    return [
        ("Mambakkam junction", _BAKED_MAP["junction"], "waypoint"),
        ("from VIT Chennai", route[0], "waypoint"),
        ("Yard", route[-1], "building"),
    ]


PLACES = _places()
"""Named so an operator follows the story without being told it. When the
reported position keeps running down NH-48 toward Bangalore while the lorry is
sitting in a yard off the estate access road, both places are on the screen
with the names they actually have."""
