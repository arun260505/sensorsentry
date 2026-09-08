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
             chennai.py — the road network and origin, kept apart so the
             corridor can be swapped for another city without touching code
detector/    the ten-stage pipeline — the actual product
fleet/       incident clustering, attack-zone estimation, advisories
console/     two pages, one server. `/` is the evidence display — the screen
             the room watches. `/drive` is the control surface — the screen
             the driver touches. common.js is what they share. Both are
             responsive, so either is also the phone view.
android/     the phone alarm — two Java files, no libraries. Polls the
             console and rings when a vehicle is spoofed. SensorSentry-debug.apk
             is the built one; adb install it, no toolchain needed
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
# 127.0.0.1:8080        the display   — put this on the screen the room sees
# 127.0.0.1:8080/drive  the controls  — put this where only you can reach it
#
# Two windows, one server. The driving window must hold keyboard focus or the
# arrow keys go nowhere; it says so across the top when it does not.

python -m tests.run_all                       # 68 tests
node tests/console_draw.js                    # the map actually draws
python -m harness.sweep                       # false alarms + detection curve
python -m harness.blame_check                 # which sensor          8/8
python -m harness.classify_check              # attack/fault/interf.  8/8
python -m harness.fallback_check              # keeps flying under attack
python -m harness.fleet_check                 # zone + advisory
python -m harness.usecases                    # all 17 cases, named
python -m harness.dressrehearsal              # the whole demo, end to end
python -m harness.results --save              # the closing card
python -m harness.send_fleet                  # 4 vehicles, 3 attacked
```

### What works

| | |
|---|---|
| False alarms, 9 honest flights and drives incl. hard manoeuvres | **zero** |
| Walk-off 2 / 3 / 5 m/s | caught 15-18 s after onset |
| Drone floor: 2 m/s | caught in 18 s; below that, **not detected** |
| Truck floor: 1 m/s | caught in 130 s; 3 m/s in 36-46 s |
| Magnet on compass, 25 deg+ | caught 2 s after onset |
| Names the guilty sensor | 8 of 8 |
| Attack / fault / interference | 8 of 8 |
| Drops the liar, keeps flying | 34 m from truth vs GPS's 91 m |
| Fleet locates the attacker | 3 hit -> one zone, 4th warned |
| Truck: honest Kelambakkam run | silent |
| Truck: walk-off 3 m/s | names gnss/attack at 36-46 s |
| Meaconing / replay | named gnss in 8 s |
| Incident replays to identical verdict | yes |

**A dense road network helps the attacker and a sparse one helps us.** That is
a real property of the method, not a quirk of one map, and it is exactly the
kind of thing a buyer's engineer will ask about. Say it first — then say that
we have measured it in both directions, because we have:

| | drawn network, 3 roads | Sriperumbudur, 105 | VIT-Kelambakkam, 43 |
|---|---|---|---|
| Truck floor | 1 m/s | 2 m/s | **1 m/s** |
| Caught at that floor | 159 s | 37 s | 130 s |
| Meaconing | — | detected, **not named** | **named in 8 s** |

A slowly drifting position keeps landing on *some* road when there are a
hundred of them, and being on *a* road is not evidence of being on the right
one. With thirty it runs out of carriageway, the road check fails, and blame
convicts the receiver outright — which is why the same replay attack that
could only be detected on the old corridor is named on this one.

**The honest trade, and do not hide it:** the floor halved, but the slowest
attack now takes 130 s to prove rather than 37 s at twice the speed. We catch
slower attackers; we take longer to be sure about them.

### The vehicle looks like the vehicle, and the browser is finally tested

The marker was a triangle for both. It is now a **lorry drawn to scale in
metres** — 12 m by 2.6 m, the same surveyed metres the roads are drawn in, so
it sits in a lane and a spoofed position is visibly off the carriageway. A
fixed-size icon would have put a 28 px lorry on a 4 m road at follow zoom: a
vehicle wider than the highway, which reads as a broken picture rather than a
wrong one. That is the trap from last time, in a new place.

**The drone is deliberately *not* to scale.** A 1.1 m quadcopter is four pixels
across at follow zoom and gone at every other. It does not sit on a road, so
nothing breaks by drawing it larger than life, and a marker nobody can find is
worse than one out of scale. Below 16 px a lorry outline is a smudge, so under
that it falls back to the arrow — the same "detail is decided in pixels" rule
the buildings and road labels use. Fleet markers are 7 px and stay arrows.

Both markers are the same shape at the same scale on purpose: they are one
vehicle, seen twice. Two different silhouettes would read as two vehicles,
which is the one thing that picture must not say.

**And `tests/console_draw.js` exists now.** Everything the browser does had no
automated coverage at all, which is why two failures in CLAUDE.md were found
live. It stubs a canvas, loads the real `common.js` and `display.js`, and draws
every vehicle at every zoom — failing on a throw, on a call that draws nothing,
and on a **non-finite coordinate**, which is worth catching on its own: canvas
silently ignores NaN, so the shape just does not appear and nothing says why.
Node, dev-time only; rule 6 is about what the demo needs to run, and the demo
does not run this.

### The wandering compass — two bugs, and one number that was never measured

The last 7/8 is now **8/8**, and neither half of the fix was where it looked.

**1. An alibi from a check that had only just stopped failing.** A compass
wandering as a random walk drifts far enough to break the compass-gyro check,
then wanders back *through* the truth on its way to being wrong the other way.
At the instant it crosses, that check flips to OK — and it was clearing the
compass of a course-check failure that had been running for **eighty-eight
seconds**, on the strength of having been quiet for **zero**. GNSS was then the
only suspect left and was named, **at confidence 1.0, as under attack.** A
wandering compass is a workshop job; that verdict sends the police.

`blame.py` already refuses an alibi from a check that averages over a window,
because it describes the past. This is the mirror of it: an alibi must also
have *held* long enough to explain the accusation — its window plus the time
hysteresis takes to relax. If the accusation is still standing after all that,
the sensor being fine right now is not the explanation.

**2. Interference and fault were split on a number that cannot split them.**
`ARRIVED_AS_STEP` asks whether the error arrived as a jump. Measured across
three seeds and two strengths: 40 deg magnet **6.8-7.2**, 25 deg magnet
**4.3-4.7**, wandering compass **2.0, 2.2, 2.6 — and 7.8**. The worst wander
steps harder than the strongest magnet. Coherence and erraticness overlap too:
over a ten-second window a slow random walk *is* a steady offset.

The difference only exists across the whole incident. Something placed beside a
sensor pushes one way and keeps pushing — both magnets stayed between -47 and
-15 degrees and **never crossed zero**. A compass going bad has no direction it
is trying to go, so it wanders through the truth and out the other side: every
wander seed spanned about -40 to +68 and scored 0.48 to 0.97. **Zero against a
half, with nothing in between.**

> "Something placed beside the compass pushes it one way and keeps pushing.
> This one has been wrong in *both* directions — nothing you could put next to
> it does that. It is the sensor."

**3. The results card was claiming a score it had not measured.** `blame_correct`
and `cause_correct` were the string literals `"8 of 8"` and `"7 of 8"`. The
second went stale the moment this was fixed, so the card would have stood in
front of judges *under*-claiming — from a hardcoded string nobody would think
to check. Both now run the real case lists. **A results card that states a
number it did not measure is the one thing on it that cannot be trusted**, and
it is the same card we invite people to re-run.

And a near-miss worth keeping: the first version of fix 1 counted time in state
only on frames where the check could be *read*. GNSS is 5 Hz against 20 Hz
frames, so it ran at a quarter of real time, and a seized odometer that used to
be named in 7 s took **39**. The alibi had been steady all along; the clock was
wrong. Found by the dress rehearsal, not by the tests — which is twice now
that the rehearsal has caught what the suite could not. **Two tests pin the
fix**, one that a wandering compass never accuses the GPS and one that the
magnet and the wander land on opposite causes.

### The map draws what is between the roads, not only the roads

Roads on their own are a diagram. `bake_map.py` takes a second, optional
extract — buildings, water, landuse — and bakes it into the same offline file:
**1579 roads, 462 buildings, 61 water bodies, 29 green areas, 74 named
landmarks**, 630 KB, still read from disk with the wifi off.

The corridor turned out to carry the **VIT campus itself**, **Maambakkam Lake**
a kilometre from the junction, and the **TAFE and TAL works** at the far end —
which is worth saying out loud, because it means this really is a lorry
corridor and not just a road we liked the look of. Names come from OSM, not
from a list somebody typed.

Three rules that keep it from getting in the way:

- **Detail is decided in pixels, not hectares.** Buildings vanish below 3 px
  and names below 70 px, so whole-run zoom stays clean and follow zoom becomes
  a street map. One rule, every scale.
- **Place names are bottom priority (20).** A lake name must never be what
  pushes the separation figure off the map.
- **Draw order is areas, then roads, then trails.** Nothing filled can paint
  over the two tracks, which are the only things on the map anyone must see.

**`HALF_BOX_M` is a measured number, not a chosen one.** It was 2600 while the
route itself ends 2544 m out — 56 m of margin — so a judge steering the spoofed
GPS drove it straight off into blank canvas. It is 3500 now, which leaves 2 km
of real map beyond the end of a hard 90-second drive.

Raising it is **not free, and the cost lands on detection**: every road in the
box is a road the check must honour. 2600 m gives the check 30 roads, 3500
gives 43, 4000 gives 75 — most of the way back to the hundred that cost a
metre per second at Sriperumbudur. At 3500 the sweep came back *identical*,
because the added roads sit 2.6-3.5 km out while a walk-off drags the position
only a few hundred metres. Measured, not assumed — and it would not have held
at 4000.

**Drawing and checking use the same box, deliberately.** Drawing roads we do
not check would put a spoofed position visibly on a road while the console
called it off-road, and a judge would be right to ask.

### Owners

| Owner | Area | State |
|---|---|---|
| Abishek | Simulator | tasks 1-3 done — flight, attacks, truck, roads |
| Abishek | Phone app | task 6 — built and working; see the handover before changing it |
| — | Detector | stages 1-10 done |
| — | Console | map, banner, verdict, fleet, phone view, report |

### The judge drives a sensor — and two detector bugs it exposed

`console` → **You are the sensor**. The vehicle drives its real route; the
judge takes a sensor over (`kind: "puppet"`) and drives it with the arrow keys.
Same control, two verdicts, and the difference is only how they press:

| They do | Reads as | In |
|---|---|---|
| Drive the GPS away | **TAMPERED** `gnss/attack` | 9.7 s |
| Let go — GPS freezes | **FAILED SENSOR** `gnss/fault` | 3.0 s |
| Turn the compass | **FAILED SENSOR** `mag/fault` | 5.0 s |
| Freeze the wheels at 0 | **FAILED SENSOR** `odom/fault` | 7.0 s |

**All 17 cases are named and checked** — `python -m harness.usecases`, and
[docs/SCENARIOS.md](docs/SCENARIOS.md) lists every one with the buttons that
produce it. Three of them answer more narrowly than you might expect, and the
table says so rather than leaving them out: a height spoof and a squeezed
barometer are detected but not attributed (only two sensors measure height, so
one failing check between them cannot name which), and two sensors taken at
once names one of the two liars rather than both.

The stopwatch and scoreboard run **in the browser only** — the click, the
timer and the comparison all happen in the page. The detector is never told an
attack was injected, which is the only reason the number means anything.

Building it exposed four real detector faults, every one invisible to the 64
tests, because a puppet produces ordinary bad readings and ordinary bad
readings are what the ten stages are meant to catch:

**1. A frozen GNSS was invisible.** The stuck flag only existed on frames that
carried a fix, and three frames in four have none, so it flickered at 5 Hz and
the hysteresis never promoted it. `health.py` now holds that verdict between
fixes. *Not having a fix this instant is not evidence the receiver is fine* —
the same "absence of evidence" trap as the course check.

**2. `gnss-odom:distance` was declared in `profiles.py` and never
implemented**, so it reported OK forever and a seized odometer reading zero
while the lorry drove was undetected. Now implemented in `crossvalidate.py`.

**3. "Stuck" was defined too literally to catch a broken sensor.** It demanded
20 bit-identical readings in a row, while the simulator models a seized sensor
the way they actually fail — sitting on the last value and throwing the
occasional spike. One spike every dozen frames resets a consecutive counter
forever, so a plainly frozen compass read as perfectly healthy. Now counts
repeats across the window instead, and the frozen compass, barometer and
wheels are all named in 3-7 s.

**4. An alibi could excuse a sensor that had already convicted itself.** Two
ways, both fixed in `blame.py`: a failing check against the *road network* now
convicts outright, because the road cannot be wrong and no amount of agreeing
with the compass changes that — without it a teleported GPS collected an alibi
and blame drifted onto the innocent accelerometer. And a sensor failing its own
health check can no longer be cleared: a frozen GPS stops on the carriageway,
keeps passing the road check, and the blame landed on a perfectly good
odometer, which sends a mechanic to the wrong part of the lorry.

**5. An alibi could come from a check that had not caught up yet.** A check
averaging over a window is still describing the past for the length of that
window, so it cannot vouch for a sensor that has just started lying. The
instant a magnet touches the compass, the course check is comparing GPS course
against four seconds of mostly pre-magnet heading — it passes, and it cleared
the compass. The only suspect left was the motion sensor, which is working
perfectly, and it was named **with confidence 1.0 for six seconds on a truck
and fourteen on a drone.** A confidence threshold would not have saved it: the
wrong answer scored 1.0 and the right one 0.5. `PairScore.window_s` now marks
the averaging checks and blame refuses their alibis, so those seconds read
`cannot_isolate` instead — which is the truth.

Blame also now accepts **stale** passes as alibis. GNSS is 5 Hz against 20 Hz
frames, so on three frames in four every check involving it is unevaluable —
the horizontal domain was left holding one check, which can never clear
anybody, and blame oscillated at 15 Hz between naming the right sensor and
shrugging. The crossvalidator already carries the last real reading forward;
this is the other half of that decision.

And one near-miss worth remembering: the first version of fix 1 watched
**latitude alone**, so a truck waiting at a signal read as a broken receiver,
and an east-bound walk-off left latitude untouched. It made the truck floor
*look* like 0.5 m/s — a number that came from a parked lorry, not from
detection. The check now packs both axes. **A measurement that improves for a
reason you cannot explain has not improved.**

### Two things that only fail live — the test suite cannot see either

The tests drive `Pipeline` directly. Everything between the UDP socket and the
browser is untested by them, and both of these were found by actually running
the demo rather than by running the tests.

**1. Kill every stale process before a run.** Two `detector.server` instances
from different sessions can both hold :8080, and the older one answers — so the
console served the *previous* road network while the source on disk was
correct. Nothing errors. The map just quietly looks right and is wrong.
Before any rehearsal or demo:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'detector\.server|simulator\.control' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

**2. "Clear" has to stop the run, not the screen.** It used to clear the
display only, while the simulator kept streaming — so an alert the operator
had just dismissed came back within a tenth of a second and the button looked
broken. `/control/clear` now resets the simulator and kills any fleet sender
first. Measured at 0.06 s, which is the phase-12 "reset under two seconds"
requirement met with room to spare.

### The console is two pages, and splitting it found a dead panel

`/` is the evidence display, `/drive` is the control surface. One server, two
paths — not two ports: `/stream` is SSE on a `ThreadingHTTPServer`, so a second
page costs one more thread on the same shared snapshot, while a second port
would mean a second process to kill, and a stale one answering has already cost
us a rehearsal.

**The display was losing to its own furniture.** The raw feed and the scenario
row took 310 px off a 960 px window, and the map's scale is *height*-bound, so
the two tracks were drawn at 0.29 px/m — a 45 m divergence is 13 px, and the
vehicle markers are 22 and 28 px wide. **The markers were bigger than the gap
they exist to show.** Moving both to `/drive` gives 0.53 px/m. Narrowing the
side column buys nothing: width-fit is 1.05 px/m against height's 0.29, so
height loses by 3.5x no matter how wide the rail is.

**And `renderActions()` was never called.** Eleven attacks — jump it 300 m,
replay elsewhere, hold a magnet, squeeze the barometer — had a table, had
cases in `harness/usecases.py` checking them end to end, and had no button in
the console. `#actions` was empty on every run. `harness.usecases` posts to
`/control/inject` directly, so it passed 17/17 against buttons a judge could
not press. **A harness that talks to the API cannot see a hole in the UI.**

