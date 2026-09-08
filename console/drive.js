/* SensorSentry — the control surface.
 *
 * Everything a person presses. The run, the sensor they take over, the raw
 * stream, the written report. The verdict, the map and the evidence are on the
 * other screen, at /.
 *
 * THE RULE THIS PAGE MUST NOT BREAK: the detector is never told any of it. The
 * click happens here, the stopwatch runs here, and it is compared against an
 * alert arriving over the ordinary stream. That is the only reason the number
 * on screen is worth anything — see the note at the top of common.js for why
 * the stopwatch stays on this page rather than following the drama onto the
 * big one.
 */

let latest = null;

/* --- scenario buttons ---------------------------------------------------
 * Built from whatever the simulator says it has, rather than hardcoded here.
 * A judge picking the scenario off a live list is part of the argument that
 * nothing is staged: the list comes from the other process.
 */

/* Two words each: what it is, and what it is for. The vehicle has to be in
   the name — stripping the prefix left a "clean" button for the drone and
   another for the truck, side by side and indistinguishable. */
const SCENARIOS = {
  truck_clean:     ["Start · truck",     "the Sriperumbudur delivery"],
  drone_clean:     ["Start · drone",     "a survey flight"],
  drone_manoeuvre: ["drone · manoeuvre", "hard flying, no attack"],
  drone_walkoff:   ["drone · walkoff",   "GPS spoofing"],
  drone_fault:     ["drone · fault",     "sensor failure"],
  drone_magnet:    ["drone · magnet",    "magnet on compass"],
  truck_walkoff:   ["truck · walkoff",   "GPS spoofing"],
  truck_fault:     ["truck · fault",     "sensor failure"],
  truck_magnet:    ["truck · magnet",    "magnet on compass"],
  truck_theft:     ["truck · theft",     "cargo theft"],
};

/* The honest runs are the way in: press Start, then attack it yourself. The
 * scripted attacks are demoted rather than deleted — they are the fallback if
 * a laptop misbehaves in the room, and harness/results.py still measures
 * against them. They just stop being the first thing a judge sees, because a
 * row of buttons named "truck · theft" is what made this look canned. */
const START_WITH = ["truck_clean", "drone_clean"];

let running = null;

async function loadScenarios() {
  const box = el("scenarios");
  try {
    const res = await fetch("/control/scenarios");
    const data = await res.json();
    // A 503 still parses as JSON, so "no scenarios" and "nothing is
    // listening" looked identical — the page said "none offered" when the
    // simulator simply was not running.
    if (!res.ok || !Array.isArray(data.scenarios)) {
      box.innerHTML = '<span class="hint bad">' +
        (data.hint || data.error || "simulator control not reachable") + '</span>';
      return;
    }
    box.innerHTML = "";
    const offered = data.scenarios || [];
    const make = (name, scripted) => {
      const b = document.createElement("button");
      const [label, note] = SCENARIOS[name] || [name.replace(/_/g, " "), ""];
      b.textContent = label;
      b.dataset.note = note;
      if (scripted) b.classList.add("scripted");
      b.addEventListener("click", () => startScenario(name, b));
      box.appendChild(b);
    };
    for (const name of offered) if (START_WITH.includes(name)) make(name, false);
    const rest = offered.filter((n) => !START_WITH.includes(n));
    if (rest.length) {
      const tag = document.createElement("span");
      tag.className = "scripted-label";
      tag.textContent = "scripted runs";
      box.appendChild(tag);
      for (const name of rest) make(name, true);
    }
    // The fleet finale is not a simulator scenario — it needs four vehicles
    // at once — but on stage it should be one more button, not a terminal.
    const fleetBtn = document.createElement("button");
    fleetBtn.textContent = "fleet · attack zone";
    fleetBtn.dataset.note = "4 vehicles, 3 attacked";
    fleetBtn.addEventListener("click", async () => {
      await post("/control/reset");
      await post("/control/fleet");
      for (const b of box.children) if (b.classList) b.classList.remove("running");
      fleetBtn.classList.add("running");
      setHint("running fleet — 4 vehicles");
    });
    box.appendChild(fleetBtn);

    if (!box.children.length) box.innerHTML = '<span class="hint">none offered</span>';
  } catch (err) {
    box.innerHTML = '<span class="hint bad">simulator control not running — ' +
      'start it with: python -m simulator.control</span>';
  }
}

