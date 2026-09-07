"""
roads.py — Small road network for the truck scenarios.

A road is a polyline — a list of (east, north) vertices in local ENU metres.
Straight-line segments are fine. No curves, no map data, nothing downloaded.

The truck follows waypoints (vehicle.py); this module exists so the detector
can cross-check a reported position against the road network. A spoofed GPS
that drifts into a field is caught instantly — trucks cannot drive through
fields.

Usage
-----
    from simulator.roads import distance_to_nearest_road, nearest_road_name
"""

import math


# ---------------------------------------------------------------------------
# Road network — ENU metres from the Coimbatore origin
# ---------------------------------------------------------------------------
ROADS = [
    # (name, [(east, north), (east, north), ...])
    (
        "NH-544",
        [
            (0, 0),
            (400, 20),
            (800, 60),
            (1200, 100),
            (1600, 130),
            (2000, 150),
            (2400, 160),
            (2800, 165),
            (3200, 168),
            (3600, 170),
        ],
    ),
    (
        "service road",
        [
            (1200, 100),
            (1180, -50),
            (1150, -200),
            (1120, -350),
        ],
    ),
    (
        "warehouse lane",
        [
            (1120, -350),
            (1100, -400),
            (1050, -430),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _dist_point_to_segment(px, py, ax, ay, bx, by):
    """Distance from point (px, py) to the line segment (ax, ay)-(bx, by)."""
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def distance_to_nearest_road(east: float, north: float) -> float:
    """Metres from this point to the closest road.  0 means on a road."""
    best = float("inf")
    for _name, verts in ROADS:
        for i in range(len(verts) - 1):
            ax, ay = verts[i]
            bx, by = verts[i + 1]
            d = _dist_point_to_segment(east, north, ax, ay, bx, by)
            if d < best:
                best = d
    return best


def nearest_road_name(east: float, north: float) -> str:
    """Name of the closest road, or 'none' if > 50 m from any road."""
    best_dist = float("inf")
    best_name = "none"
    for name, verts in ROADS:
        for i in range(len(verts) - 1):
            ax, ay = verts[i]
            bx, by = verts[i + 1]
            d = _dist_point_to_segment(east, north, ax, ay, bx, by)
            if d < best_dist:
                best_dist = d
                best_name = name
    return best_name if best_dist <= 50.0 else "none"
