# SensorSentry — project context

Read this first, every session. It exists so nobody — human or AI — drifts off
the plan, re-argues a settled decision, or forgets a rule that the whole demo
depends on.

Keep it short. If something here stops being true, **fix it in the same commit
that made it untrue.**

---

## What we're building

**PS 18 — Spoofing Detection & Sensor Fusion.**

> Create an intelligent sensor-fusion system that cross-validates simulated
> sensor data to detect GPS spoofing, false readings, or manipulated
> environmental inputs.

Note the PS names **three** problems, not one: spoofing (an attacker fakes the
signal), false readings (a sensor is simply failing), and manipulated
environmental inputs (an attacker changes what a sensor physically measures,
e.g. a magnet near the compass). We cover all three. Most teams will cover one.

### The idea in one line

> An attacker can fake the radio signal a vehicle receives.
> **He cannot fake what the vehicle physically feels.**

GPS arrives from far away, below the noise floor — a cheap ground transmitter
overpowers it and hands a drone or truck a fake position it believes
completely. We cross-check that position against the vehicle's own independent
senses (inertial motion, barometric altitude, magnetic heading, wheel odometry,
road geometry). When GPS describes movements the vehicle never made, the
disagreement is the proof.

### What makes it ours

1. **No hardware.** Every existing defence is a special antenna per vehicle.
   Ours is software, installed into fleets already in service.
2. **Names the guilty sensor.** Not "something is wrong" — *"the compass is
   lying, and the other four agree that it is."*
3. **Attack or fault?** They look identical at first and demand opposite
   responses. We tell them apart. Nobody else does.
4. **The fleet checks itself.** One vehicle can be fooled; four hit at once in
   one area locates the attacker.

---

## Non-negotiable rules

**1. Simulator and detector are separate processes.** Always, from the first
commit. This is not tidiness — it is the structural proof our demo isn't
hardcoded.

**2. No truth crosses the socket.** No `true_lat`, no `attack_active`, nothing
— not even temporarily for debugging. The detector must be *incapable* of
cheating. Log truth to a file on the simulator side instead. The detector
rejects unknown fields loudly at ingest.

**3. Zero false alarms is a gate, not a goal.** The clean-manoeuvre run must
finish with no alerts after **every** change. If that number leaves zero, stop
and fix it before adding anything new.

**4. Demoable beats complete.** The two-path map ships before any clever
detection exists. After that point every phase improves a working demo instead
of gambling on one.

**5. The system is allowed to say "I don't know."** Two sensors in conflict →
`cannot_isolate`. Low confidence → `unclassified`. Never guess. Judges test
this exact case, and admitting uncertainty is more credible than a confident
wrong answer.

**6. NumPy only.** No other Python dependencies. No install surprises on demo
day, and "we wrote the maths" survives scrutiny.

---

## Architecture

```
simulator  --UDP:5005-->  detector  --WS:8080-->  console / phone
(test tool)               (product)                    |
     ^                        |                        |
     |                        +---> fleet service <----+
  HTTP:5010                         (WS:8081)
  (control)
```

The data contract for all of these is **[docs/schema.md](docs/schema.md)** and
it is **frozen** — three people build against it in parallel. If it seems
wrong, raise it with the team; don't edit it alone.

---

## Repo map

```
simulator/   vehicle + sensor models, attack/fault injectors, scenarios
detector/    the ten-stage pipeline — the actual product
fleet/       incident clustering, attack-zone estimation, advisories
console/     one responsive page: operator console and phone app
harness/     measurement sweeps, regression runs, results card
docs/        contract, briefs, requirements, plans, handovers
```

### The ten stages (detector/)

| # | File | Does |
|---|---|---|
| 1 | `ingest.py` | receive, time-align, spot dropped frames |
| 2 | `health.py` | each sensor checked on its own |
| 3 | `deadreckon.py` | GPS-free position estimate — the witness |
| 4 | `crossvalidate.py` | score every sensor pair against every other |
| 5 | `blame.py` | **★** which sensor disagrees with everyone |
| 6 | `classify.py` | **★** attack / fault / interference |
| 7 | `trust.py` | gradual scores with hysteresis |
| 8 | `fusion.py` | drop the liar, keep navigating |
| 9 | `evidence.py` | append-only incident record |
| 10 | `server.py` | WebSocket out to console |