async function startScenario(name, button) {
  // Stop whatever is running first, so switching mid-flight cannot leave two
  // vehicles talking over each other on the same port.
  await post("/control/stopfleet");
  await post("/control/reset");
  await post("/control/clear");
  latest = null;
  held = {};
  renderHeld();

  const ok = await post("/control/start", { scenario: name });
  running = ok ? name : null;
  for (const b of el("scenarios").children) {
    if (b.classList) b.classList.toggle("running", b === button && ok);
  }
  setHint(ok ? `running ${name}` : "");
}

el("reset").addEventListener("click", async () => {
  await post("/control/stopfleet");
  await post("/control/reset");
  await post("/control/clear");
  running = null;
  for (const b of el("scenarios").children) {
    if (b.classList) b.classList.remove("running");
  }
  latest = null;
  held = {};
  renderHeld();
  setHint("stopped");
});

/* --- the written report -------------------------------------------------
 *
 * Two buttons, not one. The switch turns the feature on and off; a separate
 * action opens the report. Conflating them let the page and the server drift
 * apart — the button read "off" while the feature was on, and the sheet
 * opened empty because the fetch that fills it belonged to the other action.
 *
 * The server owns the state. The page reads it back from the stream every
 * frame rather than tracking its own copy, so the two cannot disagree.
 */

function renderReportControls(enabled) {
  el("reporttoggle").textContent = `Written report: ${enabled ? "on" : "off"}`;
  el("reporttoggle").dataset.on = enabled ? "1" : "0";
  el("reportopen").hidden = !enabled;
  if (!enabled) el("reportsheet").hidden = true;
}

el("reporttoggle").addEventListener("click", async () => {
  try {
    const res = await fetch("/report/toggle", { method: "POST" });
    const data = await res.json();
    renderReportControls(Boolean(data.enabled));
    if (data.enabled) openReport();
  } catch (err) {
    setHint(String(err), true);
  }
});

el("reportopen").addEventListener("click", openReport);
el("reportclose").addEventListener("click", () => {
  el("reportsheet").hidden = true;
});

async function openReport() {
  el("reporttitle").textContent = "Incident report";
  el("reportbody").textContent = "Reading the record…";
  el("reportsheet").hidden = false;
  try {
    const r = await (await fetch("/report")).json();
    if (r.report) {
      el("reporttitle").textContent = r.report.title;
      el("reportbody").textContent = r.report.body;
    } else {
      // Say why there is nothing, rather than showing an empty box and
      // leaving the reader to wonder whether it broke.
      el("reporttitle").textContent = "Nothing to report yet";
      el("reportbody").textContent =
        r.note || "No run has been recorded on this vehicle yet. " +
        "Start a scenario and let it reach a verdict.";
    }
  } catch (err) {
    el("reporttitle").textContent = "Could not read the record";
    el("reportbody").textContent = String(err);
  }
}

/* --- you are the sensor --------------------------------------------------
 *
 * The vehicle drives its real route. The judge takes a sensor over and drives
 * that instead, with the arrow keys, at a moment nobody scripted.
 *
 * The two cases that matter come from the same control:
 *   drive it   -> the reading moves in a way the vehicle never moved -> TAMPERED
 *   let go     -> the reading stops dead while everything else moves -> FAILED
 */

const AWAY_AFTER_S = 90;

