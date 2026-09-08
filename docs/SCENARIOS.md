# Every case, and how to produce it

Seventeen things a judge can do to this system, what each one is called, which
buttons make it happen, and what the detector says back.

Nothing here is a claim. `python -m harness.usecases` runs all seventeen
against the real pipeline and fails if any one stops behaving as written
below, so this file cannot quietly drift away from the code.

```bash
python -m harness.usecases            # the table
python -m harness.usecases --verbose  # with the evidence behind each verdict
```

**The three answers**, because they are three different instructions to whoever
is running the fleet:

| It says | Meaning | What the operator does |
|---|---|---|
| **TAMPERED** | someone is inventing this reading | stop; do not trust the position |
| **FAILED SENSOR** | the sensor has stopped telling the truth | book the vehicle in for service |
| **INTERFERENCE** | something physical is affecting it | move away from whatever it is |
| *not sure yet* | something is wrong, evidence won't name one sensor | investigate; do not act on a guess |

---

## Before anything else — the two ways to start

| Button | What runs |
|---|---|
| **Start · truck** | the VIT Chennai → Mambakkam container delivery |
| **Start · drone** | a survey flight |

Everything below is done to a run already in progress. Let it settle for
fifteen seconds or so first — the vehicle should be properly under way.

---

## 1. Nothing is wrong — the gate

These three must stay **silent**. If any of them ever raises an alert, that is
a defect and it outranks every feature on the roadmap: a system that cries
wolf on an honest delivery gets switched off within a week.

| Case | How | Says |
|---|---|---|
| Honest truck run | Start · truck, then leave it alone | silent |
| Honest drone flight | Start · drone, leave it alone | silent |
| Hard manoeuvring | scripted runs → drone · manoeuvre | silent |

---

## 2. TAMPERED — someone is inventing the reading

| Case | How | Says | When |
|---|---|---|---|
| **You drive the GPS** | GPS → Take it over → arrow keys | TAMPERED, gps | 9 s |
| Slow walk-off, 3 m/s | GPS → Slow walk-off | TAMPERED, gps | 61 s |
| GPS jumped 300 m | GPS → Jump it 300 m | TAMPERED, gps | 8 s |
| Meaconing / replay | GPS → Replay elsewhere | TAMPERED, gps | 8 s |
| Height-only spoof | Altitude → Spoof height only | *not sure yet* — see below | 8 s |

**The one to show first is the first one.** The judge holds the arrow keys,
the truck carries on driving its real route, and the system names the GPS.
They chose the moment and the direction; nothing about it was scripted.

The slow walk-off takes a minute because it is *meant* to be hard — it is the
attack a thief would actually use, and 61 seconds is our honest number for it.

---

## 3. FAILED SENSOR — it has stopped telling the truth

| Case | How | Says | When |
|---|---|---|---|
| **You let the GPS freeze** | GPS → Take it over → press space | FAILED SENSOR, gps | 3 s |
| Compass frozen | Compass → Freeze it | FAILED SENSOR, compass | 3 s |
| Compass goes noisy | Compass → Make it noisy | FAILED SENSOR, compass | 3 s |
| Barometer frozen | Altitude → Freeze it | FAILED SENSOR, barometer | 3 s |
| Wheels seized at zero | Wheels → Take it over → space | FAILED SENSOR, wheels | 7 s |
| GPS cut off | GPS → Cut it off | FAILED SENSOR, gps | 3 s |

**Do this one immediately after driving the GPS.** Same sensor, same control,
opposite verdict — and the only difference is whether the judge is pressing
the arrows or has let go. That contrast is the entire "attack or fault?"
argument, delivered in about fifteen seconds, by their own hands:

> "You just did both. You spoofed it, then you broke it. It told you which was
> which, and it never saw your keyboard."

---

## 4. INTERFERENCE — something physical is affecting it

| Case | How | Says | When |
|---|---|---|---|
| Magnet on the compass | Compass → Hold a magnet | INTERFERENCE, compass | 2 s |
| Pressure on the barometer | Altitude → Squeeze it | *not sure yet* — see below | 8 s |

This is the third failure the problem statement names — *manipulated
environmental inputs* — and it is the one most teams leave out. The compass is
not broken and nobody is transmitting anything; the magnetic field it sits in
has been changed. That needs a different response from either of the others,
which is why it gets its own word.

---

## 5. Where the answer is narrower than you might expect

**Say these out loud before a judge finds them.** A judge who finds an
unlisted failure has found a lie. A judge who finds a listed one has found an
engineer who measured their own limits.

**Height spoof, and pressure on the barometer — detected, not attributed.**
Only two things on this vehicle measure height: the GPS and the barometer. One
failing check between exactly two sensors cannot say which of the two is
wrong, so it reports *not sure yet* rather than picking one. A third height
source would fix it. We do not have one, and we would rather say so.

**Two sensors taken at once — names one of the two.** Take the GPS and the
compass together and it will name one of them, not both. With two of five
sensors lying in step, the truthful ones are outvoted. This is exactly why the
fleet check exists: one vehicle can be fooled, four in the same area cannot.

**Walk-off below our floor gets away.** 2 m/s on a drone, 1 m/s on a truck.
Set the drift slider below that and the scoreboard will credit the judge, and
it should — that attack is slower than our own sensors drift. At 1 m/s moving
a lorry a kilometre off its route takes a quarter of an hour. We do not stop
the attack; we make it slow enough to notice.

---

## 6. The fleet

| Case | How | Says |
|---|---|---|
| Four vehicles, three attacked | fleet · attack zone | one attack zone on the map, fourth vehicle warned |

Individually any one of them could be a failing sensor. Three at the same
moment in the same square kilometre is a transmitter, and the map draws a
circle around where it must be.

---

## Scripted runs — the fallback, not the demo

The pre-built scenarios still exist under **scripted runs** at the bottom of
the console. They are there in case a laptop misbehaves in the room, and
`harness/results.py` still measures against them.

They are deliberately not the way in. A row of buttons named `truck · theft`
is what made this look canned in the first place — a judge cannot tell a
scripted attack from a video, but they can tell when the controller is in
their own hands.
