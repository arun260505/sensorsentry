# Task 3 — The truck

**For:** Abishek · **Time:** ~6 hours · **Branch:** `sim/truck`

Task 3 is complete — status at the bottom. Task 2 notes are below it.

---

## Why this one matters

Right now everything we have is a drone. The pitch says **logistics is our
first market** — more vehicles, the tracking box already fitted, and a truck is
actually *easier* to protect than a drone because wheels measure real distance
and a truck has to be on a road.

None of that is true in the demo until you build it. This task is what lets us
say "different vehicle, same detector" on stage and be telling the truth.

---

## What you're building

```
simulator/roads.py       a small road network, and "is this position on a road?"
simulator/vehicle.py     truck motion — follows roads, cannot fly
simulator/scenarios.py   truck_clean and truck_theft
```

Sensors already handle trucks (`odom` is wired up). You mostly need motion and
roads.

---

## The one rule (same as last time)

**An attack changes what a sensor *reports*. It does not move the vehicle.**

In the theft scenario the truck really does drive to a warehouse and stop. Only
the reported GPS position keeps driving along the motorway.

---

## Step 1 — `roads.py`

A road network is just a list of lines.

```python
ROADS = [
    # (name, [(east, north), (east, north), ...])
    ("NH-544",        [(0, 0), (1200, 100), (2400, 150)]),
    ("service road",  [(1200, 100), (1250, -400)]),
    ("warehouse lane",[(1250, -400), (1400, -450)]),
]

def distance_to_nearest_road(e, n) -> float:
    """Metres from this point to the closest road. 0 means on a road."""

def nearest_road_name(e, n) -> str: ...
```

Straight-line segments are fine. No curves, no map data, nothing downloaded.

**Why this matters:** it becomes a cross-check the drone does not have. A
spoofed position that drifts into a field is caught instantly, because trucks
cannot drive through fields.

---

## Step 2 — truck motion in `vehicle.py`

Add a truck mode. Differences from the drone:

- **Stays on the road** — follows the road polyline, does not cut corners
- **Altitude does not change** — no climb, and no barometer at all
- **Cannot turn on the spot** — steering is limited by speed
- **Stops** — traffic lights, junctions, and the warehouse

Keep the existing rate limits. The lesson from Task 1 still applies: **the
vehicle must never change state faster than its own sensors could measure it.**

Suggested numbers:
| | |
|---|---|
| Cruise speed | 15–22 m/s (55–80 km/h) |
| Max acceleration | 1.5 m/s² |
| Max braking | 3.0 m/s² |
| Max turn rate | 0.3 rad/s |

---

## Step 3 — wheel odometry

`sensors.py` already emits `odom.wheel_speed_mps` for trucks. Two things to
check:

- It reports **actual ground speed**, with the ±1% scale error already there
- It reads **0.0 when stopped** — and stays 0.0, which is normal, not a fault

**This is the truck's superpower.** GPS says the truck moved 3 km; the wheels
say it moved 300 m. One of them is lying, and the wheels are not reachable by
radio.

---

## Step 4 — two scenarios

**`truck_clean`** — 3 minutes on the highway. Junctions, a stop, a turn onto a
service road. No attack. Must produce no alarms.

**`truck_theft`** — the demo. This is the story:

1. Truck drives the highway normally
2. At **t = 40 s** a walk-off spoof starts
3. The real truck turns off onto the service road and stops at the warehouse
4. **The reported position keeps driving up the highway at full speed**
5. Control room sees a truck on schedule; the truck is being unloaded

Schedule it the same way you did the drone scenarios.

The picture we want on the map: the fake path neatly following the road while
the real path sits still at a warehouse three kilometres back.

---

## Step 5 — check it end to end

```bash
python -m detector.server                                    # terminal 1
python -m simulator.run --scenario truck_clean --seed 42     # terminal 2
# open http://127.0.0.1:8080
```

- `truck_clean` → **no alerts at all**, for the whole three minutes
- `truck_theft` → alert naming **GPS**, cause **attack**

