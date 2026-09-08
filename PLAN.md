# SensorSentry — Build Plan

Working checklist. Tick tasks as they land. The reasoning behind each phase is
in [docs/sensorsentry-implementation-plan.html](docs/sensorsentry-implementation-plan.html);
this file is the version you keep open while building.

**Where we are: every phase that invents anything is finished.** What remains
is rehearsal, and the one thing no amount of rehearsal substitutes for — real
sensor data.

| | |
|---|---|
| **Done** | 0-11 — detect, blame, classify, keep driving, locate the attacker, evidence, console, phone, truck, fleet, Chennai corridor |
| **Next** | **rehearsal (phase 12)** — five run-throughs, wifi off |
| **After the hackathon** | real recorded sensor data — see [the honest gap](#the-honest-gap) |
| **Tests** | 64 passing, zero false alarms |

**Both of the stages that make this project different are built and measured.**
It catches a spoof, a magnet or a failing sensor, names the sensor responsible,
says whether it is an attack, a breakdown or interference, gives the operator a
different instruction for each, and refuses to guess when the evidence does not
support an answer.

Verified against the simulator: 8 of 8 blame cases
(`python -m harness.blame_check`), 7 of 8 cause cases
(`python -m harness.classify_check`), zero false alarms across nine honest runs
(`python -m harness.results`).

**It acts on the verdict.** When GPS is distrusted the vehicle drops it and
navigates on its own sensors, holding the true route while the reported fix
walks away — measured 34 m from truth against GPS's 91 m
(`python -m harness.fallback_check`). The error budget grows while free-running
and is calibrated so it never claims to be better than it is; when it runs out
the answer is "stop", not a prettier number.

<a name="the-honest-gap"></a>
### The honest gap — the one thing to say before anybody asks

**Every tolerance in the detector is calibrated against our own simulator.**
The physics is real and the maths does not know where its numbers came from,
but no constant in `crossvalidate.py` has ever met a real accelerometer. That
is the single largest risk to this as a product, and the first work after the
hackathon: take a public recorded drive (comma2k19, or the Google Smartphone
Decimeter set), inject a walk-off into the GNSS mathematically, and re-measure
every floor on data nobody here generated.

Say this to a buyer before they find it. A team that names its own weakest
joint is more credible than one that waits to be caught at it.

**Owners:** A = simulator · B = detector core · C = unique logic · D = console
*(fewer people? see [Team](#team) at the bottom)*

---

## Ground rules — check these hold after every phase

- [x] Simulator and detector run as **separate processes**
- [x] **No truth crosses the socket** — enforced at ingest, rejected loudly
- [x] Clean-run scenario finishes with **zero false alarms** — 8 runs, verified
- [x] Demo still runs end to end

> Rule 3 is a gate, not a goal. If false alarms ever leave zero, stop and fix
> it before adding anything new.

---

## Phase 0 — Foundations · ~2h · everyone

- [x] Python env, NumPy only, no other dependencies
- [x] **Freeze the sensor-frame schema** — write it to `docs/schema.md`
- [x] Freeze the verdict-frame schema
- [x] `simulator/publisher.py` sends frames over UDP :5005
- [x] `detector/ingest.py` receives and prints it
- [x] Agree coordinate frame: **local metres (ENU)**, lat/lon for display only

**Done when:** two processes are running and one receives frames from the other.

> Freezing the schema is what lets everyone work in parallel from here. Do it
> first, in writing, and don't change it casually.

---

## Phase 1 — Simulator, clean flight · ~6h · A

- [x] `vehicle.py` — motion along a scripted route, attitude rate-limited
- [x] `sensors.py` — GNSS jitter
- [x] `sensors.py` — IMU, gyro reports real body rates (was zero — see review)
- [x] `sensors.py` — barometer, magnetometer, wheel odometry
- [x] Publish at 20 Hz, GNSS at 5 Hz, realtime by default
- [x] New RNG seed per run, recorded in the stream header

**Done when:** a three-minute clean flight produces plausible frames.

> IMU drift is what sets your detection limit later. Honest drift now means an
> honest failure boundary to show judges.

---

## Phase 2 — Detector: witness and residual · ~8h · B

- [x] `ingest.py` — buffer and time-align frames, detect dropped `seq`
- [x] `health.py` — stuck value check
- [x] `health.py` — out-of-range, dropout, excess-noise checks
- [x] `health.py` — every flag carries a reason code, never a bare boolean
- [x] `deadreckon.py` — integrate IMU into an independent position
- [x] `deadreckon.py` — growing uncertainty value
- [x] Compute and print the GNSS-vs-dead-reckoning residual

**Done when:** residual stays small and bounded on a clean run, and code review
confirms **GNSS is never read in the dead-reckoning path**.

---

## Phase 3 — Console v1 · ~6h · D ⛔ GATE

- [x] `detector/server.py` — SSE out on :8080 *(was WebSocket, see docs/schema.md)*
- [x] `console/{common,display,drive}.js` — browser client *(was one app.js;
      split when the console became two pages, `/` and `/drive`)*
- [x] canvas map: grid, scale bar, auto-fit (**no online tiles**)
- [x] Draw GNSS-claimed path and dead-reckoned path, plus the gap between them
- [x] Raw-feed panel showing incoming frames
- [x] Scenario buttons wired to the simulator, built from its own list

**⛔ Do not pass until:** you can show this to someone and they understand it
without explanation.

> From here the project is demonstrable. Everything after improves a working
> demo instead of gambling on one. Ship it ugly — polish is Phase 12.

---

## Phase 4 — Attacks and cross-validation · ~8h · A + B

> A's half is handed off: [docs/handover/02-abishek-attacks.md](docs/handover/02-abishek-attacks.md)

**Attacks** (A)
- [x] Walk-off, with adjustable speed and direction
- [x] Teleport
- [x] Altitude-only
- [x] Replay / meaconing

**Faults** (A)
- [x] Stuck value
- [x] Excess noise
- [x] Dropout
- [x] Slow bias

**Cross-validation** (B)
- [x] `crossvalidate.py` — GNSS ↔ inertial
- [x] GNSS ↔ road map — the check a drone cannot have
- [x] GNSS altitude ↔ barometer
- [x] GNSS course ↔ compass **(this is what actually detects)**
- [x] Compass ↔ gyro *(catches the magnet case)*
- [ ] Barometer ↔ vertical motion *(catches pressure interference)*
- [ ] Strength slider and direction control in the console

**Done when:** launching a walk-off visibly separates the two paths, and every
pair's score is visible in the console.

---

## Phase 5 — Blame assignment ★ · ~6h · C ⛔ GATE

- [x] `blame.py` — corroboration first, then failure count
- [x] Confirm the remaining sensors still agree among themselves
- [x] Name the guilty sensor
- [x] Attach failing pairs and magnitudes as evidence
- [x] Return `cannot_isolate` when the evidence cannot choose

**⛔ Done when:** spoofing GNSS names GNSS · a magnet names the magnetometer ·
**both at once returns `cannot_isolate` rather than a wrong answer.**

> The system is allowed to say it doesn't know. Judges will test this exact case.

---

## Phase 6 — Attack or fault ★ · ~6h · C ⛔ GATE

- [x] `classify.py` — coherence, steadiness and erraticness over a window
- [x] Steady and one-directional, radio-sensed → `attack`
- [x] Erratic, restless, or failing its own health check → `fault`
- [x] Steady offset on a field-sensed measurement → `interference`
- [x] Below confidence threshold → `unclassified`, default to safe response

**⛔ Done when:** the three scenarios classify correctly with **no
scenario-specific code anywhere in the classifier.**

---

## Phase 7 — Trust, hysteresis, fallback · ~6h · B + C

- [x] `trust.py` — hysteresis per cross-check
- [x] Requires 2 s sustained to escalate, 6 s to relax
- [x] No single reading can change state — clean runs now silent
- [x] `fusion.py` — drop untrusted sensor, keep navigating
- [x] Publish a growing error budget, measured so it never under-states
- [x] Console: verdict panel, navigation source, scenario buttons

**Done when:** the hard-manoeuvre clean run finishes with **zero alerts**, and
the spoofing run keeps the vehicle on its true route after detection.

> This is the phase teams skip and the one that decides credibility.

---

## Phase 8 — Truck profile · ~4h · A + B

- [x] `profiles.py` — sensor set and applicable pairs per vehicle type
- [ ] Truck motion model
- [ ] Wheel odometry cross-check
- [ ] Simple road-network check
- [ ] Cargo-theft scenario: fake path on road, real path stops behind

**Done when:** switching to truck requires **configuration only, no code
change**. You say this on stage, so it must be literally true.

---

## Phase 9 — Fleet and attack zone ★ · ~6h · C + D

- [x] Four detector instances against four simulated vehicles
- [x] `cluster.py` — group by radius and time; attacks only, never faults window
- [x] Zone centre and radius, from the vehicles' *own* positions
- [x] Draw the zone on the fleet map
- [x] `advisory.py` — warn on time-to-reach, not distance

**Done when:** four vehicles attacked together produce **one event and one
circle** · one vehicle alone produces **no circle**.

---

## Phase 10 — Evidence, phone, report · ~6h · B + D

- [x] `evidence.py` — append-only, frames verbatim plus every verdict change
- [x] Record includes the RNG seed, so the run can be re-created
- [x] Replay: stored record back through a fresh detector, identical verdict
- [x] Responsive layout — same page works as the phone app
- [x] Alert banner sticks to the top of the phone view
- [x] Written report behind a visible switch — template, not a model, and it says so

**Done when:** replay reproduces an identical verdict, and the report switch
changes nothing about detection.

---

## Phase 11 — Measurement harness · ~5h · A

- [x] `sweep.py` — attack strengths 5.0 → 0.5 m/s across seeds
- [x] Record detection time for each
- [ ] `regress.py` — twenty clean runs, assert zero alerts
- [x] `results.py` — the card, and it fails if a false alarm appears

**Done when:** the failure-boundary table in the demo playbook holds **your
measured numbers**, including the point where you fail.

> This harness produces both your results card and your credibility.

---

## Phase 12 — Hardening and rehearsal · ~5h · everyone

- [ ] Reset returns to clean state in under two seconds, from any state
- [x] Scenario switching never requires a restart
- [ ] Larger fonts, higher contrast, readable from across a room
- [ ] Turn wifi off and confirm everything still runs
- [ ] Full run-through out loud — 1
- [ ] Full run-through out loud — 2
- [ ] Full run-through out loud — 3
- [ ] Full run-through out loud — 4
- [ ] Full run-through out loud — 5

**Done when:** someone outside the team can run the whole demo from the
playbook without asking a question.

---

## Definition of done

- [x] Two processes, no truth crossing the socket
- [x] Eight selectable — seven scenarios plus the fleet — one click each
- [x] Clean run with hard manoeuvres → **zero alerts**
- [~] Spoofing caught — but GNSS not yet *named* (phase 5) and no fallback yet (phase 7)
- [x] Broken sensor reported as fault, not attack
- [~] Magnet caught — but not yet *classified* as interference (phase 6)
- [ ] Truck profile runs with configuration only
- [x] Attacked vehicles → one event, one zone
- [x] Incident replays to an identical verdict
- [x] Strength ladder filled with measured numbers, including the failure point
- [x] Report switch turns off with no effect on detection
- [ ] Demo run end to end, out loud, five times

---

## Team

| Owner | Area | Phases |
|---|---|---|
| **A** | Simulator — vehicle, sensors, attacks, faults, scenarios | 1, 4, 8, 11 |
| **B** | Detector core — ingest, health, dead reckoning, cross-validation, fusion | 2, 4, 7 |
| **C** | The unique parts — blame, classification, trust, fleet | 5, 6, 9 |
| **D** | Console — map, panels, controls, phone layout | 3, 10 |

**Three people:** C's work goes to B; A picks up the harness.
**Two people:** one takes simulator + console, the other takes the whole
detector; cut Phase 9 to two vehicles.

**Put your strongest person on Phases 5 and 6.** Those two are the entire
difference between this project and a threshold alarm.

---

## If you fall behind

Cut in this order:

1. Simulator realism — it only has to be plausible
2. Phase 9 down to two vehicles
3. Phase 10 written report *(the optional AI layer)*
4. Phase 8 truck profile

**Never cut:** Phase 5, Phase 6, Phase 7, or Phase 12.

> The most likely way this project fails is spending the time on the simulator
> instead of the detector. The simulator is visible, satisfying and endless.
> Every mark is in the detector.
