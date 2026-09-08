"""Turn an OpenStreetMap extract into the map and route the demo drives.

    python -m simulator.bake_map roads.json [features.json]

The first extract is the road network — `way["highway"]` over the corridor —
and is all that is needed for the simulation to run. The second is optional and
purely for the eye: buildings, water, landuse. Roads on their own draw a
diagram; what makes a map read as a *place* is what sits between them.

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

HALF_BOX_M = 3500.0
"""How far from the junction to keep.

Was 2600, which was not enough: the route itself ends 2544 m out, so the map
ran out essentially where the lorry stopped. A judge steering the spoofed GPS
with the arrow keys drove it straight off the edge into blank canvas — the map
running out exactly while somebody is using it, which is the worst moment for
it to happen.

**Raising this is not free, and the cost lands on detection, not on the file
size.** Every road inside the box is a road the check has to honour, and a
spoofed position sitting on any of them passes. 2600 m gives the check 30
roads; 3500 gives it 43; 4000 gives it 75, which is most of the way back to
the hundred that cost us a metre per second on the Sriperumbudur corridor.

So this is a measured number, not a chosen one — see the sweep. And drawing
and checking must use the *same* box: drawing roads we do not check would put a
spoofed position visibly on a road while the console called it off-road, and a
judge would be right to ask."""

JUNCTION_NEAR_M = 900.0
"""How close to the origin the turn-off has to be.

The origin is the Sriperumbudur junction, and that is the whole story: a
container is not diverted on the open highway with cameras, it is diverted
after the turn-off. A junction somewhere else on the map is a different
journey."""

SIMPLIFY_M = 6.0
"""Drop points closer together than this. Road geometry is surveyed to
centimetres; a screen showing two kilometres cannot use that."""

HIGHWAY_NAME = "Vandalur - Mambakkam - Kelambakkam Road"
"""The road the lorry comes in along, exactly as OpenStreetMap names it.

Not a guess — the name has to match the `name` or `ref` tag character for
character or nothing chains and the bake fails loudly. Find it by listing the
longest roads in the extract before changing this.

It was `NH48` while the demo drove the Sriperumbudur corridor. Keeping it as a
constant rather than a literal buried in `build_route` is what makes the
corridor swappable at all."""

START_LAT, START_LON = 12.8406, 80.1534
"""Where the delivery comes *from* — VIT Chennai's main gate.

A junction has two approaches and the geometry cannot tell you which one the
story wants. Picking the longer side sent the lorry inbound from Kelambakkam
towards VIT, which is the journey backwards. This says which end the run starts
at, and `build_route` picks the approach that begins nearest it."""

APPROACH_M = 1200.0
LEG_M = 2200.0
"""How much of the highway and of the turn-off to keep. Together they set the
route length, and the route length has to be about one three-minute drive: too
short and the run ends before a verdict settles, too long and the lorry never
reaches the junction, which is the only part of the journey the story needs."""

# Which classes are worth drawing, and how prominent each is. A demo map that
# draws every service lane as heavily as the national highway is unreadable.
CLASS_RANK = {
    "motorway": 4, "trunk": 4, "primary": 3, "secondary": 3,
    "tertiary": 2, "unclassified": 1, "residential": 1, "service": 0,
}

AREA_KIND = {
    # tag -> value -> which of our four washes it belongs in
    "natural":  {"water": "water", "wetland": "water", "bay": "water",
                 "wood": "green", "scrub": "green", "grassland": "green",
                 "sand": "sand", "beach": "sand"},
    "landuse":  {"reservoir": "water", "basin": "water",
                 "forest": "green", "grass": "green", "meadow": "green",
                 "farmland": "green", "orchard": "green", "village_green": "green",
                 "recreation_ground": "green", "cemetery": "green",
                 "residential": "built", "industrial": "built",
                 "commercial": "built", "retail": "built",
                 "construction": "built", "quarry": "built",
                 "military": "built"},
    "leisure":  {"park": "green", "garden": "green", "pitch": "green",
                 "golf_course": "green", "sports_centre": "green",
                 "playground": "green", "nature_reserve": "green"},
    "amenity":  {"school": "campus", "college": "campus",
                 "university": "campus", "hospital": "campus"},
}
"""What each OSM tag means to the painter.

