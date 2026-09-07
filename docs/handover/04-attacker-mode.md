# Task 4 — Attacker mode: let the judge play the attacker

**For:** console/detector · **Time:** ~5 hours · **Branch:** `main`

---

## Why this one matters more than anything else left

We measured well and demoed badly. That is the honest summary of where the
project stands.

Everything in the results card is true — nine honest runs with no false alarm,
nine attacks out of nine caught, the guilty sensor named eight times out of
eight. None of it is visible. A judge sees a screen with three roads on it and
seven pre-named buttons like `truck · theft`, presses one, and watches a dot
move. **That is exactly what a hardcoded demo looks like**, and there is no
sentence we can say that fixes it, because the judge has no way to test the
claim.

The fix is not to argue. It is to hand them the attack.

> A judge who spoofs the GPS themselves, at a moment nobody could have
> scripted, in a direction they chose, and watches the system catch it —
> has run the experiment. Nothing said from a slide competes with that.

The capability already exists. `simulator/control.py` has had a `/inject`
endpoint since task 2: attack, fault or interference, any strength, any
bearing, injected into a run already in flight. **Nothing in the console has
ever called it.** We built the mechanism and never built the handle. This task
is the handle.

---

## What you're building

```
console/index.html   the attacker panel and the scoreboard
console/style.css    styling for both
console/app.js       injection, map-aiming, the stopwatch, the score
```

No detector change. No simulator change. The endpoint is already there and
already correct — see `_handle_inject` in `simulator/control.py`.

---

## The rule that must not break

**The detector must never learn that an attack was injected.** Not the time,
not the speed, not the bearing, not the fact of it.

This is rule 2 of the project and this task is the one most likely to break it,
because it is genuinely tempting: the browser knows exactly when the judge
clicked, so showing "detected 16 s after injection" is one line of code away.

Do it **in the browser only**. The click happens in the page, the stopwatch
runs in the page, the page compares it against the alert that arrives over the
ordinary stream. The detector is never told, cannot be told, and the number on
screen is therefore trustworthy. If that timing ever travels through the
detector, the demo's central claim is dead and we would deserve to lose.

---

## Step 1 — The attacker panel

First panel in the right-hand column, above the verdict. It is the main event
now, so it is allowed to look like it.

Title: **You are the attacker.**

Under it, one line: *nothing here is scripted; pick your moment, your speed,
your direction — the detector is never told.*

Controls:

| Control | Injects | Range |
|---|---|---|
| Drift speed slider | `walkoff` | 0.5 – 10 m/s |
| Direction slider | bearing for the above | 0 – 350° |
| **Spoof now** (big) | fires the walk-off at those settings | — |
| Aim on map | arms crosshair mode; the next map click sets the bearing and fires | — |
| Jump it 300 m | `teleport` | — |
| Magnet on compass | `magnet` interference, 30° | — |
| Break it | `stuck` / `noisy` / `dropout` on baro, mag, odom or imu | — |
| Stop the attack | `kind: "clear"` | — |

Everything is disabled until a run is live, with a hint saying so. `/inject`
returns 400 with a readable message if there is no run, so the failure is
already handled — but do not make a judge discover that.

**Aiming must be explicit, never a bare map click.** During the clean-run
scenario — the zero-false-alarm gate, the most important thing on the
schedule — one stray click would inject an attack and destroy the story we are
in the middle of telling. So: press *Aim on map* first, crosshair appears,
then click. Pressing it again cancels.

Bearing from a click, with the vehicle's last reported position as the origin:

```js
const de = eClick - eVehicle, dn = nClick - nVehicle;
const bearing = (Math.atan2(de, dn) * 180 / Math.PI + 360) % 360;   // 0 = north
```

To invert `project()`:

```js
e = view.cx + (x - w / 2) / view.scale;
n = view.cy - (y - h / 2) / view.scale;
```

## Step 2 — The stopwatch

The moment an attack is injected, start counting in the page. Three states:

- **hunting** — "you attacked · 6.4 s ago", counting up, amber
- **caught** — "caught you in 16.2 s", green, stop the clock
- **got away** — after 90 s with no alert, red

"Caught" means the stream reports `state === "ALERT"` *after* the injection.
If the console is already alerting when they arm, refuse and say
*"it is already alerting — press Stop the attack first"*; otherwise the
previous attack's alert gets counted as a catch for this one, which would be
us cheating in our own favour on stage.

## Step 3 — The scoreboard

Two numbers at the top of the panel: **you got away with** and **we caught**.

This is the part that turns a demo into a game. Judges compete. A judge who
spends four minutes trying to beat it has spent four minutes proving our case
for us, and will remember it afterwards.

It must be honest. **Slow walk-offs will score for the judge, and that is
correct** — 1 m/s on a drone is genuinely below our floor and we say so in the
pitch anyway. A scoreboard that always reads 0–5 in our favour is not a game,
it is a rigged fairground stall, and a sharp judge will smell it. Let them win
sometimes. Then explain *why* they won, which is the most credible thing that
happens all demo:

> "You beat it at half a metre per second — that is slower than our own
> sensors drift, so we genuinely cannot see it. It also means moving that
> lorry a kilometre off its route takes you half an hour. We do not stop the
> attack; we make it slow enough to notice."

---

## Done when

- [ ] A judge can spoof, jump, magnetise and break sensors mid-run, from the page
- [ ] Aiming needs an explicit arm — no accidental injection during a clean run
- [ ] The stopwatch times from their click to the alert, in the browser only
- [ ] Nothing about the injection ever reaches the detector — grep the diff
- [ ] The scoreboard counts both ways and can be lost
- [ ] `python -m tests.run_all` still 64/64, zero false alarms

---

## What this does not fix

The map still has three roads on it and looks empty, which is the other half of
why the demo underwhelms. That is task 5 — real OpenStreetMap geometry for the
Sriperumbudur–Oragadam corridor, baked into a file at build time so it still
works with the wifi off.

Attacker mode comes first because "it looks hardcoded" loses hackathons and
"the map is plain" does not.
