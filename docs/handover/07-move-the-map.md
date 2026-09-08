# Task 7 — Move the corridor to VIT Chennai

**Status: done.** Branch `map-vit-chennai`. The plan below is kept because the
reasoning is still the reasoning — if the corridor ever moves again, this is
the order to do it in.

## What actually happened

The demo now drives out of **VIT Chennai**, south-east along the
**Vandalur–Kelambakkam Road**, to the **Mambakkam junction**, and off it south.

| | Sriperumbudur | VIT → Kelambakkam |
|---|---|---|
| Roads the check sees | 105 | **43** |
| **Truck floor** | 2 m/s | **1 m/s** |
| Meaconing | detected, not named | **named in 8 s** |
| False alarms | zero | **zero** |
| Tests / use cases / rehearsal | 66 / 17 / 10 | **66 / 17 / 10** |

**The density prediction in step 1 was right**, which is the main reason to
keep this file: a sparser network halved the truck floor, and turned one
documented weakness into a strength.

### Four bugs the move exposed, all fixed

1. **The road is a dual carriageway.** `chain()` walked out one side and back
   down the other, making a 14 km loop that began and ended at the junction —
   so the junction was an endpoint and the approach was **zero metres**.
2. **Length picked the wrong direction.** The longer arm runs to Kelambakkam,
   so the first working version drove the journey backwards. `START_LAT/LON`
   now says which end the run starts from.
3. **Clipping to the box welded excursions shut.** Points outside were deleted
   in place, so a road that left and came back got a straight line across the
   gap — scenery on most roads, a teleport on the route.
4. **`HALF_BOX_M` was smaller than the route.** The route ends 2544 m out and
   the map stopped at 2600, so a judge steering the spoofed GPS drove it into
   blank canvas. 3500 now, measured — see CLAUDE.md for why not 4000.

And one that was mine rather than the product's: reading the console's trails
as local metres when they are **lat/lon**, which made a perfectly healthy
puppet look frozen 81 m from the junction for ninety seconds. 81 is
`hypot(12.8, 80.1)`. **A number that does not move is not automatically a
frozen sensor.**

---

## The original plan, as written before any of it was built

## Why do it

The one argument, and it is a good one: *a buyer recognises the road their own
lorries take.* That is already the reason the map is real OSM data instead of
three drawn polylines. Sriperumbudur is a real container corridor, but the
people in the room did not drive down it this morning — they drove down the
road outside the hall. Pointing at the map and saying "that is the junction you
came through" is a stronger opening than any amount of explaining.

## Why it might not be worth it

**The corridor is load-bearing, not decoration.** Six measured numbers on the
results card come out of this specific geometry:

| | now |
|---|---|
| Truck detection floor | 2 m/s |
| Truck walk-off, alert | 47 s |
| Truck walk-off, names the sensor | 64–70 s |
| Truck floor caught in | 37 s |
| Route length | 1331 m |
| Roads the check sees (rank ≥ 2) | 105 |

**Every one of those can move.** CLAUDE.md already records why: a dense road
network helps the attacker, because a slowly drifting spoofed position keeps
landing on *some* road. That is what took the truck floor from 1 m/s to 2 when
the map became real. A different area has a different density and will produce
different numbers — possibly better, possibly worse, definitely not the same.

So this is not a cosmetic swap. **It is a re-measurement of the truck half of
the product.** Budget for step 5, not for step 3.

---

## Step 0 · Safety

The known-good build is tagged. Do not lose it.

```
tag    demo-ready              annotated, pushed
branch demo-ready-backup       pushed
```

Work on a new branch off `main`. If step 5 fails, `git checkout demo-ready` and
the demo is exactly as it was.

---

## Step 1 · Probe the area before committing to it

OSM covers everywhere — that was the user's question and the answer is yes.
The real question is whether *this particular box* has the three things the
method needs. Check before writing anything.

**Fetch the extract.** Overpass, no key. The main mirror was blocked from this
machine; `overpass.private.coffee` worked. Roughly the box shown in the
screenshot:

