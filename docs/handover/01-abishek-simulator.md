# Task 1 — Simulator: clean flight

**Assigned to:** Abishek
**Estimated:** ~6 hours
**Blocks:** nobody — build this at your own pace, in your own branch
**Depends on:** nothing except [docs/schema.md](../schema.md)

---

## Context — 60 seconds

We're building **SensorSentry** for PS 18. Short version:

Someone with a cheap radio can fake the GPS signal a drone or truck receives, so
the vehicle believes a completely wrong location and has no idea. Our detector
catches that by cross-checking the GPS position against the vehicle's *other*
sensors — motion, altitude, compass, wheels. The attacker can fake the radio.
He can't fake what the vehicle physically feels.

**Your job is the simulator** — the fake vehicle that produces the sensor
readings our detector will consume. It is a completely separate program from
the detector, and it stays separate. That separation is what proves to judges
our demo isn't hardcoded.

If you want the whole picture, read
[docs/sensorsentry.html](../sensorsentry.html) — but you don't need it to do
this task.

---

## What you're building

A program that flies a scripted route and emits realistic sensor readings over
UDP, 20 times a second.

**This task is the clean flight only.** No attacks, no faults — those are Task 2.
Get an honest, well-behaved vehicle working first.

### Files

```
simulator/
  vehicle.py      motion along a route — position, velocity, attitude
  sensors.py      turn true motion into noisy sensor readings
  scenarios.py    named routes; for now: drone_clean, drone_manoeuvre
  publisher.py    build the JSON frame, send UDP
  run.py          entry point: python -m simulator.run --scenario drone_clean
```

Python 3, **NumPy only**. No other dependencies — we can't risk install
problems on demo day.

---

## The output contract

Read [docs/schema.md](../schema.md) properly before you start. It is frozen —
three people are building against it in parallel, so if something in it seems
wrong, message the group rather than changing it yourself.

The two things people get wrong:

1. **`gnss` is `null` on 3 out of every 4 frames.** Frames go out at 20 Hz but
   GPS only updates at 5 Hz. That gap is real and it matters to our detection,
   so don't smooth it over by repeating the last fix.
2. **Send the `run_start` header once** before the frames, with a fresh random
   seed each run.

### The rule that matters most

**Never put the true position, or anything about attacks, into a frame.**

No `true_lat`, no `attack_active`, nothing. Not even temporarily for debugging.
The detector must be structurally incapable of cheating — that's the whole
reason judges will believe our demo. If you need to inspect truth while
developing, write it to a local log file on the simulator side.

---

## How the vehicle should move

**`drone_clean`** — a 3-minute route with:
- straight cruise segments
- a few gentle turns
- one climb and one descent
- a hover / slow section

**`drone_manoeuvre`** — same length, but deliberately aggressive:
- hard banking turns
- rapid climb, rapid descent
- sharp accelerations and braking

That second one matters more than it looks. It's the scenario we use to prove
our detector *doesn't* raise false alarms during normal hard flying — which is
the first thing judges ask about. Make it genuinely violent.

Work in **local metres (ENU — east, north, up)** internally. Convert to
lat/lon only when filling in the GNSS field. Pick any origin, e.g. Coimbatore
at `11.0168, 76.9558`.

---

## Sensor models — use these numbers

These are realistic consumer/commercial-grade values. Start here, adjust only
if something looks obviously wrong.

### GNSS — 5 Hz
| | |
|---|---|
| Horizontal position noise | Gaussian, σ = 1.5 m per axis |
| Altitude noise | Gaussian, σ = 3.0 m |
| `sats` | random 8–12, changing slowly |
| `hdop` | 0.8–1.5, changing slowly |
| `cn0_mean` | 38–45 dB-Hz, small random walk |
| `fix` | always 3 for now |

### IMU — 20 Hz, the important one
| | |
|---|---|
| Accelerometer white noise | σ = 0.02 m/s² per axis |
| Accelerometer bias | initial ±0.05 m/s², random walk 0.0005 m/s²/√s |
| Gyroscope white noise | σ = 0.002 rad/s per axis |
| Gyroscope bias | initial ±0.002 rad/s, random walk 0.00005 rad/s/√s |
| Gravity | include it — `az ≈ +9.79` at rest, in body frame |

### Barometer — 20 Hz
| | |
|---|---|
| Pressure noise | σ = 0.08 hPa (≈ 0.7 m) |
| Slow drift | ±0.3 hPa over several minutes |

Use the standard barometric formula to convert altitude to pressure.

### Magnetometer — 20 Hz
| | |
|---|---|
| Heading noise | σ = 1.5° |
| Fixed offset | random ±2° per run, constant within a run |

### Odometry — trucks only, `null` for drones
| | |
|---|---|
| Wheel speed noise | σ = 0.05 m/s |
| Scale error | ±1% per run, constant within a run |

---

## The one quality bar that really matters

**The IMU noise must be honest**, because it decides how good our whole system
can be. Too clean and our detector looks unrealistically good — judges will
smell it. Too noisy and we can't detect anything.

**Required self-check:** write a small throwaway script that takes your IMU
output and integrates it into a position with no GPS at all. Run for 60
seconds of simulated time and measure how far that drifts from the true path.

**Target: roughly 20–120 metres of drift after 60 seconds.**

- Much less than 20 m → your IMU is unrealistically perfect, increase the bias
  random walk
- Much more than 120 m → too noisy, we won't be able to detect slow attacks

Run it a few times — the seed changes each run so you'll get a spread. Tell us
the numbers you get when you hand this back. This one measurement genuinely
sets the performance ceiling of the whole project.

---

## Done when

- [ ] `python -m simulator.run --scenario drone_clean` runs for 3 minutes
- [ ] Frames arrive on UDP 5005 at 20 Hz, `seq` incrementing with no gaps
- [ ] `run_start` header is sent first, with a **new seed each run**
- [ ] `gnss` is `null` on 3 of every 4 frames
- [ ] `drone_manoeuvre` scenario exists and is genuinely aggressive
- [ ] A frame printed to screen matches [docs/schema.md](../schema.md) exactly
- [ ] **No truth or attack information anywhere in any frame**
- [ ] Dead-reckoning drift self-check lands in the 20–120 m band, and you've
      written down the numbers

---

## Please don't

- **Don't build attacks or faults yet.** That's Task 2 and it depends on this
  being solid.
- **Don't perfect the flight physics.** It only has to be plausible. Aerodynamics
  is the classic way this project loses two days — if it looks like a vehicle
  moving sensibly, it's good enough.
- **Don't add fields to the schema.** Message the group instead.
- **Don't touch `detector/`, `fleet/` or `console/`.** Other people are in there.

---

## Handing back

```bash
git checkout -b sim/clean-flight
# ... work ...
git push -u origin sim/clean-flight
```

Open a PR and in the description include:

1. Your dead-reckoning drift numbers from the self-check
2. One printed sample frame
3. Anything in the schema that fought you

Then message the group. **Task 2 is attack and fault injection** — walk-off
spoofing, teleport, stuck sensors, the magnet case — which plugs straight into
what you've built here.

---

## If you get stuck

- Contract questions → [docs/schema.md](../schema.md)
- Where this fits → [PLAN.md](../../PLAN.md), Phase 1
- Why any of it → [docs/sensorsentry.html](../sensorsentry.html)

Ask early rather than guessing on the schema — it's the one thing that
breaks other people's work.
