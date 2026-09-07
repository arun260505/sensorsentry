# Manual test sheet

Seventeen cases, the exact conditions each one needs, and what counts as a
pass. Work down the list; tick as you go.

`python -m harness.usecases` checks all of these automatically. This sheet is
for doing it by hand, which is worth doing at least once before the room —
the automated checker drives `Pipeline` directly and never touches the socket,
the browser, or your keyboard.

---

## Setup — do this once

**Kill anything already running first.** Two servers can hold the same port
and the older one answers, so the screen shows the previous version of the
code and nothing errors.

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'detector\.server|simulator\.control' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Then, in two terminals:

```powershell
python -m detector.server
python -m simulator.control --quiet
```

Open **http://127.0.0.1:8080**.

---

## Rules that apply to every test

**1. Which vehicle.** They do not carry the same sensors, and this is the
commonest reason a manual test appears to fail:

| | GPS | Compass | Altitude | Wheels |
|---|---|---|---|---|
| **truck** | yes | yes | **no barometer** | yes |
| **drone** | yes | yes | yes | **no wheels** |

The console strikes through a target the running vehicle does not carry, so
if a button is greyed out and crossed, you are on the wrong vehicle — not
looking at a bug.

**2. Wait about 15–20 seconds after Start before you touch anything.** The
vehicle needs to be properly under way. The sharpest check compares which way
GPS says you are travelling against which way the compass says you are
pointing, and that comparison is only meaningful above 8 m/s and on a straight
stretch. Attack a lorry that is still pulling away and you are testing nothing.

**3. One thing at a time.** Press **Let go of everything** between tests and
wait for the banner to clear. The panel will refuse a new take-over while an
alert is still up — that is deliberate, so a previous attack's alert is never
scored as a catch for the next one.

**4. Restart the run if it has been going more than about three minutes.**
The truck reaches the yard and stops; a parked lorry is a poor test subject.

**5. A verdict settles — give it a few seconds.** Several cases alert first
and name the sensor after. That gap is the system refusing to guess, and it
reads as *not sure yet* while it waits. Do not call a case failed until the
"fail if" time has passed.

**6. Times below are from the moment you press.** They vary run to run,
because the seed changes every run — that variation *is* the proof it is not
hardcoded. Treat the "fail if" column as the real boundary.

---

## Group 1 — nothing is wrong. These must stay silent

If any of these alerts, stop and tell me. It outranks every other result on
this sheet.

| # | Case | Vehicle | Do | Pass | Fail if |
|---|---|---|---|---|---|
| 1 | Honest truck run | truck | Start · truck, watch 3 min, touch nothing | stays `OK`, no banner | any alert at all |
| 2 | Honest drone flight | drone | Start · drone, watch 3 min, touch nothing | stays `OK` | any alert |
| 3 | Hard manoeuvring | — | scripted runs → drone · manoeuvre, watch 3 min | stays `OK` | any alert |

---

## Group 2 — TAMPERED

### 4. You drive the GPS  ← show this one first

- **Vehicle:** truck (works on drone too)
- **Wait:** 20 s after Start
- **Do:** `GPS` → **Take it over** → hold an **arrow key** a few times
- **Watch:** the orange track (GPS says) peels away from the blue track (own sensors say)
- **Pass:** *reading you as* **TAMPERED**, names **gps** — measured 8.7 s
- **Fail if:** nothing by 40 s, or it names any sensor other than gps

### 5. Slow walk-off

- **Vehicle:** truck · **Wait:** 20 s
- **Do:** `GPS` → **Slow walk-off**
- **Pass:** **TAMPERED**, gps — but expect to wait **about 60 s**
- **Fail if:** nothing by 2 min
- **Note:** slow on purpose. This is the attack a real thief uses, and 60 s is our honest number for it. Do not narrate this one as fast.

### 6. GPS jumped 300 m

- **Vehicle:** truck · **Wait:** 20 s
- **Do:** `GPS` → **Jump it 300 m**
- **Pass:** **TAMPERED**, gps — measured 8.1 s. The orange track leaps off the road.
- **Fail if:** it blames imu, or says *not sure yet* for more than 20 s

### 7. Meaconing / replay

- **Vehicle:** truck · **Wait:** 20 s
- **Do:** `GPS` → **Replay elsewhere**
- **Pass:** **TAMPERED**, gps, within about 10 s
- **Fail if:** nothing by 40 s

---

## Group 3 — FAILED SENSOR

### 8. You let the GPS freeze  ← show this immediately after #4

- **Vehicle:** truck · **Wait:** 20 s
- **Do:** `GPS` → **Take it over** → then **press nothing**. Or press space / the ■ button.
- **Watch:** the orange track stops dead while the blue one drives on
- **Pass:** **FAILED SENSOR**, names **gps** — measured 3.2 s
- **Fail if:** it says TAMPERED, or blames the wheels
- **Why it matters:** same sensor and same control as #4, opposite verdict. The only difference is whether your finger is on the key. That is the whole "attack or fault?" argument in fifteen seconds.

