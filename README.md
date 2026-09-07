# SensorSentry

**A trust layer for any vehicle that navigates by GPS.**

Hackathon project for **PS 18 — Spoofing Detection & Sensor Fusion**:

> Create an intelligent sensor-fusion system that cross-validates simulated
> sensor data to detect GPS spoofing, false readings, or manipulated
> environmental inputs.

---

## The idea in one line

An attacker can fake the radio signal a vehicle receives.
**He cannot fake what the vehicle physically feels.**

GPS arrives from 20,000 km away at a signal strength below the noise floor — a
cheap ground transmitter can drown it out and hand a drone or truck a
completely fake position, which it will believe with total confidence.

SensorSentry cross-checks that reported position against the vehicle's own
independent senses — inertial motion, barometric altitude, magnetic heading,
wheel odometry, road geometry. When GPS starts describing movements the vehicle
never made, the disagreement is the proof.

## What makes it different

| | |
|---|---|
| **No hardware** | Every existing defence is a special antenna fitted per vehicle. Ours is software, installed into fleets already in service. |
| **Names the sensor** | Not "something is wrong" — *"the compass is lying, and the other four sensors agree that it is."* |
| **Attack or fault?** | A failing sensor and a deliberate attack look identical at first but demand opposite responses. We tell them apart. |
| **The fleet checks itself** | One vehicle can be fooled. Four vehicles hit simultaneously in one area locates the attacker. |

## Architecture

Three processes plus a browser. The separation is deliberate: the detector
receives **only sensor readings** — no true position, no attack flag — so it
structurally cannot cheat.

```
simulator  --UDP:5005-->  detector  --WS:8080-->  console / phone
(test tool)               (product)                    |
                              |                        |
                              +---> fleet service <----+
                                    (WS:8081)
```

## Repository

```
simulator/   vehicle + sensor models, attack and fault injectors, scenarios
detector/    the ten-stage pipeline — the actual product
fleet/       incident clustering, attack-zone estimation, advisories
console/     one responsive page: operator console and phone app
harness/     measurement sweeps, regression runs, results card
docs/        problem statements, briefs, requirements, plans
```

## Documentation

| Document | What it covers |
|---|---|
| [docs/sensorsentry.html](docs/sensorsentry.html) | **Start here.** The problem, the workflow, the novelty, the business |
| [docs/sensorsentry-explained.html](docs/sensorsentry-explained.html) | Plain-language explainer, no technical background needed |
| [docs/sensorsentry-brief.html](docs/sensorsentry-brief.html) | Technical brief — detection methods in depth |
| [docs/sensorsentry-requirements.html](docs/sensorsentry-requirements.html) | 22 use cases, 24 user stories with acceptance criteria |
| [docs/sensorsentry-implementation-plan.html](docs/sensorsentry-implementation-plan.html) | 12 build phases, data contract, team split, risks |
| [docs/sensorsentry-demo-playbook.html](docs/sensorsentry-demo-playbook.html) | Demo run-of-show, proofs it isn't hardcoded, Q&A |
| [docs/PROBLEM-STATEMENTS.md](docs/PROBLEM-STATEMENTS.md) | All 19 hackathon problem statements |

## Ground rules for this codebase

1. **Simulator and detector are separate processes.** Always. This is the proof
   the demo is not hardcoded.
2. **No truth crosses the socket.** No true position, no attack flag, ever. If a
   debugging shortcut needs it, the shortcut is wrong.
3. **Demoable beats complete.** The two-path map ships before any clever
   detection exists.
4. **Zero false alarms is a gate.** The clean run must finish with no alerts
   after every change. If that number leaves zero, stop and fix it.

## Status

Planning complete. Implementation not started.