```
12.805, 80.115, 12.870, 80.205
```

**Then check three things, in this order:**

**1. A through-road that chains.** `bake_map.build_route()` needs one named
road running the length of the box. Sriperumbudur had `NH48`. Here it is
probably the Vandalur–Kelambakkam road — **verify the exact OSM `name`/`ref`
string, do not assume it.** OSM splits one carriageway into dozens of pieces;
`chain()` stitches them, but only if they share a name and actually touch
within 40 m.

**2. A real junction, near the middle.** The whole truck story is that a
container is diverted *after the turn-off*. A road that meets the through-road
within `JUNCTION_NEAR_M` (900 m) of the origin, and then runs at least 700 m
away from it. Without this there is no story, only a lorry going straight.

**3. Density.** Count the roads with `rank >= 2` inside the box.

| roads at rank ≥ 2 | expect |
|---|---|
| well under 105 | truck floor **improves**, detection faster — good for us |
| around 105 | numbers roughly hold |
| well over 105 | floor gets **worse**; a walk-off hides on a side street |

**This single count predicts most of step 5 and costs five minutes.** Do it
before anything else. If the campus area is a dense grid of service lanes and
approach roads, that is a reason to shift the box out toward the open corridor
rather than centring it on the gate.

---

## Step 2 · Decide what the story becomes

Sriperumbudur is a container-theft story: port run, highway, junction,
industrial estate, yard. It works because those places really are those things.

**Do not just move the pins and keep the script.** Either:

- **Keep it a logistics story** — if there is real freight geography in the box
  (SIPCOT Siruseri and the OMR estates are in that direction). Then the words
  in the run sheet barely change.
- **Or make it a campus-gate story** — "the road outside this building". More
  recognisable, but a stolen container is a weaker fit and the pitch needs a
  different sentence. Decide before writing, not after.

Whichever it is, `PLACES` in `chennai.py` has to name real places or the map
gets less convincing, not more.

---

## Step 3 · The code, file by file

Everything below is what is *currently hardcoded to Sriperumbudur*. This is the
complete list; it was grepped, not remembered.

