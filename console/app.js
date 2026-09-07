/* SensorSentry console.
 *
 * Draws its own map on a canvas. No tile server, no map library, nothing
 * fetched from the internet — the demo runs with wifi switched off, in front
 * of the judges, and a map that quietly fails to load would take the whole
 * thing with it. Local metres are what the detector works in anyway, so a grid
 * and a scale bar say more here than a street map would.
 */

const el = (id) => document.getElementById(id);

const canvas = el("map");
const ctx = canvas.getContext("2d");

/* Canvas cannot read CSS variables, so these mirror style.css. Change one,
   change the other, or the map stops matching its own legend. */
const COLOR = {
  grid: "#e6ecf1",
  gridMajor: "#cfd9e0",
  claimed: "#a35d07",
  witness: "#0b5c7a",
  link: "#b3352a",
  text: "#788894",
};
const CANVAS_BG = "#ffffff";

let latest = null;
let trails = { gnss: [], witness: [] };

/* --- canvas sizing ------------------------------------------------------ */

function resize() {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
window.addEventListener("resize", resize);

/* --- view fitting -------------------------------------------------------
 * One transform for everything drawn, so the two paths, the grid and the
 * scale bar can never disagree about how long a metre is.
 */

function computeView(w, h) {
  const pts = trails.gnss.concat(trails.witness);
  if (latest && latest.gnss) pts.push([latest.gnss.e, latest.gnss.n]);

  if (pts.length === 0) {
    return { cx: 0, cy: 0, scale: 1.2 };
  }

  let minE = Infinity, maxE = -Infinity, minN = Infinity, maxN = -Infinity;
  for (const [e, n] of pts) {
    if (e < minE) minE = e;
    if (e > maxE) maxE = e;
    if (n < minN) minN = n;
    if (n > maxN) maxN = n;
  }

  const pad = 70;
  const spanE = Math.max(maxE - minE, 40);
  const spanN = Math.max(maxN - minN, 40);
  const scale = Math.min((w - pad * 2) / spanE, (h - pad * 2) / spanN);

  return { cx: (minE + maxE) / 2, cy: (minN + maxN) / 2, scale };
}

/* Metres to pixels. North is up, so the vertical axis is flipped. */
function project(e, n, view, w, h) {
  return [w / 2 + (e - view.cx) * view.scale, h / 2 - (n - view.cy) * view.scale];
}

/* A grid step that stays a round number of metres at any zoom. */
function gridStep(scale) {
  const target = 80 / scale; // aim for roughly 80 px between lines
  const pow = Math.pow(10, Math.floor(Math.log10(target)));
  for (const mult of [1, 2, 5, 10]) {
    if (pow * mult >= target) return pow * mult;
  }
  return pow * 10;
}

/* --- drawing ------------------------------------------------------------ */

function draw() {
  const rect = canvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;
  ctx.clearRect(0, 0, w, h);

  const view = computeView(w, h);
  drawGrid(w, h, view);
  drawPath(trails.gnss, COLOR.claimed, view, w, h);
  drawPath(trails.witness, COLOR.witness, view, w, h);
  drawSeparation(view, w, h);
  drawHeads(view, w, h);
  updateScaleBar(view);
}

function drawGrid(w, h, view) {
  const step = gridStep(view.scale);
  const halfW = w / 2 / view.scale, halfH = h / 2 / view.scale;
  const e0 = Math.floor((view.cx - halfW) / step) * step;
  const n0 = Math.floor((view.cy - halfH) / step) * step;

  ctx.lineWidth = 1;
  for (let e = e0; e < view.cx + halfW + step; e += step) {
    const [x] = project(e, 0, view, w, h);
    ctx.strokeStyle = Math.abs(e) < 1e-6 ? COLOR.gridMajor : COLOR.grid;
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, 0);
    ctx.lineTo(Math.round(x) + 0.5, h);
    ctx.stroke();
  }
  for (let n = n0; n < view.cy + halfH + step; n += step) {
    const [, y] = project(0, n, view, w, h);
    ctx.strokeStyle = Math.abs(n) < 1e-6 ? COLOR.gridMajor : COLOR.grid;
    ctx.beginPath();
    ctx.moveTo(0, Math.round(y) + 0.5);
    ctx.lineTo(w, Math.round(y) + 0.5);
    ctx.stroke();
  }
}

