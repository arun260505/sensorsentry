# Task 2 — Attacks and faults

**For:** Abishek · **Time:** ~8 hours · **Branch:** `sim/attacks`

Task 1 is merged. I fixed a bug in it — details at the bottom, read that after.

---

## What you're building

Four new files in `simulator/`. Do them in this order.

```
attacks.py        fake the GPS position
faults.py         break a sensor
interference.py   magnet near the compass
control.py        buttons for the console
```

---

## The one rule

**An attack changes what a sensor *reports*. It does not move the vehicle.**

A spoofer sends a fake radio signal. The drone still flies its real route
through real air — only the number coming out of the GPS is a lie.

So every file below changes the **output of `sensors.py`**, after the reading
is made. **Never edit `vehicle.py`.** If you need to move the vehicle to make
an attack work, something has gone wrong — ask me.

---

## Step 1 — `attacks.py`

Four attacks. Each one changes the `gnss` dict only.

```python
class WalkOff:
    """Pull the reported position slowly along a bearing."""
    def __init__(self, speed_mps, bearing_deg): ...
    def apply(self, gnss, t_since_start): ...

class Teleport:
    """Jump the position instantly."""
    def __init__(self, offset_m, bearing_deg): ...

class AltitudeOnly:
    """Only change altitude. Horizontal stays honest."""
    def __init__(self, offset_m): ...

class Replay:
    """Position from somewhere else, plus a stale clock."""
    def __init__(self, source_offset_m, clock_lag_s): ...
```

**WalkOff is the main one.** Offset = `speed_mps × seconds_since_attack_started`,
in the direction of `bearing_deg`.

`speed_mps` must work anywhere from **0.0 to 5.0**.
**At 0.0 nothing must change at all** — we prove that on stage.

While any attack is running, also add ~3 to `gnss["cn0_mean"]`. A real spoofer
transmits louder than satellites do. Three lines, second piece of evidence.

---

## Step 2 — `faults.py`

Four faults. Each works on any sensor: `gnss`, `imu`, `baro`, `mag`, `odom`.

```python
class Stuck:    # freeze on the last value
    def __init__(self, sensor): ...

class Noisy:    # multiply the noise
    def __init__(self, sensor, multiplier): ...

class Dropout:  # send null
    def __init__(self, sensor): ...

class Bias:     # slow ramp added to the reading
    def __init__(self, sensor, rate_per_s): ...
```

**Make `Stuck` and `Noisy` look messy.** A broken sensor is random and jumpy.
An attack is smooth and points one way. Later we have to tell them apart, so
don't make your faults tidy.

---

## Step 3 — `interference.py`

Two injectors.

```python
class Magnet:    # add an offset to compass heading
    def __init__(self, offset_deg): ...

class Pressure:  # add an offset to barometer
    def __init__(self, offset_hpa): ...
```

**Important for `Magnet`:** change `mag["heading_deg"]` only.
**Do not touch `imu["gz"]`.**

The way we detect it is: *the compass turned but the gyro says we didn't.*
If you change the gyro too, that disappears and the case stops working.

---

## Step 4 — `control.py`

An HTTP server on **port 5010**. Use `http.server` from the standard library.
The console already calls these.

| Method | Path | Body | Does |
|---|---|---|---|
| POST | `/start` | `{"scenario": "drone_clean"}` | start a run |
| POST | `/reset` | — | stop and clear |
| POST | `/inject` | `{"kind","type","strength","bearing_deg"}` | start an attack now |
| GET | `/scenarios` | — | list the names |

Three requirements:

- **Reset must finish in under 2 seconds**, from any state
- **Inject takes effect on the next frame**
- **Changing scenario must not need a restart**

---

## Step 5 — three new scenarios

Add these to `scenarios.py`. Each is a normal route with an attack scheduled
partway through, so pressing Start plays the whole story by itself.

| Name | What happens |
|---|---|
| `drone_walkoff` | clean route, walk-off spoof starts around t=30 s |
| `drone_fault` | clean route, a sensor genuinely breaks |
| `drone_magnet` | clean route, magnet near the compass |

---

## Step 6 — truth log

Extend `--truth-log` so it also writes a line whenever an attack starts:
**what type, what parameters, what time.**

Simulator side only, never in a frame. We need it later to measure "caught
14 seconds after it started" — which we can only do if something wrote down
when it started.

---

## Done when

- [ ] All four attacks work
- [ ] WalkOff at `0.0` changes nothing at all
- [ ] All four faults work on any sensor
- [ ] Magnet changes the compass and **not** the gyro
- [ ] `/start`, `/reset`, `/inject`, `/scenarios` all work
- [ ] Reset finishes in under 2 seconds
- [ ] The three new scenarios run start to finish
- [ ] Truth log records when each attack started
- [ ] **`drone_clean` still runs with zero alerts** — check this last

---

## Don't

- Don't edit `vehicle.py`
- Don't put anything about attacks into a frame (no `attack_active`, no
  `spoof_offset`, nothing). The detector rejects unknown fields, so you'll
  find out straight away
- Don't touch `detector/`, `fleet/` or `console/`

---

## When done

```bash
git checkout -b sim/attacks
git push -u origin sim/attacks
```

Open a PR and paste in:
1. One frame from during a walk-off, and one from a clean run
2. Confirmation that walk-off at 0.0 changes nothing
3. How long `/reset` takes

---

## What I fixed in your Task 1

Your structure, CLI and drift check were good. You were also right to change my
gyro bias figure — 0.002 rad/s is 412 deg/hr and real IMUs are far better.

The bug: `sensors.py` always reported `roll_rate = 0` and `pitch_rate = 0`,
but the vehicle banks to 30°. So the accelerometer said "gravity is tilted 30°"
and the gyro said "we never turned". Both can't be true.

The detector believes the gyro, keeps its attitude level, and then reads that
tilted gravity as forward acceleration — about 2.8 m/s² of thrust that never
happened. A clean flight with no attack pushed it 2 km off course.

Your `check_drift.py` couldn't catch it because it uses the vehicle's *true*
attitude, which the real detector never has.

**The lesson for this task:** every sensor has to describe the same situation.
The only sensor allowed to disagree is one you're deliberately breaking.

Fix is on your branch, commit `6e2f491` — worth reading the diff.

Small thing: `truth.jsonl` got committed with the message "Your commit
message". I removed it and gitignored it. Generated files stay out of git.