### One button system, one answer band, one theme switch

**There were seven button styles** — `.scenarios button`, `button.target`,
`button.atk`, `button.atk.primary`, `button.key`, `.actions button`,
`.toggle` — each with its own size, radius, weight and hover. Thirty controls
on `/drive` in seven visual languages is why nobody could tell what to press
first. Everything now inherits from one `button` rule and differs only by
deliberate modifier. `/drive` is numbered **1 Start a run · 2 Make a sensor
lie · 3 What came back**, and the eight scripted runs are folded into a
`<details>` rather than being most of the button count.

**The rail was eight panels of equal weight in one scrolling column**, so the
verdict could sit below the fold while a reference table sat above it. The
answer now lives in a band that never scrolls — state, one sentence, and the
separation figure — with everything else scrolling underneath. Nothing was
deleted; every panel is still there, further down.

**Light and dark, on a switch in the header**, remembered and shared between
the two pages. A hall is either bright with the blinds up or dark with a
projector and the right answer is opposite in each. This only works because
the canvas reads its colours from the stylesheet: `refreshColours()` re-reads
every token on a switch, or the page turns dark around a map still painting
roads on paper. **Never hardcode a colour in the console** — light and dark
are checked for token parity, and a literal in a script breaks dark silently.

**Zoom and pan.** `+` / `−` / scroll / drag, plus **Whole run** and
**Follow**. Manual is sticky on purpose: a view that snapped back to the
vehicle a moment after you dragged it would be worse than no control at all.
Zoom is about the cursor, so what you aim at stays where it is.

