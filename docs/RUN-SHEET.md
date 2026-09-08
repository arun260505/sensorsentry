# Run sheet

The demo, in order, with the words. Roughly seven minutes, and it stops
cleanly at four if you are cut short.

Timings below are **measured on the live stack**, not estimated — run
`python -m harness.dressrehearsal` and it drives every beat and prints them
back. But it presses the buttons; it cannot say the sentences. That half only
gets rehearsed by standing up and doing it, and it is the half that goes wrong
in the room.

---

## Before anyone is watching

**Kill everything first.** Two servers can hold the same port and the older one
answers, so the screen shows last week's code and nothing errors.

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'detector\.server|simulator\.control' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Two terminals:

```powershell
python -m detector.server
python -m simulator.control --quiet
```

Two browser windows, side by side:

| Window | URL | Who looks at it |
|---|---|---|
| **Display** | http://localhost:8080 | the room |
| **Controls** | http://localhost:8080/drive | you, and then them |

Then: **turn the wifi off** and reload both. If anything breaks, it breaks now
and not in front of anybody. Nothing in the demo needs a network — the map is
a file on disk.

Finally, `python -m harness.dressrehearsal`. Three minutes, and it tells you
whether this build is safe to show.

---

## Scene 1 · The problem (45 s, no screen)

> "A GPS signal reaches you from twenty thousand kilometres up, and it arrives
> weaker than the noise in the room. Anybody with a few hundred rupees of radio
> can drown it out and hand a lorry a position it will believe completely.
>
> Every defence sold today is an antenna — new hardware, per vehicle, for a
> fleet that already exists. Ours is software, and it goes onto lorries that
> are already on the road.
>
> The idea is one sentence. **An attacker can fake the signal a vehicle
> receives. He cannot fake what the vehicle physically feels.**"

---

## Scene 2 · An honest delivery (45 s)

Press **Start · truck**.

> "This is the Chennai Port container run, the last leg — NH-48 into the
> Sriperumbudur junction, then off toward the estates. Those are the real
> roads; the map is OpenStreetMap data, and the lorry is driving the actual
> carriageway."

Point at the legend.

> "Four sensors, four lines. They are on top of each other, so you only see
> one — that is what agreement looks like. GPS two metres from our own
> estimate, compass one degree, wheels a tenth of a metre a second.
>
> Three minutes of this and it says nothing at all. That matters more than
> anything I am about to show you: a system that cries wolf on an honest
> delivery gets switched off in a week."

---

## Scene 3 · Hand over the controls (2 min) — **the demo**

Turn the second screen toward them.

> "I would rather not attack it myself. Would you?"

**Let them press it.** Wait for the run to settle first — about twenty seconds
from Start, or the lorry is still pulling away.

### They drive the GPS · caught in ~10 s

`GPS` → arrows.

> "You are the GPS now. The lorry is still driving its real route — only the
> position it reports is doing what you say."

When the lines part and the banner goes:

> "**Tampered.** It named the GPS. Look at why —"

Point at *Why we say that*:

> "The compass, the gyro and the wheels all still agree with each other. Only
> the GPS disagrees, with all three of them. That is the whole method on one
> panel."

### They let go · caught in ~3 s

Same sensor. **Press nothing**, or space.

> "Now stop. Don't do anything."

> "**Failed sensor.** Same sensor, same control, opposite verdict — and the
> only difference is whether your finger was on the key.
>
> That distinction is the thing nobody else does. A spoofed lorry is a police
> matter. A broken receiver is a workshop matter. Get it the wrong way round
> and you send the wrong people, twice."

### The magnet · settles in ~20 s

`Compass` → **Hold a magnet**.

> "Nothing is broken here and nobody is transmitting. The magnetic field the
> compass sits in has been changed."

It will say *not sure yet* first. **Say so before they ask:**

