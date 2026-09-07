# Task 2 — Attacks, faults and interference

**Assigned to:** Abishek
**Estimated:** ~8 hours
**Depends on:** your Task 1 (merged, with fixes — read the note at the bottom)
**Blocks:** our phases 5 and 6, so this one *is* on the critical path

---

## First, what happened to Task 1

Your simulator was reviewed by running it into the detector. Structure, CLI and
the drift self-check were all good, and you were right to push back on the gyro
bias figure in my handover — 0.002 rad/s is 412 deg/hr and real commercial IMUs
are far better than that. Good catch.

One real bug, worth understanding because it shapes this task:

`sensors.py` reported `roll_rate = pitch_rate = 0` while the vehicle banked to
30 degrees. So the accelerometer said "gravity is tilted 30 degrees" and the
gyro said "we never rotated." Both cannot be true. A detector integrates the
gyro, holds its attitude level, and then reads that tilted gravity as forward
thrust — **2.8 m/s² of acceleration that never happened.** A clean flight with
no attack at all pushed the detector 2 km off and raised a full alarm.

Your `check_drift.py` could not catch it because it integrates using the
vehicle's *true* attitude, which no real detector ever has.

**The rule this gives us, and it matters for every injector below:** every
sensor must describe the *same* physical situation. If one sensor says the
vehicle did something, the others must agree — unless you are deliberately
injecting a fault, and then exactly one of them should disagree, in exactly the
way that fault would cause.

Fixes are on your branch (`6e2f491`). Worth reading the diff.

Also: `truth.jsonl` got committed, under the message "Your commit message". I
removed it and gitignored it. Generated output stays out of git, and commit
messages should say what changed.

---

## What you're building

The attack and fault library, plus the control endpoint that lets the console
drive it. This is what turns a flight into a demo.

```
simulator/
  attacks.py     GPS spoofing: walk-off, teleport, altitude-only, replay
  faults.py      broken sensors: stuck, noisy, dropout, slow bias
  interference.py  magnet near the compass, pressure near the barometer
  control.py     HTTP :5010 — start, reset, inject, scenarios
  scenarios.py   add the six demo scenarios
```

---

## The one idea that makes all of this correct

**An attack changes what a sensor *reports*. It does not change where the
vehicle actually is.**

A spoofer transmits a fake radio signal. The drone's motors, its inertia, its
compass and its barometer carry on exactly as before — the vehicle keeps flying
its real path through real air. Only the number coming out of the GPS receiver
is a lie.

So every injector in this task is a filter applied to the **sensor output**,
after `sensors.py` has produced an honest reading. None of them may touch
`vehicle.py`. If you find yourself modifying the vehicle's position to make an
attack work, the design has gone wrong.

(One day the autopilot would react to the fake position and fly off course.
Not yet — that is a later phase. For now the vehicle flies its route and only
the reported position lies.)

---

## The injectors

Each takes parameters, applies to a specific sensor, and can start at a chosen
moment mid-run.

### attacks.py — someone is lying to the receiver

| Attack | What it does | Parameters |
|---|---|---|
| `walkoff` | Reported position pulled steadily along a bearing. Every individual fix looks reasonable; only the accumulation gives it away. **This is the core demo.** | `speed_mps`, `bearing_deg` |
| `teleport` | Position jumps to an offset in a single sample | `offset_m`, `bearing_deg` |
| `altitude_only` | Horizontal position honest, altitude forced. The classic geofence-ceiling defeat | `offset_m` or `rate_mps` |
| `replay` | Position from a different place, replayed. Position is internally consistent but wrong, and the receiver clock goes stale | `source_offset_m`, `clock_lag_s` |

For `walkoff`, offset at time *t* is `speed_mps × (t − start_t)` along the
bearing. Make `speed_mps` adjustable from **0.0 to 5.0** — we turn it down live
on stage until the detector fails, and show the judges exactly where that is.
At 0.0 nothing should happen at all.

A real spoofer also arrives louder and more uniformly than a real constellation,
so while any GPS attack is active also nudge `cn0_mean` up by a few dB-Hz and
tighten `hdop` slightly. Small effects, but they are a second independent
signature and cost you three lines.

### faults.py — nothing is attacking, the hardware is failing

| Fault | What it does | Parameters |
|---|---|---|
| `stuck` | Sensor freezes on its last value | `sensor` |
| `noisy` | Noise inflated well past the rated figure | `sensor`, `multiplier` |
| `dropout` | Sensor emits `null` | `sensor`, `duty_cycle` |
| `bias` | Slow ramp added to a sensor's reading | `sensor`, `rate_per_s` |

Any of `gnss`, `imu`, `baro`, `mag`, `odom`.

**These must look different from an attack, and the difference must be real,
not cosmetic.** A failing sensor is erratic and incoherent. An attack is smooth
and purposeful, because the attacker wants the vehicle somewhere specific.
Phase 6 separates them on exactly that, so please do not make your faults
tidy and directional — a `bias` fault that ramps smoothly in one direction is
genuinely ambiguous, which is fine and honest, but `noisy` and `stuck` should
be visibly messy.