const TARGETS = {
  gps: {
    sensor: "gnss", label: "You are the GPS",
    hint: "Arrows drive it. Stop, and it freezes where it is.",
    arrow: (dir) => ({ bearing_deg: { up: 0, right: 90, down: 180, left: 270 }[dir],
                       moving: true }),
    stop:  () => ({ moving: false }),
    show:  (d) => d.moving
      ? String(Math.round(d.bearing_deg || 0)).padStart(3, "0") + "°"
      : "frozen",
  },
  compass: {
    sensor: "mag", label: "You are the compass",
    hint: "Left and right turn it. The gyro does not follow.",
    arrow: (dir) => ({ turn_deg: (dir === "right" || dir === "up") ? 5 : -5 }),
    stop:  () => ({}),
    // null until the puppet has seen one frame and learned the real heading
    // to take over from. Showing "—" for that instant beats showing NaN.
    show:  (d) => d.heading_deg == null ? "—" : Math.round(d.heading_deg) + "°",
  },
  altitude: {
    sensor: "baro", label: "You are the barometer",
    hint: "Up and down move its reported height.",
    arrow: (dir) => ({ step_m: (dir === "up" || dir === "right") ? 10 : -10 }),
    stop:  () => ({ height_offset_m: 0 }),
    show:  (d) => (d.height_offset_m > 0 ? "+" : "")
                  + Math.round(d.height_offset_m || 0) + " m",
  },
  wheels: {
    sensor: "odom", label: "You are the wheel sensor",
    hint: "Hold it at zero while the truck drives on.",
    arrow: (dir) => ({ step_mps: (dir === "up" || dir === "right") ? 2 : -2 }),
    stop:  () => ({ speed_mps: 0 }),
    show:  (d) => d.speed_mps == null ? "—"
                  : (Math.round(d.speed_mps * 10) / 10) + " m/s",
  },
};

/* Everything else that can be done to each sensor, beyond taking it over.
 *
 * One table, so the console offers exactly what the simulator implements and
 * harness/usecases.py can check every entry end to end. If a button is here,
 * a case in that file says what it should produce; if it is not, a judge
 * cannot press it and be surprised.
 */
const ACTIONS = {
  gnss: [
    ["Jump it 300 m",     { kind: "attack", type: "teleport", strength: 300, bearing_deg: 90 },
     "you jumped the GPS 300 m"],
    ["Replay elsewhere",  { kind: "attack", type: "replay", strength: 250, bearing_deg: 45 },
     "you replayed a signal from elsewhere"],
    ["Slow walk-off",     { kind: "attack", type: "walkoff", strength: 3, bearing_deg: 90 },
     "you started a slow walk-off"],
    ["Cut it off",        { kind: "fault", type: "dropout", sensor: "gnss" },
     "you cut the GPS off"],
  ],
  mag: [
    ["Hold a magnet",     { kind: "interference", type: "magnet", strength: 30 },
     "you held a magnet to the compass"],
    ["Freeze it",         { kind: "fault", type: "stuck", sensor: "mag" },
     "you froze the compass"],
    ["Make it noisy",     { kind: "fault", type: "noisy", sensor: "mag", strength: 18 },
     "you made the compass noisy"],
    ["Cut it off",        { kind: "fault", type: "dropout", sensor: "mag" },
     "you cut the compass off"],
  ],
  baro: [
    ["Squeeze it",        { kind: "interference", type: "pressure", strength: -6 },
     "you squeezed the barometer"],
    ["Spoof height only", { kind: "attack", type: "altitude_only", strength: 60 },
     "you spoofed the height only"],
    ["Freeze it",         { kind: "fault", type: "stuck", sensor: "baro" },
     "you froze the barometer"],
    ["Make it noisy",     { kind: "fault", type: "noisy", sensor: "baro", strength: 12 },
     "you made the barometer noisy"],
  ],
  odom: [
    ["Freeze it",         { kind: "fault", type: "stuck", sensor: "odom" },
     "you froze the wheel sensor"],
    ["Make it noisy",     { kind: "fault", type: "noisy", sensor: "odom", strength: 12 },
     "you made the wheels noisy"],
    ["Cut it off",        { kind: "fault", type: "dropout", sensor: "odom" },
     "you cut the wheel sensor off"],
  ],
};

/* What the detector currently believes, in the judge's own words. */
const VERDICT = {
  attack:       ["TAMPERED", "someone is inventing this reading"],
  fault:        ["FAILED SENSOR", "the sensor has stopped telling the truth"],
  interference: ["INTERFERENCE", "something physical is affecting it"],
};

/* Which sensors each vehicle actually carries. A lorry has no barometer and a
 * drone has no wheels, so offering those targets on the wrong vehicle gives a
 * judge a button that does nothing — which reads as broken, not as absent. */
const FITTED = {
  drone: ["gnss", "mag", "baro"],
  truck: ["gnss", "mag", "odom"],
};

function fitted(sensor) {
  const type = latest && latest.vehicle_type;
  if (!type || !FITTED[type]) return true;      // unknown vehicle: offer all
  return FITTED[type].includes(sensor);
}