**★ = the parts that make this project different. Protect them when time runs
short.**

---

## Status

**Current phase:** 8 — truck (Abishek). Detection, blame, classification and fallback all done.
**Last updated:** 7 September 2026

**Both starred stages are built.** The detector catches a spoof, a magnet or
a failing sensor, names the sensor, says whether it is an attack, a breakdown
or interference, and gives a different instruction for each. It says
`cannot_isolate` or `unclassified` rather than guessing.

    harness/sweep.py           zero false alarms; detection floor ~2 m/s
    harness/blame_check.py     8 of 8 - which sensor
    harness/classify_check.py  8 of 8 - attack / fault / interference

It now **acts** on the verdict: the lying sensor is dropped and the vehicle
keeps navigating on the rest, with an error budget that grows honestly and
tells the operator when to stop.

    harness/fallback_check.py  34 m from truth vs GPS's 91 m, budget honest

What remains: truck profile, fleet map, evidence, phone view.

| Owner | Area | State |
|---|---|---|
| Abishek | Simulator | task 1 done (reviewed, fixed) · **task 2 in progress** — attack injectors |
| — | Detector core | stages 1-8 done, 43 tests passing |
| — | Console | live — canvas map, two paths, raw feed |
| — | Blame (stage 5) | **done** — names the sensor, or says `cannot_isolate` |
| — | Classify (stage 6) | **done** - attack / fault / interference, or `unclassified` |
| — | Truck, fleet, evidence | not started — phases 8, 9, 10 |

Full checklist: **[PLAN.md](PLAN.md)**

Run it:

```bash
python -m detector.server                                 # terminal 1
python -m harness.send_fixture --spoof 2.5 --spoof-at 15  # terminal 2
# open http://127.0.0.1:8080

python -m tests.run_all                                   # 22 tests
python -m detector.run                                    # terminal-only version
```

Two things about the console that are deliberate and easy to undo by accident:

- **It draws its own map on a canvas — no tile server, no map library.** The
  demo runs with wifi off in front of judges; a map that silently fails to
  load would take the whole thing with it.
- **The raw-feed panel stays visible.** It is the anti-hardcoding proof: point
  at it and say *"that is everything the detector receives — show me the field
  that tells it an attack is happening."*

### Engineering findings — these cost real time, don't rediscover them

**-3. An error budget must be measured from the moment it starts counting, and
must never flatter itself.** The free-running budget was first fitted to drift
from the start of the run and started from zero, so it claimed 190 m while the
witness was 729 m out. An operator deciding whether to press on was being
handed a figure four times better than the truth. Fixed by measuring growth
from the freeze (quadratic, ~0.18 m/s^2) and starting from the error already
present when aiding stopped (~25 m).

**Dead reckoning buys about 40 seconds, not minutes.** Past that our own drift
overtakes even a 3 m/s spoof. That is not a defect to hide — it is why the
console counts down and then says "stop or land" rather than showing a number.


**-2. A reference must stop following a sensor the moment it becomes suspect.**
The gyro heading is slowly re-seeded from the compass so it cannot drift
without bound. Left running, that re-seeding quietly *absorbed* a magnet
offset: the magnet was correctly blamed for fifty seconds, the gyro caught up
with the corrupted compass, the two agreed again, and the accusation moved to
GPS - which was innocent. Same rule as freezing GNSS aiding under attack.

**Evidence must be allowed to be intermittent.** Requiring strictly continuous
evidence looked tidy and missed the most obvious fault there is: a compass
gone noisy dips back under the threshold between samples, resetting the timer
forever, so a sensor reading 17x normal raised nothing at all. The accumulator
now leaks at half speed instead of resetting.

**Heading rate is not the gyro's z reading.** Only a level vehicle turns about
its own z axis; banked over, part of the turn appears on y and the rest is
foreshortened by pitch. Integrating gz raw under-reads a 30-degree banked turn
by 13 percent, which accumulated to 171 degrees across a flight and read as a
failing compass on a healthy vehicle.

**Coherence alone cannot separate interference from a dying sensor** - both
give a smooth one-way error. Steadiness can: a magnet holds its offset, a
failing compass keeps changing how wrong it is.