### interference.py — the environment itself is manipulated

This is the third thing PS 18 asks for and the one most teams will skip.

| Injector | What it does | Parameters |
|---|---|---|
| `magnet` | Adds an offset to the compass heading | `offset_deg` |
| `pressure` | Adds an offset to barometric pressure | `offset_hpa` |

The signature that makes `magnet` detectable is worth stating explicitly,
because your job is to produce it faithfully: **the compass swings while the
gyroscope reports no rotation.** The vehicle did not turn — the magnetic field
around it changed. So `magnet` must affect `mag.heading_deg` *only*, and leave
`imu.gz` completely untouched. If you also nudge the gyro, the signature
disappears and the whole case becomes undetectable.

---

## control.py — the endpoint the console drives

Already specified in **[docs/schema.md](../schema.md) section 4**. Build to it
exactly; the console is already calling it.

HTTP on port **5010**:

| Method | Path | Body | Does |
|---|---|---|---|
| `POST` | `/start` | `{"scenario": "drone_clean"}` | Begin a run |
| `POST` | `/reset` | — | Stop and clear |
| `POST` | `/inject` | `{"kind","type","strength","bearing_deg"}` | Start an injector mid-run |
| `GET` | `/scenarios` | — | List scenario names |

Requirements that come straight from the demo:

- **Reset must return to a clean state in under two seconds, from any state.**
  Judges interrupt, and ask to see things again.
- **Injection takes effect on the next frame**, not the next run.
- **Scenario switching must not need a restart.**
- The standard library's `http.server` is fine. NumPy is still the only
  dependency.

---

## scenarios.py — the six demo scenarios

These are what the judges pick from, so the names appear on screen:

| Name | What it is |
|---|---|
| `drone_clean` | Already built. No attack. |
| `drone_manoeuvre` | Already built. Hard flying, no attack. |
| `drone_walkoff` | Clean route, walk-off spoof starting partway |
| `drone_fault` | Clean route, a sensor genuinely fails |
| `drone_magnet` | Clean route, magnet near the compass |
| `truck_theft` | Truck profile — comes with Task 3, leave a stub |

Scenarios should compose a route with a scheduled injection, so that pressing
Start runs the whole story without anyone touching a slider. The manual
`/inject` path stays available for driving it live.

---

## Recording truth (important for later)

Extend `--truth-log` to also record **when each injection started, what type,
and with what parameters.**

Simulator side only, never in a frame. We need it in phase 11 to measure
detection latency — "caught 14 seconds after onset" is a number we can only
produce if something wrote down the onset. That measurement is what fills the
results card the demo closes on.

---

## Done when

- [ ] All four attacks work and are parameterised
- [ ] `walkoff` at `speed_mps=0.0` produces **no change whatsoever**
- [ ] All four faults work on any named sensor
- [ ] `magnet` shifts the compass and leaves the gyro **completely untouched**
- [ ] `/start`, `/reset`, `/inject`, `/scenarios` all work
- [ ] Reset returns to clean state in under two seconds
- [ ] Injection takes effect on the next frame
- [ ] The five drone scenarios run start to finish
- [ ] Truth log records injection onset, type and parameters
- [ ] **A clean run still finishes with zero alerts** — check this last, every time

---

## Please don't

- **Don't touch `vehicle.py` to make an attack work.** Attacks change reported
  readings, never the vehicle's real motion.
- **Don't leak attack state into a frame.** Not `attack_active`, not
  `spoof_offset`, not a suspicious extra field. The detector rejects unknown
  fields loudly, so you will find out immediately — but the reason is that this
  is the one thing that would make our demo dishonest.
- **Don't make faults look like attacks.** Phase 6 has to tell them apart, and
  if your fault is a smooth directional ramp there is nothing to tell apart.
- **Don't touch `detector/`, `fleet/` or `console/`.**

---

## Handing back

Same branch flow. In the PR description include:

1. One frame from during a `walkoff`, and one from a clean run, so we can see
   they are indistinguishable field-by-field
2. Confirmation that `walkoff` at 0.0 changes nothing
3. Timing for `/reset`

Then message the group. **Task 3 is the truck profile** — wheel odometry, road
constraint, and the cargo-theft scenario.

---

## If you get stuck

- Contract → [docs/schema.md](../schema.md), section 4 for the control endpoint
- Why each attack matters → [docs/sensorsentry.html](../sensorsentry.html),
  the attack library table
- What the demo does with all this →
  [docs/sensorsentry-demo-playbook.html](../sensorsentry-demo-playbook.html)

The demo playbook is worth twenty minutes of your time before you start. It
shows exactly how these injectors get used on stage, including the moment we
turn the attack strength down until the detector fails and tell the judges
that is where our limit is. Building for that moment will make better choices
about parameter ranges than any spec I could write.