let target = "gps";       // which one the arrows drive
let held = {};            // sensor -> true, everything taken over
let hunting = null;
let swTimer = null;
const score = { you: 0, us: 0 };

function attackLive() { return latest !== null && latest !== undefined; }

function setAttackEnabled() {
  const live = attackLive();
  for (const node of document.querySelectorAll(
        "#attackpanel button, #attackpanel input, #attackpanel select")) {
    node.disabled = !live;
  }
  if (!live && Object.keys(held).length) { held = {}; renderHeld(); }

  // A target the running vehicle does not carry is disabled and says so,
  // rather than accepting a press and doing nothing.
  for (const button of document.querySelectorAll(".target")) {
    const has = fitted(TARGETS[button.dataset.target].sensor);
    button.disabled = !live || !has;
    button.classList.toggle("absent", live && !has);
  }
  const here = fitted(TARGETS[target].sensor);
  for (const button of el("actions").querySelectorAll("button")) {
    button.disabled = !live || !here;
  }
  el("taketarget").disabled = !live || !here;
  const type = latest && latest.vehicle_type;
  el("atkhint").textContent = !live
    ? "Press Start above, then take a sensor."
    : !fitted(TARGETS[target].sensor)
      ? `This ${type} has no ${target}. Try one of the others.`
      : TARGETS[target].hint;
}

function selectTarget(name) {
  target = name;
  for (const button of document.querySelectorAll(".target")) {
    button.classList.toggle("on", button.dataset.target === name);
  }
  // Rebuild the action list for the newly selected sensor. Leaving this out
  // meant #actions was never filled at all, and eleven attacks that
  // harness/usecases.py checks end to end had no button in the console.
  renderActions();
  renderHeld();
  setAttackEnabled();
}

function renderActions() {
  const box = el("actions");
  box.innerHTML = "";
  for (const [label, body, said] of ACTIONS[TARGETS[target].sensor] || []) {
    const button = document.createElement("button");
    button.textContent = label;
    button.disabled = !attackLive();
    button.addEventListener("click", async () => {
      if (latest && latest.state === "ALERT" && !hunting) {
        el("atkhint").textContent = "It is already alerting — let go of everything first.";
        return;
      }
      if (await post("/control/inject", body)) startHunt(said);
    });
    box.appendChild(button);
  }
}

function renderHeld() {
  const spec = TARGETS[target];
  const mine = Boolean(held[spec.sensor]);
  el("taketarget").textContent = mine ? "Let go of it" : "Take it over";
  el("taketarget").classList.toggle("ghost", mine);
  el("drive").hidden = !mine;
  el("drivewhat").textContent = spec.label;
  for (const button of document.querySelectorAll(".target")) {
    button.classList.toggle("mine", Boolean(held[TARGETS[button.dataset.target].sensor]));
  }
}

/* --- the stopwatch, timed here and nowhere else -------------------------- */

function startHunt(label) {
  hunting = { at: performance.now(), label };
  const sw = el("stopwatch");
  sw.hidden = false;
  sw.dataset.state = "hunting";
  if (swTimer) clearInterval(swTimer);
  swTimer = setInterval(tickHunt, 100);
  tickHunt();
}

function tickHunt() {
  if (!hunting) return;
  const secs = (performance.now() - hunting.at) / 1000;
  el("swlabel").textContent = hunting.label + " · not caught yet";
  el("swtime").textContent = secs.toFixed(1) + " s";
  if (secs >= AWAY_AFTER_S) endHunt(false, secs);
}

function endHunt(caught, secs) {
  if (!hunting) return;
  if (swTimer) { clearInterval(swTimer); swTimer = null; }
  const sw = el("stopwatch");
  sw.dataset.state = caught ? "caught" : "away";
  el("swlabel").textContent = caught ? "caught you in" : "you got away with it";
  el("swtime").textContent = secs.toFixed(1) + " s";
  if (caught) score.us += 1; else score.you += 1;
  el("scoreus").textContent = String(score.us);
  el("scoreyou").textContent = String(score.you);
  hunting = null;
}

/* Called on every snapshot: the live readout, and the stopwatch. An alert
 * only counts as a catch if it arrives after the judge's click — otherwise a
 * previous attack's alert is scored for this one, which would be us cheating
 * in our own favour, on stage. */