> "Watch — it knows something is wrong immediately, and it is refusing to name
> a sensor until the evidence supports one. There: the compass, and it is
> interference, not an attack. Three different findings, three different
> responses."

---

## Scene 4 · Park it (45 s) — **the closing image**

Spoof the GPS, let it run a few seconds, then press **Park the vehicle**.

> "The lorry is now stationary. Watch every honest sensor go quiet together —
> wheels zero, our own estimate zero, the position stops moving.
>
> And the GPS is still doing seventy kilometres an hour."

Let that sit.

> "A container standing still whose tracker is driving to Bangalore. That is
> the whole product in one picture."

---

## Scene 5 · Where it fails (45 s)

**Do this. Do not skip it.**

Set the drift slider to **0.5 m/s** and take the GPS.

> "It is going to get away with this one, and the scoreboard will say so."

> "Below two metres a second on a drone, and one on a lorry, the attack is
> slower than our own sensors drift and we genuinely cannot see it. That is
> our floor and it is a property of the physics, not a bug we will fix.
>
> What it costs the attacker is time. At one metre a second, moving a lorry a
> kilometre off its route takes him a quarter of an hour. We do not stop the
> attack. We make it slow enough to notice."

Two more, if they are engineers:

> "A dense road network helps the attacker. On the real Chennai corridor there
> are a hundred roads, so a drifting position keeps landing on one of them —
> we catch at two metres a second here where we caught one on a sparser map.
> That only became visible when we stopped drawing our own roads."

> "And a height spoof we detect but cannot attribute. Only two things on that
> aircraft measure height, so one disagreement between them cannot say which
> is lying. It says *not sure yet*, and that is the honest answer."

---

## Scene 6 · The proofs (45 s)

**Nothing is hardcoded.** Point at *Everything the detector receives*.

> "That is the entire input. No true position, no attack flag — there is
> nothing in there it could cheat with. The simulator is a separate process
> talking over a socket, and the detector rejects any field it does not expect.
>
> You chose the moment and the direction a minute ago. I could not have
> scripted it."

**Run it twice.** The seed changes every run, so the detection time changes.

> "Different number every time. A recording would give you the same one."

**The AI switch.** Press *Written report: on*.

> "One place a language model is allowed: writing this up afterwards, from the
> stored record. Turn it off and re-run the attack — the detection is
> identical, because nothing that decides anything can call it. There is a
> test that fails the build if a detector stage ever imports it.
>
> The physics is deliberately not a model. It has to run on the vehicle, at
> sensor rate, offline, and give the same answer every time an investigator
> replays it. That is not a network call."

*(With no key set it writes from a template and labels itself `template`.
Never let it pass for a model.)*

---

## Scene 7 · The fleet (30 s, only if time)

Press **fleet · attack zone**.

> "Four lorries in one area, three of them hit at the same moment. Any one on
> its own could be a failing sensor. Three at once in the same square
> kilometre is a transmitter — and there is the circle around where it must
> be. The fourth is warned before it drives into it."

---

## Closing (20 s)

> "Nine honest runs, no false alarms. Nine attacks, all nine caught, sixteen
> seconds on average. It names the guilty sensor eight times out of eight, and
> tells an attack from a breakdown seven times out of eight.
>
> Every one of those numbers comes out of a script you can run — and where it
> fails, that is measured too, and on the same card."

---

## If something goes wrong

| Symptom | Almost certainly |
|---|---|
| Map looks wrong, buttons do nothing | A stale server. Kill everything, restart, reload. |
| A target is struck through | Wrong vehicle — a lorry has no barometer, a drone no wheels. |
| Nothing happens when attacked | Acted too early. Give it twenty seconds from Start. |
| Verdict says *not sure yet* | Often correct. Wait — several settle after a few seconds. |
| It all falls over | **scripted runs** → `truck · theft`. Pre-built, same engine, still not hardcoded. |

The scripted runs are the parachute. They exist for exactly this.