### Ten things wrote on the map and none of them looked first

With four vehicles clustered that produced exactly what you would expect:
LIKELY TRANSMITTER across a drone, a vehicle name across the separation
figure, and a compass note over both with its start lost behind another chip.

Nothing draws a label now; it asks for one. They are placed together at the
end of the frame, highest priority first — the two positions and the gap
between them outrank a fleet member's name — and anything that cannot find
clear space is nudged, then dropped. **Dropped deliberately:** a map that
hides its fourth-most-important note is readable; one that prints all ten on
top of each other is not, and it is the important ones that get buried,
because they are drawn first.

The legend and the scale bar are HTML sitting *on top of* the canvas, so the
canvas could not see them and was writing underneath both. Their boxes are
measured off the elements themselves each frame rather than guessed, so
moving them in the stylesheet cannot silently start hiding labels again.

### The map follows the vehicle now, and there are two view modes

Fitting the whole route every frame (1331 m then, 3960 m now) is what produced
0.29 px/m. The
camera now follows the vehicle at **4 px/m** and pulls back only as far as it
must to keep the spoofed position on screen beside the real one — so the
harder the attacker drags, the wider the shot. It frames itself.

| separation | before | now |
|---|---|---|
| 20 m | 6 px | **80 px** |
| 45 m — the road-check tolerance | 13 px | **180 px** |
| 91 m — GPS's error in the fallback demo | 26 px | **332 px** |

