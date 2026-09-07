"""Turn an OpenStreetMap extract into the map and route the demo drives.

    python -m simulator.bake_map path/to/overpass.json

Run once, by hand, with the wifi on. It writes `simulator/chennai_map.json`,
which is committed and read from disk at run time — so the demo itself never
touches the network. That is the whole point: an online map fails *silently*,
giving you a grey rectangle in front of judges with no error and no warning.

## Why real geometry rather than a drawn one

The hand-drawn network was three polylines on a white background, and it
looked like exactly what it was. A logistics buyer recognises the road their
own lorries take; they do not recognise a diagram.

More than looks, though, it keeps the story honest. The truck now drives the
actual carriageway of NH-48 into the Sriperumbudur junction and turns onto the
real road south — so when a spoofed position wanders into a field, it is
wandering into a field that is really there, and the road check is measuring
against a road that really exists.

## What it keeps

Everything inside a couple of kilometres of the junction, classified so the
console can draw a trunk road differently from a service lane, and simplified
so the file stays small enough to ship. Coordinates are converted to local
metres east and north of the junction, which is what the detector works in
anyway.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from .chennai import ORIGIN_LAT, ORIGIN_LON

OUT = Path(__file__).with_name("chennai_map.json")

HALF_BOX_M = 2600.0
"""How far from the junction to keep. Enough to hold a three-minute drive with
context around it, small enough that the file stays under a megabyte."""

JUNCTION_NEAR_M = 900.0
"""How close to the origin the turn-off has to be.

