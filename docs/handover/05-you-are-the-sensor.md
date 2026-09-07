# Task 5 — You are the sensor

**For:** simulator + console · **Time:** ~6 hours · **Branch:** `main`

---

## The idea

Task 4 let a judge *nudge* a real sensor. This one hands them the sensor
outright.

Press **Start**. The truck pulls away and drives its real route — the actual
Sriperumbudur to Oragadam delivery, exactly as before. But the GPS no longer
follows it. **The GPS is now the judge.** Whatever they press, the GPS reports.
Press up, the reported position goes north. Press nothing, it sits exactly
where it is while the truck drives away from it.

Then the system has to work out, on its own, what it is looking at.

## Why this is the better demo

The old flow asked a judge to believe that a button labelled `truck · theft`
was not a video. This one cannot be a video, because they are holding the
controller.

And it makes the hardest claim in the pitch visible in about fifteen seconds.
The problem statement names three failures — spoofing, false readings,
manipulated inputs — and says most teams will only cover one. Here a judge
produces two of them **with the same control**, and the difference between
them is nothing but how they press the keys:

| What the judge does | What it is | Why |
|---|---|---|
| Drives the GPS away while the truck goes straight | **tampered** | The position is moving in a way the vehicle never moved. Something is inventing it. |
| Lets go — GPS sits frozen while the truck drives on | **failed** | The position is not moving at all. Nothing is inventing it; the sensor has simply stopped. |

Same sensor, same screen, opposite verdicts, opposite responses — and the
judge caused both deliberately. That is the entire "attack or fault?" argument
delivered in one interaction instead of one paragraph.

> "You just did both. You spoofed it, then you broke it. It told you which was
> which, and it never saw your keyboard."

## The rule this must not break

**The detector is never told any of this.** Not which sensor was taken over,
not when, not what the judge pressed. It receives ordinary sensor frames on
the ordinary socket and has no field that could carry the information. The
verdict has to come out of the physics or it is worth nothing.

This is rule 2 of the project. It is also, conveniently, the whole trick: a
judge who knows we could not have cheated does not need to be persuaded.

---

## What you're building

```
simulator/puppet.py      sensors the operator drives instead of the vehicle
simulator/control.py     take-over and steering endpoints for them
console/index.html       the sensor buttons and the verdict readout
console/app.js           arrows -> whichever sensors are taken over
```

**No detector change.** If this needs one, something is wrong: taking a sensor
over produces ordinary bad readings, and detecting ordinary bad readings is
what the ten stages already do. If a case is missed, that is a real finding
about the detector, not a licence to teach it about puppets.

## Step 1 — The puppets

One class per sensor. Each replaces its sensor's output entirely rather than
offsetting it, and each starts from the real reading at the moment of
take-over so there is no give-away jump.

| Class | Drives | Arrows |
|---|---|---|
| `GnssPuppet` | `gnss.lat/lon` | direction of travel; speed from the slider |
| `MagPuppet` | `mag.heading_deg` | left/right turn the heading |
| `BaroPuppet` | `baro.pressure_hpa` | up/down raise and lower apparent height |
| `OdomPuppet` | `odom.wheel_speed_mps` | up/down faster and slower |

Two details that carry the whole demo:

**Frozen must be exactly frozen.** When nothing is pressed, `GnssPuppet` must
return byte-identical lat/lon every frame — not a value that jitters in the
sixth decimal. `health.py` calls GNSS stuck when the position does not move at
all for five fixes, because real receivers dither even at a standstill. That
check is what turns "judge let go" into "the sensor failed", so a puppet that
quietly wobbles breaks the fault case.

**Take-over must be seamless.** Initialise from the last real reading. A
puppet that starts at the origin teleports the vehicle across the map, which
is detected instantly as an attack and proves nothing.

## Step 2 — Holding several at once

The judge can take over more than one. Slots already exist from task 4, so
`puppet:gnss` and `puppet:mag` coexist — but the arrows drive whichever is
**selected**, not everything held. Selecting is not releasing.

Expect a two-sensor take-over to come back `cannot_isolate`. That is correct
and it is worth saying out loud rather than hiding: with two of five sensors
lying in agreement, the honest ones are outvoted. It is the same reason the
fleet check exists — one vehicle can be fooled, four cannot.

## Step 3 — The readout

Above the controls, in plain words, what the detector currently believes:

```
    reading you as:   TAMPERED          gps
    reading you as:   FAILED SENSOR     gps
    reading you as:   INTERFERENCE      compass
    reading you as:   not sure yet      — two sensors disagree
```

Map from the verdict already on the stream: cause `attack` → TAMPERED,
`fault` → FAILED SENSOR, `interference` → INTERFERENCE, and anything
unclassified or `cannot_isolate` → *not sure yet*, which stays on screen
rather than being hidden. Refusing to answer is a feature here; a judge who
sees it appear and then resolve has watched the system decline to guess.

## Step 4 — Demote the scripted scenarios

The row of pre-named buttons along the bottom is the thing that made the demo
look canned, so it stops being the way in. **Start** becomes two buttons —
truck or drone — and the scripted attack runs move into a small "scripted
runs" group, out of the way.

Keep them. They are the fallback if a laptop misbehaves in the room, and
`harness/results.py` still measures against them. They just stop being the
first thing a judge sees.

---

## Done when

- [ ] Start runs the vehicle; the judge takes the GPS and drives it with arrows
- [ ] Driving it away from a straight-running truck reads **TAMPERED**
- [ ] Letting it sit frozen while the truck drives on reads **FAILED SENSOR**
- [ ] Compass, barometer and wheels can all be taken over the same way
- [ ] Several can be held at once; the arrows drive the selected one
- [ ] Take-over is seamless — no jump at the moment it is taken
- [ ] Not one line of `detector/` changed
- [ ] `python -m tests.run_all` still 64/64, zero false alarms

## Open questions — ping me rather than guessing

- Should the scripted runs stay reachable at all, or be removed completely?
  Written above as "demoted, kept as fallback", which is the safer call.
- Wheels and barometer are the least interesting to drive. If time runs short,
  GPS and compass are the two that matter and the other two can wait.