function drawPath(points, color, view, w, h) {
  if (points.length < 2) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  points.forEach(([e, n], i) => {
    const [x, y] = project(e, n, view, w, h);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

/* The line between where GPS claims the vehicle is and where its own senses
 * put it. This gap is the entire product, so it is drawn explicitly rather
 * than left for the viewer to estimate between two curves. */
function drawSeparation(view, w, h) {
  if (!latest || !latest.gnss || latest.witness.e === null) return;
  const gap = latest.residual ? latest.residual.horizontal_m : 0;
  if (gap < 8) return;

  const [x1, y1] = project(latest.gnss.e, latest.gnss.n, view, w, h);
  const [x2, y2] = project(latest.witness.e, latest.witness.n, view, w, h);

  ctx.strokeStyle = COLOR.link;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 4]);
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = COLOR.link;
  ctx.font = "600 13px ui-monospace, Consolas, monospace";
  ctx.textAlign = "center";
  ctx.fillText(`${gap.toFixed(0)} m apart`, (x1 + x2) / 2, (y1 + y2) / 2 - 9);
}

function drawHeads(view, w, h) {
  if (!latest) return;
  if (latest.gnss) head(latest.gnss.e, latest.gnss.n, COLOR.claimed, "GPS says", view, w, h);
  if (latest.witness.e !== null) {
    head(latest.witness.e, latest.witness.n, COLOR.witness, "actually here", view, w, h);
  }
}

function head(e, n, color, label, view, w, h) {
  const [x, y] = project(e, n, view, w, h);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, 6, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = CANVAS_BG;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.fillStyle = color;
  ctx.font = "600 12px system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(label, x + 11, y + 4);
}

function updateScaleBar(view) {
  const metres = gridStep(view.scale);
  el("scaletext").textContent = metres >= 1000
    ? `${(metres / 1000).toFixed(metres % 1000 ? 1 : 0)} km`
    : `${metres} m`;
  document.querySelector(".scalebar").style.width = `${metres * view.scale}px`;
}

/* --- panels ------------------------------------------------------------- */

const SENSOR_LABEL = { gnss: "GPS", imu: "Motion", baro: "Height", mag: "Compass", odom: "Wheels" };
const FLAG_LABEL = {
  stuck: "frozen",
  out_of_range: "impossible reading",
  dropped: "silent",
  noisy: "degraded",
  no_fix: "no fix",
};

function renderPanels(snapshot) {
  const s = snapshot.state;

  el("violation").hidden = !snapshot.violation;
  if (snapshot.violation) el("violation").textContent = `SCHEMA VIOLATION — ${snapshot.violation}`;

  if (!s) {
    el("statepill").textContent = "IDLE";
    el("statepill").dataset.state = "IDLE";
    el("runid").textContent = "waiting for a run";
    el("vehicle").textContent = "no vehicle";
    return;
  }

  el("vehicle").textContent = `${s.vehicle_id} (${s.vehicle_type})`;
  el("runid").textContent = s.run_id;
  el("clock").textContent = `t ${s.t.toFixed(1)} s`;

  const state = s.anchored ? s.state : "IDLE";
  el("statepill").textContent = s.anchored ? state : "ANCHORING";
  el("statepill").dataset.state = state;

  const r = s.residual;
  el("sep").textContent = r ? r.horizontal_m.toFixed(1) : "—";
  el("sep").dataset.state = state;
  el("sigma").textContent = r ? `± ${r.sigma_m.toFixed(1)} m` : "—";
  el("ratio").textContent = r ? `${r.ratio.toFixed(2)} ×` : "—";
  el("vert").textContent = r ? `${r.vertical_m >= 0 ? "+" : ""}${r.vertical_m.toFixed(1)} m` : "—";

  el("speed").textContent = `${s.witness.speed_mps.toFixed(1)} m/s`;
  el("dist").textContent = `${s.witness.distance_m.toFixed(0)} m`;
  el("elapsed").textContent = `${s.witness.elapsed_s.toFixed(0)} s`;
  el("frames").textContent = s.frames.dropped
    ? `${s.frames.seen} · ${s.frames.dropped} lost`
    : `${s.frames.seen}`;

  const box = el("sensors");
  box.innerHTML = "";
  for (const [name, h] of Object.entries(s.health)) {
    const row = document.createElement("div");
    row.className = "sensor" + (h.healthy ? "" : " bad");
    const why = h.flags.map((f) => FLAG_LABEL[f] || f).join(", ");
    row.innerHTML =
      `<i class="dot"></i><span class="who">${SENSOR_LABEL[name] || name}</span>` +
      `<span class="why">${why}</span>`;
    box.appendChild(row);
  }

  // --- what the vehicle is actually steering by ------------------------
  const nav = s.navigation;
  el("navpanel").hidden = !nav;
  if (nav) {
    const dr = nav.source === "dead_reckoning";
    el("navpanel").dataset.source = nav.source;
    el("navsource").textContent = dr
      ? "The vehicle's own sensors"
      : "GPS + own sensors";
    el("navbudget").textContent = `± ${nav.error_budget_m.toFixed(0)} m`;
    el("navremaining").textContent = dr
      ? (nav.seconds_remaining > 0 ? `${nav.seconds_remaining} s` : "past the limit")
      : "not counting down";
    el("navnote").textContent = nav.note || "";
  }

  // --- verdict: which sensor is lying, and why -------------------------
  const blame = s.blame || {};
  const cause = s.cause || {};
  const named = blame.guilty && blame.guilty !== "cannot_isolate";
  const showing = Boolean(blame.guilty);

  el("verdictpanel").hidden = !showing;
  if (showing) {
    el("guilty").textContent = named
      ? (SENSOR_LABEL[blame.guilty] || blame.guilty)
      : "Cannot isolate";
    el("cause").textContent = cause.label || "—";
    el("cause").dataset.cause = cause.label || "";
    el("reason").textContent = cause.reason || "";
    el("action").textContent = cause.action || "";

    const list = el("evidence");
    list.innerHTML = "";
    for (const line of (blame.evidence || [])) {
      const li = document.createElement("li");
      li.textContent = line;
      list.appendChild(li);
    }
  }

  if (snapshot.raw) {
    el("feed").textContent = JSON.stringify(snapshot.raw, null, 1);
  }
}

/* --- stream ------------------------------------------------------------- */

function connect() {
  const source = new EventSource("/stream");

  source.onmessage = (event) => {
    const snapshot = JSON.parse(event.data);
    latest = snapshot.state;
    trails = snapshot.trails;
    renderPanels(snapshot);
    draw();
  };

  source.onerror = () => {
    el("statepill").textContent = "NO LINK";
    el("statepill").dataset.state = "IDLE";
    // EventSource reconnects on its own; restarting the server mid-demo
    // should not need a page reload.
  };
}

/* --- controls ----------------------------------------------------------- */

async function post(path, body) {
  const hint = el("hint");
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      hint.className = "hint bad";
      hint.textContent = data.hint || data.error || `failed (${response.status})`;
      return false;
    }
    return true;
  } catch (err) {
    hint.className = "hint bad";
    hint.textContent = String(err);
    return false;
  }
}