### 9. Compass frozen

- **Vehicle:** truck or drone · **Wait:** 20 s
- **Do:** `Compass` → **Freeze it**
- **Pass:** **FAILED SENSOR**, compass — measured 3.6 s
- **Fail if:** nothing by 30 s

### 10. Compass goes noisy

- **Vehicle:** truck or drone · **Wait:** 20 s
- **Do:** `Compass` → **Make it noisy**
- **Pass:** **FAILED SENSOR**, compass, within about 5 s

### 11. Barometer frozen

- **Vehicle:** **drone only** — a lorry has no barometer
- **Wait:** 20 s · **Do:** `Altitude` → **Freeze it**
- **Pass:** **FAILED SENSOR**, barometer — measured 3.3 s
- **Fail if:** you are on the truck and the target is struck through — switch vehicle, that is not a bug

### 12. Wheels seized at zero

- **Vehicle:** **truck only** — a drone has no wheels
- **Wait:** 20 s · **Do:** `Wheels` → **Take it over** → press **space** (hold at zero)
- **Pass:** **FAILED SENSOR**, wheels — measured 7.1 s
- **Fail if:** nothing by 40 s

### 13. GPS cut off

- **Vehicle:** truck · **Wait:** 20 s
- **Do:** `GPS` → **Cut it off**
- **Pass:** **FAILED SENSOR**, gps — measured 3.3 s

---

## Group 4 — INTERFERENCE

### 14. Magnet on the compass

- **Vehicle:** truck or drone · **Wait:** 20 s
- **Do:** `Compass` → **Hold a magnet**
- **Pass, in two steps — this is the one to watch closely:**
  - it alerts at about **2 s** saying ***not sure yet***
  - it settles on **INTERFERENCE**, compass — about **8 s** on a truck, **16 s** on a drone
- **Fail if:** it ever names the **motion sensor**. It used to, confidently, for the first six seconds, because the course check averages over four seconds and was still describing the compass from before the magnet arrived — an alibi it had not earned. Fixed; if you see it again, tell me.
- **Say, while it is sitting on *not sure yet*:** "It knows something is wrong straight away, and it is refusing to name a sensor until the evidence supports one. Watch — there it is: the compass, and it's interference, not an attack. Nothing is broken; the magnetic field it sits in has been changed. That needs a different response from either of the other two."

---

## Group 5 — the three that answer narrowly. Expected, not broken

**Test these too, and learn the sentence for each.** A judge who finds an
unlisted failure has found a lie; one who finds a listed failure has found an
engineer who measured their own limits.

### 15. Height-only spoof

- **Vehicle:** drone only · **Do:** `Altitude` → **Spoof height only**
- **Pass:** alerts, says ***not sure yet***
- **Say:** "Only two things on this aircraft measure height — the GPS and the barometer. One disagreement between exactly two sensors cannot tell you which of them is wrong, so it refuses to name one. A third height source would fix it. We don't have one."

### 16. Pressure on the barometer

- **Vehicle:** drone only · **Do:** `Altitude` → **Squeeze it**
- **Pass:** alerts, says ***not sure yet***
- **Say:** the same sentence, from the other side.

### 17. Two sensors at once

- **Vehicle:** truck · **Do:** `GPS` → Take it over → arrows. Then `Compass` → **Hold a magnet**, without letting go of the GPS.
- **Pass:** names **one** of the two, not both
- **Say:** "Two of five sensors lying in step outvote the truthful ones. That is exactly why we check across the fleet — one vehicle can be fooled, four in the same area cannot."

---

## Group 6 — try to beat it. It should sometimes win

- **Vehicle:** truck · **Do:** set the drift slider to **0.5 m/s**, then `GPS` → Take it over → arrows
- **Expected:** **it gets away.** The scoreboard credits you.
- **This is correct.** Below 2 m/s on a drone and 1 m/s on a truck, the attack is slower than our own sensors drift.
- **Say:** "You beat it — and at that speed, moving a lorry a kilometre off its route takes you a quarter of an hour. We don't stop the attack. We make it slow enough to notice."

A scoreboard that always reads in our favour is a rigged fairground stall, and
a sharp judge will smell it. Let it lose this one in front of them.

---

## The fleet

- **Do:** bottom bar → **fleet · attack zone**
- **Pass:** four vehicles appear, three alert, a circle is drawn round the estimated transmitter, the fourth is warned
- **Note:** this replaces the single-vehicle run. Press **Stop** afterwards.

---

## If something fails

Tell me the case number, which vehicle, and what the verdict box said. The
three most likely causes, in order:

1. **A stale server** — the commonest by far. Kill everything and restart; see Setup.
2. **Wrong vehicle** — a struck-through target means that sensor is not fitted.
3. **Acted too early** — under 15 s the vehicle is not yet up to speed and the sharpest check is not valid.