| File | What is pinned there |
|---|---|
| [simulator/chennai.py:52-54](../../simulator/chennai.py#L52-L54) | `ORIGIN_LAT`, `ORIGIN_LON`, `ORIGIN_ALT` |
| [simulator/chennai.py:96-123](../../simulator/chennai.py#L96-L123) | `_FALLBACK_ROADS` — the drawn Sriperumbudur network |
| [simulator/chennai.py:128-146](../../simulator/chennai.py#L128-L146) | `_places()` — junction, yard, "to Chennai" |
| [simulator/bake_map.py:172-212](../../simulator/bake_map.py#L172-L212) | `build_route()` matches the literal string `"NH48"`, twice |
| [simulator/bake_map.py:42-52](../../simulator/bake_map.py#L42-L52) | `HALF_BOX_M` 2600, `JUNCTION_NEAR_M` 900 |
| [simulator/bake_map.py:223-227](../../simulator/bake_map.py#L223-L227) | 1200 m approach, 2200 m leg — tuned to make a 3-minute run |
| [simulator/scenarios.py:291-348](../../simulator/scenarios.py#L291-L348) | docstrings naming NH-48 and Sriperumbudur |
| [console/drive.js:27](../../console/drive.js#L27) | the button reads *"the Sriperumbudur delivery"* |
| [simulator/roads.py:21-27](../../simulator/roads.py#L21-L27) | comments still say Coimbatore — stale already |
| `simulator/chennai_map.json` | the baked file itself, regenerated |

**`build_route()` is the only one that needs real work.** Give it the highway
name as an argument instead of the literal `"NH48"`; everything else is a
value change.

### Two traps found while reading, both worth fixing here

**1. The fallback silently shows the wrong city.** If `chennai_map.json` fails
to load for any reason, `chennai.py` falls back to `_FALLBACK_ROADS` — the
hand-drawn Sriperumbudur network — and *nothing errors*. You would stand in
front of judges talking about the road outside while the screen quietly draws
somewhere 60 km away. This is the same failure class as the stale-server bug
that cost us a rehearsal: it looks right and is wrong.

Either update the fallback to match the new corridor, or make it print a loud
warning on load. Do not leave it as it is.

**2. `sensors.py` hardcodes an altitude the map disagrees with.**

```python
# simulator/sensors.py:205
true_alt_asl = pos[2] + 412.0  # ORIGIN_ALT
```

`chennai.py` says `ORIGIN_ALT = 60.0`. That 412 is left over from the old
Coimbatore origin. It currently causes no false alarms — so the vertical checks
must be working differentially rather than absolutely — but it is a hardcode
labelled with the name of the constant it is ignoring, and the new origin will
be different again (the Kelambakkam area is near sea level, so on the order of
10–20 m, not 60 and certainly not 412).

**Make it read `ORIGIN_ALT` and re-run the drone vertical cases**, because if
anything *was* depending on the 412 the failure will be a vertical false alarm
— which is rule 3, the gate.

---

## Step 4 · Route length has to land near 1331 m

Not for tidiness. The camera, the inset, the scenario durations and the truck
timings are all built around a run of about that length at half true scale. A
route half as long ends before the attack is named; one twice as long never
reaches the turn-off.

`cut()`'s 1200 m and 2200 m are the knobs. Adjust them until
`python -m simulator.bake_map <extract>` prints a route within roughly
±300 m of 1331.

---

## Step 5 · The gate — this is the actual work

**Nothing merges until all of these pass on the new map.**

```bash
python -m tests.run_all            # 66 tests — one asserts the route stays on a road
python -m harness.usecases         # 17/17
python -m harness.sweep            # false alarms must be ZERO — rule 3
python -m harness.blame_check      # 8/8
python -m harness.classify_check   # 7/8 or better
python -m harness.results --save   # the card, with the new numbers
python -m harness.dressrehearsal   # 10/10 beats
```

Kill stale processes first, every time:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'detector\.server|simulator\.control' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

**Then look at the map by eye**, at follow zoom and at whole-run. Roads are
drawn to scale in metres; a corridor with different road widths can look wrong
in ways no test can see. Confirm the lorry sits in a lane and a spoofed
position is visibly off the carriageway.

**Expect the numbers to change and write down the new ones honestly.** If the
truck floor moves, say why — the density count from step 1 should already
predict it. A number that moves for a reason you cannot explain has not moved;
it has broken.

---

## Step 6 · The words

Numbers appear in prose in five places and they must not disagree with the
card:

- [CLAUDE.md](../../CLAUDE.md) — the status table, the "truck floor moved"
  paragraph, **and the decision-table row "Where the demo drives"**, which
  currently records Sriperumbudur as settled. Rule: fix it in the same commit
  that makes it untrue.
- [docs/RUN-SHEET.md](../RUN-SHEET.md) — scenes 2, 4 and 5 name the roads out
  loud
- [docs/SCENARIOS.md](../SCENARIOS.md), [docs/MANUAL-TESTS.md](../MANUAL-TESTS.md)
- [console/drive.js:27](../../console/drive.js#L27) — the button label

---

## Rollback

If step 5 does not pass and time is short, this is a one-line decision:

```bash
git checkout demo-ready
```

The Sriperumbudur build is measured, rehearsed and known good. **A recognisable
map that alerts on an honest delivery is worth less than an unfamiliar map that
never does.** Rule 3 is a gate, not a goal — and it applies to this task like
everything else.

---

## Open questions — answer before step 1

1. **Which box?** Centred on the campus gate, or shifted onto the open corridor
   where the road network is sparser and detection is better?
2. **Which story** — freight, or the road outside the hall (step 2)?
3. **Is the demo already past?** If it is imminent, this task should not start;
   it is a day's work with a re-measurement in the middle, not an evening's.