Measured live at 30 m of real walk-off: 9 px before, **119 px** now.

**Fleet is a separate mode and has to be.** `CLUSTER_RADIUS_M` is 3000 and a
zone runs 400 m+; the one we measure is 823 m across. At follow zoom its
circle would be several times the width of the map, so zones or more than one
vehicle switch the camera back to fit-everything. Idle does too.

**The inset is not decoration.** At 4 px/m the map covers ~300 m and a lorry
crosses it in twenty seconds, while the truck walk-off does not settle to a
verdict for the better part of a minute. Without the whole run in the corner,
the place where
the two tracks separated is hundreds of metres off the edge by the time the
console names the sensor — the evidence gone exactly when the answer arrives.

**Roads are drawn to scale in metres, not at a fixed pixel width.** A fixed
16 px trunk road is 4 m wide at follow zoom while the lorry drawn on it is 5:
a vehicle wider than the highway, which reads as a broken picture. To scale,
the truck sits in a lane and a spoofed position off the carriageway is
visibly off it. That is the road check in `blame.py`, drawn.

Two costs found by running the paint loop against a live spoofed snapshot
rather than by reading it: 4662 road points were being re-projected twice a
frame — 233,000 `Math.cos` of a constant latitude per second — now projected
once per run with a bounding box per road; and the trails were one canvas
stroke per segment, 53,000 a second 55 s into a run and still climbing, now
ten bands per trail. 430,788 strokes over 200 frames became 7,884.