/* --- scenario buttons ---------------------------------------------------
 * Built from whatever the simulator says it has, rather than hardcoded here.
 * A judge picking the scenario off a live list is part of the argument that
 * nothing is staged: the list comes from the other process.
 */

const NOTES = {
  drone_clean:     "no attack",
  drone_manoeuvre: "hard flying, no attack",
  drone_walkoff:   "GPS spoofing",
  drone_magnet:    "magnet on compass",
  drone_fault:     "sensor failure",
  truck_theft:     "cargo theft",
};

let running = null;

async function loadScenarios() {
  const box = el("scenarios");
  try {
    const res = await fetch("/control/scenarios");
    const data = await res.json();
    box.innerHTML = "";
    for (const name of data.scenarios || []) {
      const b = document.createElement("button");
      b.textContent = name.replace(/^drone_|^truck_/, "").replace(/_/g, " ");
      b.dataset.note = NOTES[name] || "";
      b.addEventListener("click", () => startScenario(name, b));
      box.appendChild(b);
    }
    if (!box.children.length) box.innerHTML = '<span class="hint">none offered</span>';
  } catch (err) {
    box.innerHTML = '<span class="hint bad">simulator control not running — ' +
      'start it with: python -m simulator.control</span>';
  }
}

async function startScenario(name, button) {
  // Stop whatever is running first, so switching mid-flight cannot leave two
  // vehicles talking over each other on the same port.
  await post("/control/reset");
  await post("/control/clear");
  latest = null;
  trails = { gnss: [], witness: [] };
  draw();

  const ok = await post("/control/start", { scenario: name });
  running = ok ? name : null;
  for (const b of el("scenarios").children) {
    if (b.classList) b.classList.toggle("running", b === button && ok);
  }
  el("hint").className = "hint";
  el("hint").textContent = ok ? `running ${name}` : "";
}

el("reset").addEventListener("click", async () => {
  await post("/control/reset");
  await post("/control/clear");
  running = null;
  for (const b of el("scenarios").children) {
    if (b.classList) b.classList.remove("running");
  }
  latest = null;
  trails = { gnss: [], witness: [] };
  draw();
  el("hint").textContent = "stopped";
});

resize();
connect();
loadScenarios();
