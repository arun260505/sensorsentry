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
  road: "#e8e2d6",        /* carriageway */
  roadCase: "#cfc7b6",    /* its casing, so trails stay readable over it */
  roadText: "#8a8272",
  building: "#ded6c6",
};
const CANVAS_BG = "#ffffff";

let latest = null;
let trails = { gnss: [], witness: [] };
let fleet = {};
let zones = [];
let advisories = [];
let focus = null;

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

/* Every vehicle shares one origin-relative frame, so their local metres are
 * directly comparable and the whole fleet fits in one view. */
function computeView(w, h) {
  const pts = trails.gnss.concat(trails.witness);
  for (const v of Object.values(fleet)) {
    if (v.trails) pts.push(...v.trails.gnss, ...v.trails.witness);
  }
  // The roads frame the view as well, so a stationary vehicle is shown in
  // its surroundings rather than filling the screen on its own.
  for (const road of basemap.roads) {
    for (const [lat, lon] of road.points) pts.push(toLocal(lat, lon));
  }
  // Keep the whole zone in view, not just its centre.
  for (const z of zones) {
    if (z.e === undefined) continue;
    pts.push([z.e - z.radius_m, z.n - z.radius_m],
             [z.e + z.radius_m, z.n + z.radius_m]);
  }

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

/* --- drawing ------------------------------------------------------------
 *
 * This is an operations display, not a plot. Someone standing three metres
 * back has to be able to answer three questions without being told: where are
 * my vehicles, is anything wrong, and where is the attacker. Everything drawn
 * here earns its place against one of those.
 */

let basemap = { roads: [], places: [] };
let tick = 0;                       // drives the slow pulse on live elements
let lastDraw = 0;

function draw() {
  const rect = canvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;

  ctx.fillStyle = CANVAS_BG;
  ctx.fillRect(0, 0, w, h);

  const view = computeView(w, h);
  drawGrid(w, h, view);
  drawRoads(view, w, h);
  drawRangeRings(w, h, view);
  drawZones(view, w, h);
  drawFleet(view, w, h);

  // The claimed track sits under the real one: when they overlap, what the
  // vehicle actually did should be the line you see.
  drawTrail(trails.gnss, COLOR.claimed, view, w, h, 3);
  drawTrail(trails.witness, COLOR.witness, view, w, h, 3.5);

  drawSeparation(view, w, h);
  drawHeads(view, w, h);
  drawCompass(w, h);
  updateScaleBar(view);
}

/* A steady repaint keeps the pulse smooth even when frames arrive at 10 Hz. */
function animate(now) {
  if (now - lastDraw > 40) { tick += 1; lastDraw = now; draw(); }
  requestAnimationFrame(animate);
}

function pulse(period) {
  return 0.5 + 0.5 * Math.sin((tick * 0.04 * Math.PI * 2) / period);
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

/* Range rings around the focused vehicle. A grid tells you a metre is a
 * metre; rings tell you how far away something is at a glance, which is the
 * question actually being asked of this screen. */
/* The roads the vehicles are actually driving on.
 *
 * Not decoration. When the fake track runs neatly up the highway while the
 * real truck sits at the warehouse, the whole cargo-theft story is on screen
 * instead of being narrated — and a spoofed position that wanders into a
 * field is visibly in a field.
 */
function drawRoads(view, w, h) {
  for (const road of basemap.roads) {
    const pts = road.points.map(([lat, lon]) => toLocal(lat, lon));
    if (pts.length < 2) continue;

    // Casing under colour, the way roads are drawn on real maps: it keeps
    // them readable where a vehicle trail crosses them.
    for (const [width, colour] of [[road.major ? 13 : 8, COLOR.roadCase],
                                   [road.major ? 9 : 5, COLOR.road]]) {
      ctx.strokeStyle = colour;
      ctx.lineWidth = width;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.beginPath();
      pts.forEach(([e, n], i) => {
        const [x, y] = project(e, n, view, w, h);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    // Name it along its longest straight, the way a road label sits.
    let best = 0, bi = 0;
    for (let i = 1; i < pts.length; i++) {
      const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      if (d > best) { best = d; bi = i; }
    }
    const [ax, ay] = project(pts[bi - 1][0], pts[bi - 1][1], view, w, h);
    const [bx, by] = project(pts[bi][0], pts[bi][1], view, w, h);
    if (Math.hypot(bx - ax, by - ay) > 70) {
      ctx.save();
      ctx.translate((ax + bx) / 2, (ay + by) / 2);
      let angle = Math.atan2(by - ay, bx - ax);
      if (angle > Math.PI / 2 || angle < -Math.PI / 2) angle += Math.PI;
      ctx.rotate(angle);
      ctx.fillStyle = COLOR.roadText;
      ctx.font = "600 10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(road.name.toUpperCase(), 0, -road.major ? -8 : -6);
      ctx.restore();
    }
  }

  for (const place of basemap.places) {
    const [e, n] = toLocal(place.at[0], place.at[1]);
    const [x, y] = project(e, n, view, w, h);
    if (place.kind === "building") {
      ctx.fillStyle = COLOR.building;
      ctx.strokeStyle = COLOR.roadText;
      ctx.lineWidth = 1;
      ctx.fillRect(x - 9, y - 7, 18, 14);
      ctx.strokeRect(x - 9, y - 7, 18, 14);
    } else {
      ctx.fillStyle = COLOR.roadText;
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = COLOR.roadText;
    ctx.font = "600 10px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(place.name, x, y + 20);
    ctx.textAlign = "left";
  }
}

function drawRangeRings(w, h, view) {
  const here = trails.witness[trails.witness.length - 1];
  if (!here) return;
  const [cx, cy] = project(here[0], here[1], view, w, h);
  const step = gridStep(view.scale) * 2;
  ctx.strokeStyle = COLOR.grid;
  ctx.lineWidth = 1;
  for (let i = 1; i <= 4; i++) {
    const r = step * i * view.scale;
    if (r < 30 || r > Math.max(w, h)) continue;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
  }
}

/* Trails fade with age, so the eye reads direction of travel without an
 * arrow cluttering every segment. */
function drawTrail(points, color, view, w, h, width) {
  if (!points || points.length < 2) return;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  const n = points.length;
  const tailStart = Math.max(0, n - 400);
  for (let i = Math.max(1, tailStart); i < n; i++) {
    const age = (i - tailStart) / Math.max(1, n - tailStart);
    ctx.globalAlpha = 0.15 + 0.85 * age;
    ctx.strokeStyle = color;
    ctx.lineWidth = width * (0.5 + 0.5 * age);
    const [x1, y1] = project(points[i - 1][0], points[i - 1][1], view, w, h);
    const [x2, y2] = project(points[i][0], points[i][1], view, w, h);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function drawPath(points, color, view, w, h, width) {
  drawTrail(points, color, view, w, h, width || 2.5);
}

function drawFleet(view, w, h) {
  for (const [id, v] of Object.entries(fleet)) {
    if (id === focus || !v.trails) continue;
    const attacked = v.state && v.state.state === "ALERT";
    ctx.save();
    ctx.globalAlpha = 0.5;
    drawTrail(v.trails.witness, attacked ? COLOR.link : COLOR.witness, view, w, h, 2);
    ctx.restore();

    const last = v.trails.witness[v.trails.witness.length - 1];
    if (!last) continue;
    const [x, y] = project(last[0], last[1], view, w, h);
    drawVehicle(x, y, headingOf(v.trails.witness),
                attacked ? COLOR.link : COLOR.witness, attacked, 7);
    label(x + 13, y + 4, id, attacked ? COLOR.link : COLOR.text, attacked);
  }
}

/* Where the attacker probably is.
 *
 * Soft-edged and slowly breathing on purpose: a hard circle reads as a
 * measurement, and this is an estimate from a handful of witnesses. */
function drawZones(view, w, h) {
  for (const z of zones) {
    if (z.e === undefined) continue;
    const [x, y] = project(z.e, z.n, view, w, h);
    const r = Math.max(12, z.radius_m * view.scale);

    const glow = ctx.createRadialGradient(x, y, 0, x, y, r);
    glow.addColorStop(0, "rgba(179,53,42,.22)");
    glow.addColorStop(0.6, "rgba(179,53,42,.10)");
    glow.addColorStop(1, "rgba(179,53,42,0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = COLOR.link;
    ctx.globalAlpha = 0.5 + 0.35 * pulse(2.5);
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 6]);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    // A cross at the estimated centre — the thing you would send someone to.
    ctx.strokeStyle = COLOR.link;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x - 7, y); ctx.lineTo(x + 7, y);
    ctx.moveTo(x, y - 7); ctx.lineTo(x, y + 7);
    ctx.stroke();

    label(x, y - r - 14, `LIKELY TRANSMITTER · ${z.vehicles.length} HIT`,
          COLOR.link, true, "center");
  }
}

function drawSeparation(view, w, h) {
  const g = trails.gnss[trails.gnss.length - 1];
  const wit = trails.witness[trails.witness.length - 1];
  if (!g || !wit) return;
  const gap = latest && latest.residual ? latest.residual.horizontal_m : 0;
  if (gap < 8) return;

  const [x1, y1] = project(g[0], g[1], view, w, h);
  const [x2, y2] = project(wit[0], wit[1], view, w, h);

  ctx.strokeStyle = COLOR.link;
  ctx.globalAlpha = 0.55 + 0.35 * pulse(1.6);
  ctx.lineWidth = 2;
  ctx.setLineDash([6, 5]);
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;

  const text = gap >= 1000 ? `${(gap / 1000).toFixed(1)} km apart`
                           : `${gap.toFixed(0)} m apart`;
  label((x1 + x2) / 2, (y1 + y2) / 2 - 10, text, COLOR.link, true, "center");
}

function headingOf(points) {
  if (!points || points.length < 2) return 0;
  const n = points.length;
  const a = points[Math.max(0, n - 6)], b = points[n - 1];
  return Math.atan2(b[1] - a[1], b[0] - a[0]);
}

/* A triangle pointing where the vehicle is going. A dot says "something is
 * here"; this says "and it is heading that way", which is what the operator
 * is actually judging. */
function drawVehicle(x, y, heading, color, alert, size) {
  const r = size || 9;
  if (alert) {
    ctx.fillStyle = "rgba(179,53,42,.20)";
    ctx.beginPath();
    ctx.arc(x, y, r * (2.2 + 0.8 * pulse(1.2)), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(-heading);          // canvas y is down; headings are maths-style
  ctx.beginPath();
  ctx.moveTo(r * 1.4, 0);
  ctx.lineTo(-r * 0.8, r * 0.8);
  ctx.lineTo(-r * 0.35, 0);
  ctx.lineTo(-r * 0.8, -r * 0.8);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = CANVAS_BG;
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.restore();
}

function drawHeads(view, w, h) {
  const g = trails.gnss[trails.gnss.length - 1];
  const wit = trails.witness[trails.witness.length - 1];
  const alert = latest && latest.state === "ALERT";

  if (g) {
    const [x, y] = project(g[0], g[1], view, w, h);
    drawVehicle(x, y, headingOf(trails.gnss), COLOR.claimed, false, 8);
    label(x + 15, y + 4, "GPS SAYS", COLOR.claimed, true);
  }
  if (wit) {
    const [x, y] = project(wit[0], wit[1], view, w, h);
    drawVehicle(x, y, headingOf(trails.witness), COLOR.witness, alert, 10);
    label(x + 17, y + 4, "ACTUALLY HERE", COLOR.witness, true);
  }
}

/* Labels sit on a chip so they stay readable over a trail or a zone. */
function label(x, y, text, color, strong, align) {
  ctx.font = strong ? "700 11px system-ui, sans-serif"
                    : "500 11px system-ui, sans-serif";
  ctx.textAlign = align || "left";
  const pad = 5;
  const w = ctx.measureText(text).width;
  const left = align === "center" ? x - w / 2 - pad : x - pad;
  ctx.fillStyle = "rgba(255,255,255,.82)";
  ctx.fillRect(left, y - 11, w + pad * 2, 15);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.textAlign = "left";
}

function drawCompass(w, h) {
  const x = w - 44, y = 44, r = 17;
  ctx.strokeStyle = COLOR.gridMajor;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x, y + r - 3);
  ctx.lineTo(x, y - r + 3);
  ctx.stroke();
  ctx.fillStyle = COLOR.text;
  ctx.font = "700 10px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("N", x, y - r - 4);
  ctx.textAlign = "left";
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

  const count = Object.keys(fleet).length;
  el("mode").textContent = count > 1 ? `FLEET · ${count} VEHICLES` : "SINGLE VEHICLE";
  el("mode").dataset.mode = count > 1 ? "fleet" : "single";

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

  // --- the banner: the answer, in words, at the top of the screen ------
  const b = s.blame || {};
  const c = s.cause || {};
  const showBanner = s.state === "ALERT" && Boolean(b.guilty);
  el("banner").hidden = !showBanner;
  if (showBanner) {
    const who = b.guilty === "cannot_isolate"
      ? "A sensor" : (SENSOR_LABEL[b.guilty] || b.guilty);
    const headline = {
      attack: `${who} IS BEING SPOOFED`,
      fault: `${who} HAS FAILED`,
      interference: `${who} IS BEING INTERFERED WITH`,
    }[c.label] || `${who} CANNOT BE TRUSTED`;
    el("banner").dataset.cause = c.label || "";
    el("bannerwhat").textContent = headline;
    el("bannerwho").textContent = zones.length
      ? `${zones[0].vehicles.length} vehicles affected`
      : (s.vehicle_id || "");
    el("bannerdo").textContent = c.action || "";
  }

  // --- the fleet: how many are hit, and where the attacker is ----------
  el("fleetpanel").hidden = zones.length === 0;
  if (zones.length) {
    const z = zones[0];
    el("zoneline").textContent = z.describe;
    el("zonevehicles").textContent = z.vehicles.join(", ");
    el("zoneradius").textContent = `${(z.radius_m / 1000).toFixed(1)} km across`;
    const list = el("advisories");
    list.innerHTML = "";
    for (const a of advisories) {
      const li = document.createElement("li");
      li.innerHTML = `<b>${a.vehicle_id}</b> — ${a.message}`;
      list.appendChild(li);
    }
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

/* One frame for the whole page.
 *
 * Everything arrives as lat/lon, because every vehicle anchors its own local
 * origin and their metres are not comparable — a fleet map built from raw
 * local metres stacks four vehicles on the same spot and puts one three
 * kilometres away next door. So the page picks a single reference and
 * converts everything against it.
 */
const EARTH_R = 6378137;
let reference = null;

function toLocal(lat, lon) {
  if (reference === null) reference = { lat, lon };
  const latRad = (reference.lat * Math.PI) / 180;
  return [
    ((lon - reference.lon) * Math.PI / 180) * EARTH_R * Math.cos(latRad),
    ((lat - reference.lat) * Math.PI / 180) * EARTH_R,
  ];
}

function pathToLocal(points) {
  return (points || []).map(([lat, lon]) => toLocal(lat, lon));
}

/* --- stream ------------------------------------------------------------- */

function connect() {
  const source = new EventSource("/stream");

  source.onmessage = (event) => {
    const snapshot = JSON.parse(event.data);
    latest = snapshot.state;
    focus = snapshot.focus;
    advisories = snapshot.advisories || [];

    // Anchor the page frame on the focused vehicle, once.
    if (reference === null && latest && latest.witness && latest.witness.lat != null) {
      reference = { lat: latest.witness.lat, lon: latest.witness.lon };
    }

    trails = {
      gnss: pathToLocal(snapshot.trails.gnss),
      witness: pathToLocal(snapshot.trails.witness),
    };
    fleet = {};
    for (const [id, v] of Object.entries(snapshot.vehicles || {})) {
      fleet[id] = {
        state: v.state,
        trails: {
          gnss: pathToLocal(v.trails.gnss),
          witness: pathToLocal(v.trails.witness),
        },
      };
    }
    zones = (snapshot.zones || []).map((z) => {
      const [e, n] = toLocal(z.lat, z.lon);
      return { ...z, e, n };
    });

    renderReportControls(Boolean(snapshot.report_enabled));
    renderPanels(snapshot);
    attackWatch(latest);
    setAttackEnabled();
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
      reference = null;
      for (const b of box.children) if (b.classList) b.classList.remove("running");
      fleetBtn.classList.add("running");
      el("hint").className = "hint";
      el("hint").textContent = "running fleet — 4 vehicles";
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
  trails = { gnss: [], witness: [] };
  fleet = {}; zones = []; advisories = []; reference = null;
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
  await post("/control/stopfleet");
  await post("/control/reset");
  await post("/control/clear");
  running = null;
  for (const b of el("scenarios").children) {
    if (b.classList) b.classList.remove("running");
  }
  latest = null;
  trails = { gnss: [], witness: [] };
  fleet = {}; zones = []; advisories = []; reference = null;
  draw();
  el("hint").textContent = "stopped";
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
    el("hint").className = "hint bad";
    el("hint").textContent = String(err);
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

async function loadBasemap() {
  try {
    basemap = await (await fetch("/basemap")).json();
  } catch (err) {
    basemap = { roads: [], places: [] };   // a grid is still usable
  }
  draw();
}

resize();
connect();
loadScenarios();
loadBasemap();
requestAnimationFrame(animate);

/* --- you are the sensor --------------------------------------------------
 *
 * The vehicle drives its real route. The judge takes a sensor over and drives
 * that instead, with the arrow keys, at a moment nobody scripted.
 *
 * The two cases that matter come from the same control:
 *   drive it   -> the reading moves in a way the vehicle never moved -> TAMPERED
 *   let go     -> the reading stops dead while everything else moves -> FAILED
 *
 * THE RULE THIS SECTION MUST NOT BREAK: the detector is never told any of it.
 * The click happens here, the stopwatch runs here, and it is compared against
 * an alert arriving over the ordinary stream. That is the only reason the
 * number on screen is worth anything.
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
    show:  (d) => Math.round(d.heading_deg || 0) + "°",
  },
  altitude: {
    sensor: "baro", label: "You are the barometer",
    hint: "Up and down move its reported height.",
    arrow: (dir) => ({ step_m: (dir === "up" || dir === "right") ? 10 : -10 }),
    stop:  () => ({ height_offset_m: 0 }),
    show:  (d) => (d.height_offset_m > 0 ? "+" : "") + Math.round(d.height_offset_m || 0) + " m",
  },
  wheels: {
    sensor: "odom", label: "You are the wheel sensor",
    hint: "Hold it at zero while the truck drives on.",
    arrow: (dir) => ({ step_mps: (dir === "up" || dir === "right") ? 2 : -2 }),
    stop:  () => ({ speed_mps: 0 }),
    show:  (d) => (Math.round((d.speed_mps || 0) * 10) / 10) + " m/s",
  },
};

/* What the detector currently believes, in the judge's own words. */
const VERDICT = {
  attack:       ["TAMPERED", "someone is inventing this reading"],
  fault:        ["FAILED SENSOR", "the sensor has stopped telling the truth"],
  interference: ["INTERFERENCE", "something physical is affecting it"],
};

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
  el("atkhint").textContent = live
    ? TARGETS[target].hint
    : "Press Start below, then take a sensor.";
}

function selectTarget(name) {
  target = name;
  for (const button of document.querySelectorAll(".target")) {
    button.classList.toggle("on", button.dataset.target === name);
  }
  renderHeld();
  setAttackEnabled();
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

initAttackPanel();