The stopwatch stays on `/drive` on purpose. Getting the number onto the big
screen would mean routing the judge's click through the server, and although
the pipeline would never read it, "the timer goes through the detector but the
detector does not look at it" is not a sentence to be saying to a sceptic. Read
it out loud instead.

### Known weaknesses — say these out loud, do not hide them

- **Walk-off below 2 m/s is not detected on a drone**, and below 1 m/s on a
  truck. Slower than our own drift. At 1 m/s an attacker needs a quarter of an
  hour to move a lorry a kilometre off its route — which is the honest way to
  put it: we do not stop the attack, we make it slow enough to notice.
- **And the truck floor depends on the map, not only on us.** 1 m/s here, 2 m/s
  on the denser Sriperumbudur corridor. Quote the floor with the corridor it
  was measured on, or it is not a number.
- **Dead reckoning buys about 40 seconds**, not minutes. Past that our drift
  overtakes even a 3 m/s spoof, which is why the console counts down and then
  says "stop or land".
- **A parked lorry shows this plainly, so make the parking beat promptly.**
  Measured over three runs: the wheels read exactly 0.00 within 8 s of the
  hold, while our own estimate bottoms out at 0.4-1.2 m/s and then *climbs*
  — 1.16 to 1.75 m/s over the next twenty seconds, which is accelerometer bias
  integrating with no GPS to correct it. Leave the parked lorry on screen for a
  minute and a sharp judge will see our estimate walking away. Name it first;
  it is the same drift already on the card.
