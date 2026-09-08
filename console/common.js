/* SensorSentry console — what both surfaces share.
 *
 * The console is two pages served by the one detector process:
 *
 *   /        the evidence display  — the screen the room watches
 *   /drive   the control surface   — the screen the driver touches
 *
 * WHY THEY ARE SEPARATE
 *
 * Two reasons, and the second is the one that matters.
 *
 * The display was losing to its own furniture. The raw feed and the scenario
 * buttons took 310px off the bottom of a 960px window, and the map's scale is
 * height-bound — so the two tracks that are the entire claim of this project
 * were drawn at 0.29 px per metre, where a 45 m divergence is thirteen pixels
 * and the vehicle markers are wider than the gap between them.
 *
 * And "the detector is never told you touched it" was a sentence in small grey
 * text. Now the controls live on a different screen from the verdict, in the
 * judge's own hands. The separation is something they can see rather than
 * something we assert — the same argument as simulator-and-detector-are-
 * separate-processes, one level up into the UI.
 *
 * ONE SERVER, TWO PATHS — NOT TWO PORTS.
 *
 * /stream is server-sent events and the server is a ThreadingHTTPServer, so a
 * second page costs nothing: it gets its own thread reading the same shared
 * snapshot. A second *port* would mean a second process to kill before a demo
 * — and a stale detector.server holding the old port, answering with last
 * week's map and erroring on nothing, has already cost us one rehearsal.
 * Same origin also means every /control POST keeps working unchanged.
 *
 * THE RULE THIS SPLIT MUST NOT BREAK: the stopwatch stays on /drive. It is
 * started by the judge's own click and compared against an alert arriving over
 * the ordinary stream. Routing that click through the server to put the number
 * on the big screen would not technically violate rule 2 — the pipeline would
 * never read it — but "the timer goes through the detector but the detector
 * does not look at it" is not a sentence to be saying to a sceptical judge.
 * The number is worth something precisely because it never goes near them.
 */

/* --- elements ------------------------------------------------------------ */

const el = (id) => document.getElementById(id);

/* Both pages carry some of the same furniture and neither carries all of it,
 * so everything shared is written to whatever exists and skips the rest. A
 * missing element is a page that does not want that readout, not a fault. */
function setText(id, text) {
  const node = el(id);
  if (node) node.textContent = text;
}

/* --- shared vocabulary ---------------------------------------------------
 * The words in docs/schema.md are field names. These are the words an
 * operator reads. Kept here so the two pages cannot drift into calling the
 * same sensor different things on different screens. */

const SENSOR_LABEL = {
  gnss: "GPS", imu: "Motion", baro: "Height", mag: "Compass", odom: "Wheels",
};

const FLAG_LABEL = {
  stuck: "frozen",
  out_of_range: "impossible reading",
  dropped: "silent",
  noisy: "degraded",
  no_fix: "no fix",
};

/* --- the hint line -------------------------------------------------------
 * Lives on /drive, because that is where the actions that fail are. On the
 * display these calls are no-ops rather than errors. */

function setHint(text, bad) {
  const node = el("hint");
  if (!node) return;
  node.className = bad ? "hint bad" : "hint";
  node.textContent = text;
}

async function post(path, body) {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setHint(data.hint || data.error || `failed (${response.status})`, true);
      return false;
    }
    return true;
  } catch (err) {
    setHint(String(err), true);
    return false;
  }
}

/* --- the run header ------------------------------------------------------
 *
 * Both pages carry the state pill: the driver needs to know what the room is
 * being shown, or they are pressing buttons blind. The display carries the
 * fuller version with the vehicle and the mode.
 */

function renderStatus(snapshot) {
  const s = snapshot.state;

  const violation = el("violation");
  if (violation) {
    violation.hidden = !snapshot.violation;
    if (snapshot.violation) {
      violation.textContent = `SCHEMA VIOLATION — ${snapshot.violation}`;
    }
  }

  if (!s) {
    setText("statepill", "IDLE");
    if (el("statepill")) el("statepill").dataset.state = "IDLE";
    setText("runid", "waiting for a run");
    setText("vehicle", "no vehicle");
    setText("clock", "t 0.0 s");
    return false;
  }

  setText("vehicle", `${s.vehicle_id} (${s.vehicle_type})`);
  setText("runid", s.run_id);
  setText("clock", `t ${s.t.toFixed(1)} s`);

  // Anchoring is not a state the detector reports; it is the window before it
  // has enough history to report anything, and saying so beats showing OK.
  const state = s.anchored ? s.state : "IDLE";
  setText("statepill", s.anchored ? state : "ANCHORING");
  if (el("statepill")) el("statepill").dataset.state = state;

  return true;
}

/* --- the stream ----------------------------------------------------------
 *
 * Server-sent events, one connection per page, both reading the same shared
 * snapshot on the server. Each page registers what it wants to do with a
 * frame; nothing here knows what those things are.
 */

const listeners = [];

function onSnapshot(fn) {
  listeners.push(fn);
}

function startStream() {
  const source = new EventSource("/stream");

  source.onmessage = (event) => {
    const snapshot = JSON.parse(event.data);
    for (const fn of listeners) fn(snapshot);
  };

  source.onerror = () => {
    setText("statepill", "NO LINK");
    if (el("statepill")) el("statepill").dataset.state = "IDLE";
    // EventSource reconnects on its own; restarting the server mid-demo
    // should not need a page reload on either surface.
  };
}

/* --- theme ---------------------------------------------------------------
 *
 * Canvas cannot read CSS variables, and the previous version mirrored them by
 * hand in a table with a comment asking future readers to keep the two in
 * step. They had already drifted — the map was drawing a cool blue grid over
 * a warm paper ground, and its legend swatches were a different amber from
 * the track they labelled.
 *
 * So the canvas reads the stylesheet instead. One place defines a colour, and
 * a dark theme becomes an edit to one block of CSS rather than a hunt through
 * two files for hardcoded whites.
 */

function themeColours(names) {
  const style = getComputedStyle(document.documentElement);
  const out = {};
  for (const [key, prop] of Object.entries(names)) {
    out[key] = style.getPropertyValue(prop).trim();
  }
  return out;
}