The origin is the Sriperumbudur junction, and that is the whole story: a
container is not diverted on the open highway with cameras, it is diverted
after the turn-off. A junction somewhere else on the map is a different
journey."""

SIMPLIFY_M = 6.0
"""Drop points closer together than this. Road geometry is surveyed to
centimetres; a screen showing two kilometres cannot use that."""

# Which classes are worth drawing, and how prominent each is. A demo map that
# draws every service lane as heavily as the national highway is unreadable.
CLASS_RANK = {
    "motorway": 4, "trunk": 4, "primary": 3, "secondary": 3,
    "tertiary": 2, "unclassified": 1, "residential": 1, "service": 0,
}

_LAT_M = 111_320.0


def _lon_m(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def to_enu(lat: float, lon: float) -> tuple[float, float]:
    return ((lon - ORIGIN_LON) * _lon_m(ORIGIN_LAT), (lat - ORIGIN_LAT) * _LAT_M)


def simplify(points: list[list[float]], tolerance: float = SIMPLIFY_M) -> list[list[float]]:
    if len(points) < 3:
        return points
    kept = [points[0]]
    for point in points[1:-1]:
        if math.dist(point, kept[-1]) >= tolerance:
            kept.append(point)
    kept.append(points[-1])
    return kept


def load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    roads = []
    for element in data.get("elements", []):
        geometry = element.get("geometry")
        if not geometry:
            continue
        tags = element.get("tags", {})
        cls = tags.get("highway", "")
        if cls not in CLASS_RANK:
            continue

        points = [list(to_enu(node["lat"], node["lon"])) for node in geometry]
        points = [p for p in points
                  if abs(p[0]) <= HALF_BOX_M and abs(p[1]) <= HALF_BOX_M]
        if len(points) < 2:
            continue

        roads.append({
            "name": tags.get("name") or tags.get("ref") or "",
            "cls": cls,
            "rank": CLASS_RANK[cls],
            "points": simplify(points),
        })
    return roads


def chain(segments: list[list[list[float]]], gap: float = 40.0) -> list[list[float]]:
    """Stitch road segments into one continuous line.

    OSM splits a road wherever anything about it changes — a bridge, a speed
    limit, a name — so a single carriageway arrives as two dozen pieces in no
    particular order. The truck needs one path, so they are joined end to end,
    nearest first, flipping any that were drawn backwards.
    """
    if not segments:
        return []
    remaining = [list(s) for s in segments]
    line = remaining.pop(0)

    changed = True
    while remaining and changed:
        changed = False
        for i, seg in enumerate(remaining):
            for end, candidate in (("tail", seg), ("tail", seg[::-1]),
                                   ("head", seg), ("head", seg[::-1])):
                if end == "tail" and math.dist(line[-1], candidate[0]) <= gap:
                    line = line + candidate[1:]
                elif end == "head" and math.dist(line[0], candidate[-1]) <= gap:
                    line = candidate[:-1] + line
                else:
                    continue
                remaining.pop(i)
                changed = True
                break
            if changed:
                break
    return line


def cut(line: list[list[float]], metres: float, *, from_end: bool) -> list[list[float]]:
    """The first or last `metres` of a path."""
    work = line[::-1] if from_end else line
    out, run = [work[0]], 0.0
    for previous, point in zip(work, work[1:]):
        run += math.dist(previous, point)
        out.append(point)
        if run >= metres:
            break
    return out[::-1] if from_end else out


def build_route(roads: list[dict]) -> dict:
    """The leg the truck drives: along NH-48, then off it at Sriperumbudur.

    The turn-off is chosen by geometry rather than by name. Picking it by name
    went wrong twice: OSM splits one road into two dozen pieces that do not
    all chain, and the piece that does chain ran north to Thiruvallur while
    the pieces heading south sat unconnected — so the lorry drove up the road
    it was supposed to turn down, and the route doubled back on itself.

    What actually matters is not what the road is called. It is that a real
    road meets the highway and carries on for a couple of kilometres away from
    it, which is the shape of every turn-off a container is diverted down.
    """
    highway = chain([r["points"] for r in roads if r["name"] == "NH48"])
    if len(highway) < 4:
        raise SystemExit("no NH48 geometry in this extract")

    def gap_to_highway(point):
        return min(math.dist(point, h) for h in highway)

    # Candidates: every other road of consequence, stitched into whole lines.
    others = {}
    for road in roads:
        if road["rank"] < 3 or road["name"] == "NH48" or not road["name"]:
            continue
        others.setdefault(road["name"], []).append(road["points"])

    best = None
    for name, pieces in others.items():
        for line in (chain(pieces), chain(pieces[::-1])):
            if len(line) < 3:
                continue
            gaps = [gap_to_highway(p) for p in line]
            meets = min(gaps)
            if meets > 120.0:              # never actually reaches the highway
                continue
            join = gaps.index(meets)
            # And it has to meet it *here*. Without this the search happily
            # picked a junction two and a half kilometres away at the far
            # corner of the extract, where NH48 simply runs out — which is the
            # edge of the map, not a turn-off, and not Sriperumbudur.
            if math.hypot(*line[join]) > JUNCTION_NEAR_M:
                continue
            # The useful half is whichever side of the junction is longer.
            for branch in (line[join:], line[:join + 1][::-1]):
                if len(branch) < 3:
                    continue
                run = sum(math.dist(a, b) for a, b in zip(branch, branch[1:]))
                if run < 700.0:
                    continue
                if best is None or run > best[0]:
                    best = (run, name, branch, branch[0])
    if best is None:
        raise SystemExit("no side road meets NH48 in this extract")

    _run, turn_name, leg_line, junction = best

    # Cut the highway *at the junction*, not at whichever end of it happens
    # to be nearest. The junction sits partway along NH-48, so trimming from
    # the end left the approach finishing a kilometre short — and the route
    # then jumped that kilometre in one step. The lorry drove the straight
    # line across it, a hundred and twenty metres off any carriageway, which
    # the road check correctly called a truck in a field.
    join = min(range(len(highway)), key=lambda i: math.dist(highway[i], junction))
    approach = cut(highway[:join + 1], 1200.0, from_end=True)
    if math.dist(approach[-1], junction) > 1.0:
        approach.append(list(junction))

    leg = cut(leg_line, 2200.0, from_end=False)
    if math.dist(leg[0], junction) > 1.0:
        leg.insert(0, list(junction))

    return {"approach": approach, "leg": leg,
            "junction": list(junction), "turn": turn_name}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2

    roads = load(Path(argv[0]))
    route = build_route(roads)

    payload = {
        "origin": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
        "note": "Baked from OpenStreetMap by simulator/bake_map.py. "
                "Read from disk at run time; the demo never goes online.",
        "roads": sorted(roads, key=lambda r: r["rank"]),
        "route": route,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    drawn = sum(len(r["points"]) for r in roads)
    length = sum(math.dist(a, b) for a, b in
                 zip(route["approach"] + route["leg"],
                     (route["approach"] + route["leg"])[1:]))
    print(f"{len(roads)} roads, {drawn} points -> {OUT} "
          f"({OUT.stat().st_size / 1024:.0f} KB)")
    print(f"route: {length:.0f} m — NH48, then {route['turn']}")
    print(f"turn-off at ({route['junction'][0]:.0f}, {route['junction'][1]:.0f}) m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
