# Task 6 — The phone app

**For:** Abishek · **Branch:** `main` · **Demo is in 24 hours**

The app is built, installed on a phone, and working. This is what it does, how
to run it, and what is worth changing. **Read the last section before you
change anything** — the demo is tomorrow and some of what looks unfinished is
deliberate.

---

## What it is for

The operator console runs on a laptop. The person who actually owns the lorry
is not standing in front of it — they are somewhere else, and the first they
should learn about a spoofed container is their phone going off in their
pocket. That is the whole app.

It shows **one line**: what happened, and what to do. Not a map, not a chart,
not a sensor list. The console has all of that on a screen with room for it;
someone glancing at a phone needs the answer to one question.

---

## Run it in five minutes

**Install the built APK** — no build needed:

```powershell
adb install -r android/SensorSentry-debug.apk
```

**Or build it yourself:**

```powershell
cd android
gradle assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Gradle 8.13 and JDK 17 are already on this machine, and `local.properties`
points at the SDK. There is **no Kotlin plugin, no AndroidX and no third-party
library** — that is on purpose, so the build cannot fail on dependency
resolution the morning of a demo.

**Then, to see it do anything**, the console has to be running on a laptop:

```powershell
python -m detector.server        # prints the address to type into the app
python -m simulator.control --quiet
```

Open the app, put that address in the box, press **Watch**, then start a run
on the console and take a sensor over. Full sequence in
[docs/RUN-SHEET.md](../RUN-SHEET.md).

---

## How it works

Two files, both Java.

**`WatchService.java`** — a foreground service that polls
`http://<laptop>:8080/snapshot` once a second. When the run goes to `ALERT` it
posts a heads-up notification, plays the bundled tone on the alarm stream and
vibrates. It broadcasts every reading to the Activity.

**`MainActivity.java`** — one screen, built in code rather than XML because it
is four views. Address box, Watch button, and the headline.

### Three decisions to know before you change them

**It polls; it does not read the event stream.** The console publishes SSE and
reading it would be tidier. It would also mean owning reconnect logic on a
phone that moves between cells and sleeps its radio. A one-second poll is
self-healing by construction — every request either works or is retried a
second later — and a second of latency is nothing against a detection that
takes ten.

**It reports; it does not decide.** Every word on that screen is read off the
console's own snapshot. The phone runs no detection of its own. If it did,
there would be two systems that could disagree about whether a lorry was being
stolen, and the demo would have to explain which one to believe.

**An unnamed sensor stays unnamed.** When the console says `cannot_isolate`
the phone says "SOMETHING IS WRONG" — never a guess. The console refusing to
name a sensor when the evidence will not support it is the most careful thing
the system does, and a phone that turned that into a confident accusation
would undo it.

---

## Three bugs already found and fixed — do not reintroduce them

**The alarm was silent while looking perfectly healthy.** It asked the phone
for its default notification sound; on the test handset that setting is empty,
so the channel was created pointing at a URI resolving to nothing. Nothing in
the notification dump looked wrong. **The tone now ships inside the APK**
(`res/raw/alert.wav`, generated). Do not go back to
`RingtoneManager.getDefaultUri` — it depends on a device setting for the one
thing this app exists to do.

**Changing a channel's sound in code does nothing.** Android fixes it the
first time it sees the channel. That is why the id is `sensorsentry.alert.v3`
and the two dead ones are deleted at startup. **If you change the tone, bump
the id again** or you will test a change that never took effect.

**Android 13+ drops every notification until `POST_NOTIFICATIONS` is granted.**
Asked for on first launch. If you refactor `onCreate`, keep it — without it the
app looks like it works and does nothing.

---

## Worth doing, in the order I would do them

**1. A settings screen instead of the address box.** The address is setup, not
information, and it disappears once watching. But it is still an `EditText`
sitting on the main screen. A proper settings sheet would be tidier.

**2. Remember more than one console.** A fleet has several; the app stores one
address in `SharedPreferences`.

**3. History.** Right now it shows the current state only. A list of the last
few incidents, tapped through to the stored evidence record, is the obvious
next thing a fleet manager would ask for.

**4. Wake the screen on an alert.** It posts a heads-up notification, which is
enough for a phone in a hand. A phone face-down on a table stays dark.

**5. The map.** Deliberately absent — see below.

---

## Before you change anything

**The demo is in 24 hours and this app works.** Verified end to end over wifi
with the USB cable unplugged: fresh run, GPS taken over, console alerted at
18.2 s, phone went red with "GPS SPOOFING DETECTED · TRUCK-17", tone played,
alarm withdrew itself when the console cleared.

So:

- **Work on a branch.** `main` is the demo build.
- **Do not add the map yet.** It was left out on purpose: one line on a phone
  is a stronger demo than a small map on a small screen, and it is what a real
  fleet manager's phone would do. After the demo, by all means.
- **Do not add libraries.** No AndroidX, no Retrofit, no OkHttp. Two files and
  the standard library is why this builds first time on any machine.
- **If you change the alarm path, test it on a phone with no default
  notification sound set** — that is the case that broke it once already and
  it is invisible everywhere else.

Anything you are unsure about, ask before the demo rather than after.