**-1. A sensor is only cleared by a check that would have caught the fault.**
GNSS passing an altitude check says nothing about it lying horizontally, and
passing the position check says almost nothing about a slow walk-off. Blame
therefore works inside one *domain* — heading, horizontal, vertical — and only
same-domain evidence can provide an alibi. Allowing cross-domain alibis let
the real culprit walk free in every test.

**Two failing checks accuse; one only detects.** A single failing check names
two sensors and cannot choose between them, so it returns `cannot_isolate` —
which is the honest answer, not a gap. A walk-off is isolated because the
compass is *cleared* by still agreeing with the gyro; a magnet is isolated
because the compass fails everything it takes part in.

**Comparing GNSS course against gyro-integrated heading does not work.** It
looked like the obvious way to isolate GNSS without involving the compass, but
a gyro has no absolute reference: its heading accumulates scale error over
every turn and reads 51x normal on an honest manoeuvring flight, far worse
than any attack. Removed.


**0. Position integration cannot catch a slow walk-off, and no amount of
filter tuning changes that.** A constant half-degree pitch error — well inside
what a complementary filter leaves behind — leaks enough gravity to build a
**6 m/s velocity error inside a minute**. That swamps a 2 m/s attack whatever
you do to the uncertainty model. We tried position aiding, then alpha-beta
position-and-velocity aiding; the clean-run noise always came out as large as
the attack signal. This is a known limit of inertial-only spoofing detection.

**What works instead is comparing *direction*, not accumulated position.**
Pull a 12 m/s vehicle sideways at 2 m/s and its course over ground swings 9
degrees while the airframe still points where it pointed. The compass is good
to 1.5 degrees, and a radio attack cannot reach it. Measured: 2.4 degrees of
disagreement on honest flights against 10.6 degrees under a 2 m/s walk-off.
This is why the design has many sensor pairs and not one residual — see
`crossvalidate.py`.


**1. An accelerometer cannot tell tilting from accelerating, and getting the
gate wrong is unrecoverable.** Correcting attitude from gravity during
acceleration writes a false pitch, the gyro then faithfully preserves it,
gravity leaks into the forward axis, and the witness silently under-reads
speed forever after — it read 7.1 m/s on a 12 m/s vehicle. The gate must close
on the *worst* sample in the last second, not the average: an averaged gate
still opens at the start of a manoeuvre while the window is half full of the
stationary samples before it, which is enough to do the damage.

**2. Health checks must measure sample-to-sample noise, not raw spread, and
use a median.** Real motion is smooth so it barely shows between adjacent
samples; a failing sensor is not. And one genuine jump — a vehicle moving off
— makes a standard deviation declare the sensor faulty for two seconds. Median
absolute deviation ignores outliers by construction.

**3. Steady horizontal accelerometer bias is *rejected*, not integrated.** The
complementary filter absorbs it into a small pitch offset that cancels it. So
the textbook b·t²/2 drift bound does not apply to us — our uncertainty grows
roughly **linearly**, and the quadratic model was unusable: it claimed 80 m of
uncertainty while the witness was 12 m off, hiding a live 2 m/s attack.

### Measured detection curve

`python -m harness.sweep`, against the real simulator, whole pipeline, four
seeds per point. These are the numbers to put on the results card.

| | result |
|---|---|
| **False alarms**, 8 honest flights incl. hard manoeuvres | **zero** |
| GPS walk-off 5 m/s | caught 15-16 s after onset |
| GPS walk-off 3 m/s | caught 16-17 s after onset |
| GPS walk-off 2 m/s | caught 16-18 s after onset |
| GPS walk-off 1 m/s and below | **not detected** |
| Magnet on compass, 25 deg or more | caught 2 s after onset |
| Magnet on compass, 10 deg | caught 14 s after onset |

**Say the floor out loud on stage.** Below about 2 m/s a walk-off is slower
than our own inertial drift and we do not catch it. That is a property of the
IMU, not a bug — and at that speed an attacker needs about eight minutes to
move a vehicle a kilometre.

The 15-17 s latency is honest too, and worth explaining rather than hiding:
the course check only means anything while the vehicle is flying straight, so
detection waits for the next straight segment after the attack starts.

### Open questions

- **`DRIFT_RATE_MPS` recalibrated 0.5 -> 1.8** against Abishek's simulator
  (7 Sep). The fixture was the optimistic one: it flies straight, and turns are
  where dead reckoning suffers. Worst case over five seeds — clean 60 m at 60 s,
  manoeuvre 103 m; both inside the 20-120 m band the handover asked for.