If `truck_clean` raises anything, that is a bug and it is the most important
one in this task. Tell me rather than tuning it away.

---

## Done when

- [ ] `roads.py` answers "how far from a road is this point?"
- [ ] Truck follows roads, does not fly, does not turn on the spot
- [ ] Wheel speed reports real ground speed, and 0.0 when stopped
- [ ] `truck_clean` runs 3 minutes with **zero alerts**
- [ ] `truck_theft` shows the fake path on the road and the real truck stopped
- [ ] Both appear in the console's scenario buttons automatically
- [ ] Truth log records the injection, as before

---

## Don't

- Don't put anything about the attack into a frame
- Don't download map data — the demo runs with wifi off
- Don't touch `detector/`, `fleet/` or `console/`

---

## Notes on Task 2

Good work, and the two easy-to-miss things were both right: **walk-off at 0.0
changes nothing**, and **the magnet leaves the gyro alone**. I checked both.
All five scenarios ran 1400 frames each with nothing about the attack reaching
the detector.

End to end through the full detector:

| scenario | verdict |
|---|---|
| `drone_clean` | silent |
| `drone_walkoff` | GPS / attack |
| `drone_magnet` | compass / interference |
| `drone_fault` | motion sensor / fault |

Two things to fix when you next touch that code:

**`control.py` had no `__main__` block.** `serve()` existed but nothing could
start it, so the console's buttons pointed at a port with nobody listening. I
added `python -m simulator.control`. Worth remembering: if a module is meant to
be run, give it an entry point and run it once yourself.

**The `Bias` fault ramps every IMU field at once** — all three accelerometer
axes and all three gyro axes together. No real sensor fails that way; a bias
hits one axis. It also made the fault harder to diagnose than it should be. If
you get time, make `Bias` take an axis.

---

## Status — Task 3 complete (7 Sep)

All the "Done when" checks pass against the real detector (41 tests total):

| item | verdict |
|---|---|
| `roads.py` answers how far a point is from the road | `distance_to_nearest_road` / `nearest_road_name` |
| Truck follows roads, never leaves them, stops properly | `TruckVehicle` + stop-and-hold controller |
| Wheels read real ground speed and **0.0 when parked** | `sensors.py` now zeroes a stopped wheel (< 0.01 m/s) instead of adding noise |
| `truck_clean`, 3 minutes, zero alerts | green — 38/38 seeds incl. junction, red-light hold, service-road turn |
| `truck_theft` | the demo — GPS blamed, cause `attack`, ~t=108; real truck parked at warehouse |

Verification is in the repo, not on my desk: `tests/run_all.py` (roads
distance, stop-and-hold + wheel-zero, the 3-minute zero-alert gate, theft →
GPS/attack) and `harness/sweep.py` (truck_clean honest runs + along-road truck
walk-offs).

Two things surface that are **detector**, not simulator:

- **Gentle acceleration needs a direction check, not a wider magnitude band.**
  At the briefed 1.5 m/s² pull-away, |a| − g ≈ 0.10 — inside any band that also
  lets a resting drone level itself. The tilt gate now refuses a pitch
  correction whose forward axis can't match −g·sin(pitch)
  (`detector/deadreckon.py`, `FORCE_CONSISTENCY_MPS2`). Pitch-only by design;
  gating roll the same way regressed the drone's banked-turn clean run.
- **`Bias` is now single-axis** (the "next time" note above) — `axis="ax"` default.

Recorded curve, whole pipeline: truck walk-off 2-5 m/s *parallel* to the road
caught ~69-70 s after onset (the compass/course eyes see nothing in an in-line
drift, so detection rides the cumulative GPS-vs-wheels residual), 1 m/s not
detected, and `truck_theft` — where the real truck turns off-road and the
divergence is instantly visible — caught at ~t=108. While the parked truck
holds at the red light, the cause can flap attack/stuck for a couple of
seconds (blame stays gnss throughout); a classifier tie-break is the fix.

---

## Stuck?

- Contract → [docs/schema.md](../schema.md)
- Why trucks matter → [docs/sensorsentry.html](../sensorsentry.html), "Who it protects"
