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

**7 September 2026. Everything that makes this project different is built and
measured.** What remains is rehearsal and polish, not invention.

Run it:

```bash
python -m detector.server                     # terminal 1 — console on :8080
python -m simulator.control --quiet           # terminal 2 — scenario buttons
# open http://127.0.0.1:8080 and pick a scenario

python -m tests.run_all                       # 64 tests
python -m harness.sweep                       # false alarms + detection curve
python -m harness.blame_check                 # which sensor          8/8
python -m harness.classify_check              # attack/fault/interf.  7/8
python -m harness.fallback_check              # keeps flying under attack
python -m harness.fleet_check                 # zone + advisory
python -m harness.send_fleet                  # 4 vehicles, 3 attacked
```

### What works

| | |
|---|---|
| False alarms, 8 honest flights incl. hard manoeuvres | **zero** |
| Walk-off 2 / 3 / 5 m/s | caught 15-18 s after onset |
| Walk-off 1 m/s and below | **not detected — our floor** |
| Magnet on compass, 25 deg+ | caught 2 s after onset |
| Names the guilty sensor | 8 of 8 |
| Attack / fault / interference | 7 of 8 |
| Drops the liar, keeps flying | 34 m from truth vs GPS's 91 m |
| Fleet locates the attacker | 3 hit -> one zone, 4th warned |
| Incident replays to identical verdict | yes |

### Owners

| Owner | Area | State |
|---|---|---|
| Abishek | Simulator | tasks 1-3 done — flight, attacks, truck, roads |
| — | Detector | stages 1-10 done |
| — | Console | map, banner, verdict, fleet, phone view, report |

### Known weaknesses — say these out loud, do not hide them

- **Walk-off below ~2 m/s is not detected.** Slower than our own drift. At that
  speed an attacker needs eight minutes to move a vehicle a kilometre.
- **Dead reckoning buys about 40 seconds**, not minutes. Past that our drift
  overtakes even a 3 m/s spoof, which is why the console counts down and then
  says "stop or land".
- **A slowly drifting compass is not reliably attributed.** As it drifts the
  course check fails and blame can migrate to GPS; settled verdicts flip-flop.
  The magnet and the noisy compass are solid; this middle case is not.
- **Detection latency is 15-18 s** because the course check needs a straight
  segment. The attack is caught at the next one.

### Still to do

1. **Rehearsal (phase 12) — nothing done, and worth more than any feature.**
   Five full run-throughs, wifi off, reset under two seconds.
2. `harness/regress.py` and `results.py` — the results card.
3. **The demo playbook HTML still has placeholder numbers** (it says a 0.2 m/s
   floor; the measured floor is 2 m/s). Fix before anyone reads it.
4. Fleet cannot be started from the console — `send_fleet` is CLI only.

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