- **The clean-run gate now holds for ~90 s, not the full 3-minute route.**
  Drift is not linear: the implied rate climbs 0.6 -> 4.3 m/s between 30 s and
  120 s, so a free-running witness eventually outgrows any linear sigma. Raising
  the constant to cover 120 s would push sigma past 200 m and make a 2 m/s
  walk-off invisible — trading the attack we exist to catch for a passing test.
  **Phase 7 is now a blocker, not an improvement:** while GNSS is trusted it must
  aid the witness so drift stops growing without bound and the residual becomes
  a filter innovation. Free-running for three minutes is a phase 2 shortcut.
- Abishek's raw-integration drift check (target 20–120 m at 60 s) measures
  *unfiltered* integration, so it is not the same quantity as our filtered
  witness error (~12 m). Both are useful; don't confuse them.

---

## Decisions already made — don't re-open these

| Decision | Settled on | Why |
|---|---|---|
| Which PS | **18**, not 4 or 15 | Novel, feasible, real market, and "no existing AI" is a strength here rather than an apology |
| Coordinate frame | local metres (ENU) | All maths in metres; lat/lon for display only |
| Frame rate | 20 Hz frames, GNSS at 5 Hz | Realistic — the gap matters to detection |
| Transport | UDP for sensors | Mirrors real telemetry; dropped frames become a real case |
| Randomness | new seed per run, stored | Different detection time each run (our proof), exact replay later |
| Map | own canvas, **no online tiles** | Demo runs with wifi off; tiles would fail silently |
| Filter | simple residuals first | Kalman innovation test is a Phase 11 upgrade — credibility, not capability |
| Vehicles | trucks **and** drones | Logistics is the better first market; same engine either way |

---

## Deliberately out of scope

- **Raw RF or signal-level processing** — that's the hardware answer we're
  displacing
- **Any real vehicle or receiver** — the PS says *simulated* data
- **A trained neural model in the detection path** — physics is deterministic,
  explainable, replayable and runs offline at sensor rate
- **Authentication schemes needing satellite-side support**

### Where an AI model *is* allowed

One job only: **writing the incident report after the event**, from the stored
structured record. Behind a visible on/off switch. Detection must be
byte-identical with it off — we demonstrate that on stage.

---

## Vocabulary

Use these words consistently; they end up in the UI and the pitch.

| Term | Means |
|---|---|
| **witness** | the GPS-free position estimate from stage 3 |
| **residual** | gap between what GPS claims and what physics says |
| **blame** | deciding *which* sensor is lying |
| **cause** | attack / fault / interference / unclassified |
| **trust** | 0–1 score per sensor, moves gradually |
| **attack zone** | map circle where the attacker probably is |
| **walk-off** | slow spoofing that drags position a few m/s |
| **meaconing** | replaying a genuine signal recorded elsewhere |

---

## Documents

| File | For |
|---|---|
| **[docs/schema.md](docs/schema.md)** | The frozen data contract. Read before writing code. |
| **[PLAN.md](PLAN.md)** | Tickable build checklist, 12 phases |
| [docs/sensorsentry.html](docs/sensorsentry.html) | The main brief — problem, workflow, novelty, business |
| [docs/sensorsentry-explained.html](docs/sensorsentry-explained.html) | Plain-language version, no background needed |
| [docs/sensorsentry-requirements.html](docs/sensorsentry-requirements.html) | 22 use cases, 24 user stories |
| [docs/sensorsentry-implementation-plan.html](docs/sensorsentry-implementation-plan.html) | The reasoning behind the phases |
| [docs/sensorsentry-demo-playbook.html](docs/sensorsentry-demo-playbook.html) | Demo script, anti-hardcoding proofs, Q&A |
| [docs/PROBLEM-STATEMENTS.md](docs/PROBLEM-STATEMENTS.md) | All 19 hackathon PS |

---

## If we fall behind

Cut in this order: simulator realism → fleet down to two vehicles → the written
report → truck profile.

**Never cut:** blame assignment, classification, hysteresis, or rehearsal.

> The most likely way this project fails is spending the time on the simulator
> instead of the detector. The simulator is visible, satisfying and endless.
> Every mark is in the detector.