Deliberately a small set of *washes* rather than a faithful cartography. A
console map has one job — show a lorry on a road and a second lorry beside it
that is not really there — and forty shades of landuse compete with it. Four
tones and buildings is enough for the eye to read "this is a place" without
anything arguing with the two tracks.
"""

MIN_AREA_M2 = 60.0
"""Drop anything smaller than a garden shed.

Set at 400 first, which sounds modest and quietly deleted seven buildings in
eight — an ordinary house is 100-150 m². The console only draws buildings at
close zoom, where a house is tens of pixels across, so the threshold only
needs to remove genuine specks."""

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

        # Cut the way where it leaves the box; do not simply delete the points
        # outside it. Filtering in place welds the two ends of an excursion
        # together, so a road that wanders out of the box and back gets a
        # straight line drawn across the gap. Harmless on a road nobody drives
        # — but the turn-off is a road we drive, and there the weld is a
        # teleport. Same failure as cutting the highway at its end instead of
        # at the junction, found the same way: by measuring the longest step
        # in the finished route.
        runs, run = [], []
        for point in points:
            if abs(point[0]) <= HALF_BOX_M and abs(point[1]) <= HALF_BOX_M:
                run.append(point)
            else:
                if len(run) >= 2:
                    runs.append(run)
                run = []
        if len(run) >= 2:
            runs.append(run)

        for piece in runs:
            roads.append({
                "name": tags.get("name") or tags.get("ref") or "",
                "cls": cls,
                "rank": CLASS_RANK[cls],
                "points": simplify(piece),
            })
    return roads


def polygon_area(points: list[list[float]]) -> float:
    """Area of a closed ring, in square metres. Sign discarded."""
    total = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        total += ax * by - bx * ay
    return abs(total) / 2.0


def load_areas(path: Path) -> tuple[list[dict], list[dict]]:
    """Everything that is not a road: buildings, water, green, built-up land.

    Roads on their own draw a diagram. What makes a map read as a *place* is
    what sits between the roads — so this bakes the same offline file with the
    blocks, the water tanks and the campus, from the same free API.

    Returns (areas, rails). Rails are lines, not fills, so they are kept apart.
    """
    if not path.is_file():
        return [], []
    data = json.loads(path.read_text(encoding="utf-8"))

    areas, rails = [], []
    for element in data.get("elements", []):
        geometry = element.get("geometry")
        if not geometry or len(geometry) < 3:
            continue
        tags = element.get("tags", {})

        points = [list(to_enu(node["lat"], node["lon"])) for node in geometry]
        if all(abs(p[0]) > HALF_BOX_M or abs(p[1]) > HALF_BOX_M for p in points):
            continue

        if "railway" in tags:
            if tags["railway"] in ("rail", "light_rail", "subway"):
                rails.append({"points": simplify(points, 12.0)})
            continue

        kind = None
        if "building" in tags:
            kind = "building"
        else:
            for tag, mapping in AREA_KIND.items():
                if tags.get(tag) in mapping:
                    kind = mapping[tags[tag]]
                    break
        if kind is None:
            continue

        # A building is drawn as a shape; a landuse wash only needs its
        # outline, so it can be simplified far harder without anyone noticing.
        points = simplify(points, 3.0 if kind == "building" else 10.0)
        if len(points) < 3 or polygon_area(points) < MIN_AREA_M2:
            continue

        # Keep the name OSM already has. This corridor turns out to carry the
        # VIT campus itself, Maambakkam Lake beside the route, and the TAFE and
        # TAL works down at the far end — a lorry corridor with real factories
        # on it. Naming them is most of what separates a map from a diagram,
        # and a judge who finds their own campus on the screen believes the
        # rest of it. The console decides which ones will fit.
        area = {"kind": kind, "points": points}
        if tags.get("name"):
            area["name"] = tags["name"]
        areas.append(area)

    # Big washes first so a park does not paint over the buildings inside it.
    areas.sort(key=lambda a: -polygon_area(a["points"]))
    return areas, rails


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


def path_length(line: list[list[float]]) -> float:
    """Metres along a polyline."""
    return sum(math.dist(a, b) for a, b in zip(line, line[1:]))


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
    highway_pieces = [r["points"] for r in roads if r["name"] == HIGHWAY_NAME]
    highway = chain(highway_pieces)
    if len(highway) < 4:
        raise SystemExit(f"no {HIGHWAY_NAME!r} geometry in this extract")

    def gap_to_highway(point):
        return min(math.dist(point, h) for h in highway)

    # Candidates: every other road of consequence, stitched into whole lines.
    others = {}
    for road in roads:
        if road["rank"] < 3 or road["name"] == HIGHWAY_NAME or not road["name"]:
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
        raise SystemExit(f"no side road meets {HIGHWAY_NAME!r} in this extract")

    _run, turn_name, leg_line, junction = best

    # Cut the highway *at the junction*, not at whichever end of it happens to
    # be nearest. The junction sits partway along the road, so trimming from
    # the end once left the approach finishing a kilometre short — and the
    # route then jumped that kilometre in one step. The lorry drove the
    # straight line across it, a hundred and twenty metres off any carriageway,
    # which the road check correctly called a truck in a field.
    #
    # Which side of the junction it approaches along cannot be read off the
    # order the pieces happened to chain in, and two more things go wrong if
    # you try.
    #
    # Taking the "before" side blindly worked on NH-48 and gave a **zero metre
    # approach** here: the delivery would have begun with the lorry already
    # standing on the turn-off, no run-up, and no story to tell about it. The
    # cause is that this road is a **dual carriageway**. `chain` walked out
    # along one side and back down the other, producing a fourteen-kilometre
    # line that begins and ends at the same junction — a loop, on which the
    # junction is necessarily an endpoint and one side is necessarily empty.
    #
    # Nor is length the answer: the longer arm of this junction runs towards
    # Kelambakkam, so choosing it drove the whole journey backwards.
    #
    # So: gather every approach any candidate line can offer — the chained
    # line and the longest individual carriageways — keep the ones long enough
    # to be a run-up, and take whichever *starts* nearest where the delivery
    # is supposed to start from.
    start = to_enu(START_LAT, START_LON)
    candidates = [highway] + sorted(highway_pieces, key=path_length,
                                    reverse=True)[:4]
    approaches = []
    for line in candidates:
        if len(line) < 2:
            continue
        near = min(range(len(line)), key=lambda i: math.dist(line[i], junction))
        if math.dist(line[near], junction) > 60.0:
            continue                      # this carriageway misses the junction
        for side in (line[:near + 1], line[near:][::-1]):
            if len(side) < 2:
                continue
            piece = cut(side, APPROACH_M, from_end=True)
            if path_length(piece) < APPROACH_M * 0.8:
                continue
            if math.dist(piece[-1], junction) > 1.0:
                piece = piece + [list(junction)]
            approaches.append(piece)
    if not approaches:
        raise SystemExit("no arm of the highway is long enough to approach on")
    approach = min(approaches, key=lambda p: math.dist(p[0], start))

    leg = cut(leg_line, LEG_M, from_end=False)
    if math.dist(leg[0], junction) > 1.0:
        leg.insert(0, list(junction))

    return {"approach": approach, "leg": leg,
            "junction": list(junction), "turn": turn_name}


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print(__doc__)
        return 2

    roads = load(Path(argv[0]))
    route = build_route(roads)
    areas, rails = load_areas(Path(argv[1])) if len(argv) == 2 else ([], [])

    payload = {
        "origin": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
        "note": "Baked from OpenStreetMap by simulator/bake_map.py. "
                "Read from disk at run time; the demo never goes online.",
        "roads": sorted(roads, key=lambda r: r["rank"]),
        "areas": areas,
        "rails": rails,
        "route": route,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    drawn = sum(len(r["points"]) for r in roads)
    length = sum(math.dist(a, b) for a, b in
                 zip(route["approach"] + route["leg"],
                     (route["approach"] + route["leg"])[1:]))
    print(f"{len(roads)} roads, {drawn} points -> {OUT} "
          f"({OUT.stat().st_size / 1024:.0f} KB)")
    if areas or rails:
        counted: dict[str, int] = {}
        for area in areas:
            counted[area["kind"]] = counted.get(area["kind"], 0) + 1
        summary = ", ".join(f"{n} {k}" for k, n in
                            sorted(counted.items(), key=lambda kv: -kv[1]))
        print(f"areas: {summary}" + (f", {len(rails)} rail" if rails else ""))
    else:
        print("areas: none — pass a features extract as the second argument "
              "for buildings, water and landuse")
    print(f"route: {length:.0f} m — {HIGHWAY_NAME}, then {route['turn']}")
    print(f"turn-off at ({route['junction'][0]:.0f}, {route['junction'][1]:.0f}) m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
