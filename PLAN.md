# SensorSentry — Build Plan

Working checklist. Tick tasks as they land. The reasoning behind each phase is
in [docs/sensorsentry-implementation-plan.html](docs/sensorsentry-implementation-plan.html);
this file is the version you keep open while building.

**Where we are:** the detection core works. Clean flights are silent, GPS
spoofing and compass interference are both caught, and the numbers are
measured rather than claimed (`python -m harness.sweep`).

| | |
|---|---|
| **Done** | 1 simulator · 2 witness · 3 console · 4 cross-validation · **5 blame** · 7 hysteresis |
| **In progress** | 4 attack injectors (Abishek, task 2) |
| **Next gate** | **6 — attack, fault or interference** |
| **Not started** | 8 truck · 9 fleet · 10 evidence · 12 rehearsal |
| **Tests** | 22 passing |

What the system can do today: catch a spoof or a magnet, **name the sensor
responsible**, show the evidence, and refuse to guess when the evidence does
not support an accusation. Verified 8 of 8 cases — `python -m harness.blame_check`.

What it still cannot do is say **why**: attack, breakdown or interference.
Those demand opposite responses from the operator, and telling them apart is
phase 6 — the last of the two stages that make this project different.

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
- [x] `console/app.js` — browser client *(one file, not ws/map/panels split)*
- [x] canvas map: grid, scale bar, auto-fit (**no online tiles**)
- [x] Draw GNSS-claimed path and dead-reckoned path, plus the gap between them
- [x] Raw-feed panel showing incoming frames
- [ ] Start and Reset controls wired to the simulator *(buttons live; simulator :5010 not up yet)*

**⛔ Do not pass until:** you can show this to someone and they understand it
without explanation.

> From here the project is demonstrable. Everything after improves a working
> demo instead of gambling on one. Ship it ugly — polish is Phase 12.

---

## Phase 4 — Attacks and cross-validation · ~8h · A + B

> A's half is handed off: [docs/handover/02-abishek-attacks.md](docs/handover/02-abishek-attacks.md)

**Attacks** (A)
- [ ] Walk-off, with adjustable speed and direction
- [ ] Teleport
- [ ] Altitude-only
- [ ] Replay / meaconing

**Faults** (A)
- [ ] Stuck value
- [ ] Excess noise
- [ ] Dropout
- [ ] Slow bias

**Cross-validation** (B)
- [x] `crossvalidate.py` — GNSS ↔ inertial
- [ ] GNSS ↔ wheels, GNSS ↔ road map
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

- [ ] `classify.py` — measure directional consistency of error over a window
- [ ] Steady and one-directional → `attack`
- [ ] Erratic, or paired with a health failure → `fault`
- [ ] One measurement shifting while motion sensors report nothing → `interference`
- [ ] Below confidence threshold → `unclassified`, default to safe response

**⛔ Done when:** the three scenarios classify correctly with **no
scenario-specific code anywhere in the classifier.**

---

## Phase 7 — Trust, hysteresis, fallback · ~6h · B + C

- [x] `trust.py` — hysteresis per cross-check
- [x] Requires 2 s sustained to escalate, 6 s to relax
- [x] No single reading can change state — clean runs now silent
- [ ] `fusion.py` — drop untrusted sensor, keep navigating
- [ ] Publish a growing error budget in metres
- [ ] Console: verdict panel and trust bars

**Done when:** the hard-manoeuvre clean run finishes with **zero alerts**, and
the spoofing run keeps the vehicle on its true route after detection.

> This is the phase teams skip and the one that decides credibility.

---

## Phase 8 — Truck profile · ~4h · A + B

- [ ] `profiles.py` — sensor set and applicable pairs per vehicle type
- [ ] Truck motion model
- [ ] Wheel odometry cross-check
- [ ] Simple road-network check
- [ ] Cargo-theft scenario: fake path on road, real path stops behind

**Done when:** switching to truck requires **configuration only, no code
change**. You say this on stage, so it must be literally true.

---

## Phase 9 — Fleet and attack zone ★ · ~6h · C + D

- [ ] Run four detector instances against four simulated vehicles
- [ ] `cluster.py` — group incidents by radius and time window
- [ ] `zone.py` — estimate centre and radius
- [ ] Draw the zone on the fleet map
- [ ] `advisory.py` — warn vehicles whose route crosses the zone

**Done when:** four vehicles attacked together produce **one event and one
circle** · one vehicle alone produces **no circle**.

---

## Phase 10 — Evidence, phone, report · ~6h · B + D

- [ ] `evidence.py` — append-only incident record
- [ ] Record includes the RNG seed, so replay is exact
- [ ] Replay mode: feed a stored record back through the detector
- [ ] Responsive layout — same page works as the phone app
- [ ] Push alert to the phone view
- [ ] Optional written report behind a **visible on/off switch**

**Done when:** replay reproduces an identical verdict, and the report switch
changes nothing about detection.

---

## Phase 11 — Measurement harness · ~5h · A

- [x] `sweep.py` — attack strengths 5.0 → 0.5 m/s across seeds
- [x] Record detection time for each
- [ ] `regress.py` — twenty clean runs, assert zero alerts
- [ ] `results.py` — results card and strength ladder

**Done when:** the failure-boundary table in the demo playbook holds **your
measured numbers**, including the point where you fail.

> This harness produces both your results card and your credibility.

---

## Phase 12 — Hardening and rehearsal · ~5h · everyone

- [ ] Reset returns to clean state in under two seconds, from any state
- [ ] Scenario switching never requires a restart
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
- [ ] Six scenarios selectable, starting within two seconds
- [x] Clean run with hard manoeuvres → **zero alerts**
- [~] Spoofing caught — but GNSS not yet *named* (phase 5) and no fallback yet (phase 7)
- [ ] Broken sensor reported as fault, not attack
- [~] Magnet caught — but not yet *classified* as interference (phase 6)
- [ ] Truck profile runs with configuration only
- [ ] Four attacked vehicles → one event, one zone
- [ ] Incident replays to an identical verdict
- [x] Strength ladder filled with measured numbers, including the failure point
- [ ] Report switch turns off with no effect on detection
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