- **A wandering compass answers `cannot_isolate` for part of the incident**,
  and that is now the honest answer rather than a bug. While *both* heading
  checks are failing it names the compass; while only the course check is, two
  sensors are equally implicated and it says so. It no longer blames GPS.
  ~~A slowly drifting compass is not reliably attributed~~ — fixed; see below.
- **Detection latency is 15-18 s** on a drone because the course check needs a
  straight segment. The attack is caught at the next one. On the truck run it
  is 36-46 s at 3 m/s, because the road check has to wait for the reported
  position to walk the full 45 m tolerance off the carriageway.
- **The truck alerts before it can name the sensor.** The console reads
  `cannot_isolate` first and the verdict settles to `gnss / attack` a few
  seconds later — 66 s in the last dress rehearsal, against a 3 m/s walk-off
  starting at 35 s. This is rule 5 behaving correctly, not a bug — but
  **narrate it**, or
  it looks like one. "It is telling you something is wrong the moment it knows,
  and refusing to name a culprit until the evidence supports one." A judge who
  sees that gap unexplained reads it as flakiness; a judge who is told to expect
  it reads it as restraint.

### The written report can now be a model

`detector/narrate.py` calls Claude with the stored record when
`ANTHROPIC_API_KEY` is set. With no key, no network, or any failure at all it
falls back to the template and the report says `generated_by: template` — a
blank panel in front of judges is the worst outcome available, and a template
passing for a model is the second worst.

Raw `urllib`, not the SDK, because of rule 6: a demo that needs `pip install`
on the morning has a new way to fail.

**Two tests pin the on-stage claim.** One asserts no detector stage imports
`narrate` — structurally, because a behavioural test would pass on a build
where a stage called the model and happened to agree with itself that
afternoon. The other asserts that with no key the report still arrives and
still labels itself honestly.

### Still to do

**Rehearsal (phase 12), out loud.** `python -m harness.dressrehearsal` drives
all ten beats through the browser's own endpoints and reports which land — it
found a walk-off bearing that takes over 110 s to name where another takes 70.
But it presses buttons; it cannot say the sentences. Five run-throughs from
[docs/RUN-SHEET.md](docs/RUN-SHEET.md), wifi off.

A demo that has never been run start to finish will break in the room — that
is the normal outcome, not bad luck.

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
| Where the demo drives | **VIT Chennai to the Mambakkam junction**, real names, real geometry | The judges drove in on this road. Moved here from Sriperumbudur once the corridor was swappable; the origin, the road name and a re-bake are the whole change — see `simulator/chennai.py` and `bake_map.py` |

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
| **[docs/SCENARIOS.md](docs/SCENARIOS.md)** | Every case a judge can produce, how to press it, what it says. Checked by `harness/usecases.py`. |
| **[docs/MANUAL-TESTS.md](docs/MANUAL-TESTS.md)** | The same 17 as a by-hand checklist: which vehicle, how long to wait, measured timings, what counts as a fail. |
| **[docs/RUN-SHEET.md](docs/RUN-SHEET.md)** | The demo in order, with the words. Setup, seven scenes, what to say where it fails, and the parachute. |
| [docs/handover/06-abishek-phone-app.md](docs/handover/06-abishek-phone-app.md) | The phone app: how to run it, the three decisions behind it, and the bugs not to reintroduce. |
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