function attackWatch(state) {
  const box = el("verdictread");
  if (!state || state.state !== "ALERT") {
    box.dataset.state = "quiet";
    el("verdictword").textContent = Object.keys(held).length ? "nothing yet" : "—";
    el("verdictwhy").textContent = "";
  } else {
    const known = VERDICT[state.cause && state.cause.label];
    const named = state.blame && state.blame.guilty;
    if (known && named && named !== "cannot_isolate") {
      box.dataset.state = state.cause.label;
      el("verdictword").textContent = known[0];
      el("verdictwhy").textContent = SENSOR_LABEL[named] + " — " + known[1];
    } else {
      box.dataset.state = "unsure";
      el("verdictword").textContent = "not sure yet";
      el("verdictwhy").textContent =
        "something is wrong; not enough evidence to name one sensor";
    }
  }
  if (hunting && state && state.state === "ALERT") {
    endHunt(true, (performance.now() - hunting.at) / 1000);
  }
}

/* --- taking a sensor over ------------------------------------------------ */

async function takeOver() {
  const spec = TARGETS[target];
  if (held[spec.sensor]) {
    await post("/control/inject", { kind: "clear", which: "puppet:" + spec.sensor });
    delete held[spec.sensor];
    if (hunting) endHunt(false, (performance.now() - hunting.at) / 1000);
    renderHeld();
    return;
  }
  if (!attackLive()) return;
  if (latest && latest.state === "ALERT" && !hunting) {
    el("atkhint").textContent = "It is already alerting — let go of everything first.";
    return;
  }
  const ok = await post("/control/inject", { kind: "puppet", sensor: spec.sensor });
  if (!ok) return;
  held[spec.sensor] = true;
  renderHeld();
  startHunt("you took the " + target);
}

async function steer(body) {
  const spec = TARGETS[target];
  if (!held[spec.sensor]) return;
  try {
    const res = await fetch("/control/steer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ target: target }, body)),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return;
    el("driveheading").textContent = spec.show(data);
  } catch (err) { /* the run ended under us; the panel resets on its own */ }
}

function pressArrow(direction) {
  const spec = TARGETS[target];
  if (!held[spec.sensor]) return;
  steer(spec.arrow(direction));
}

/* --- wiring -------------------------------------------------------------- */

function initAttackPanel() {
  for (const button of document.querySelectorAll(".target")) {
    button.addEventListener("click", () => selectTarget(button.dataset.target));
  }
  el("taketarget").addEventListener("click", takeOver);

  for (const key of document.querySelectorAll(".key[data-arrow]")) {
    key.addEventListener("click", () => pressArrow(key.dataset.arrow));
  }
  el("driveoff").addEventListener("click", () => steer(TARGETS[target].stop()));

  const ARROWS = { ArrowUp: "up", ArrowRight: "right",
                   ArrowDown: "down", ArrowLeft: "left" };
  window.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    if (event.key in ARROWS) { event.preventDefault(); pressArrow(ARROWS[event.key]); }
    if (event.key === " ") { event.preventDefault(); steer(TARGETS[target].stop()); }
  });

  el("doclear").addEventListener("click", async () => {
    await post("/control/inject", { kind: "clear" });
    if (hunting) endHunt(false, (performance.now() - hunting.at) / 1000);
    held = {};
    renderHeld();
  });

  selectTarget("gps");
  setAttackEnabled();
}

/* The arrow keys only reach this window while it has focus, and a driver
 * watching the big screen will not notice that it does not. Say so, rather
 * than letting four dead keypresses read as a broken demo. */
function watchFocus() {
  const mark = () => {
    document.body.dataset.focus = document.hasFocus() ? "on" : "off";
  };
  window.addEventListener("focus", mark);
  window.addEventListener("blur", mark);
  mark();
}

/* --- the stream ---------------------------------------------------------- */

onSnapshot((snapshot) => {
  latest = snapshot.state;
  renderStatus(snapshot);
  renderReportControls(Boolean(snapshot.report_enabled));
  attackWatch(latest);
  setAttackEnabled();

  if (snapshot.raw) {
    el("feed").textContent = JSON.stringify(snapshot.raw, null, 1);
  } else if (!snapshot.state) {
    el("feed").textContent = "waiting for frames…";
  }
});

initAttackPanel();
watchFocus();
loadScenarios();
startStream();
