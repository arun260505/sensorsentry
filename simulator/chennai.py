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

# The NH-48 junction at Sriperumbudur. Every position below is metres from here.
ORIGIN_LAT = 12.9675
ORIGIN_LON = 79.9450
ORIGIN_ALT = 60.0
"""Sriperumbudur sits around 60 m above sea level. It matters: barometric
altitude is measured against this origin, and the old network assumed a 412 m
plateau that does not exist here."""


ROADS = [
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

PLACES = [
    ("Sriperumbudur junction", (0, 0), "waypoint"),
    ("SIPCOT Oragadam", (350, -1000), "building"),
    ("Yard", (650, -1200), "building"),
    ("to Chennai", (-700, 250), "waypoint"),
    ("to Bangalore", (300, -75), "waypoint"),
]
"""Named so an operator follows the story without being told it. When the
reported position keeps running down NH-48 toward Bangalore while the lorry is
sitting in a yard off the estate access road, both places are on the screen
with the names they actually have."""
