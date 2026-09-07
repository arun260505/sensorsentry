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
  road: "#ffffff",        /* carriageway */
  roadCase: "#ddd6c6",    /* its casing, so trails stay readable over it */
  roadText: "#8a8272",
  building: "#ded6c6",
};
const CANVAS_BG = "#f4f1ea";

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
  // The route frames the view, not the whole extract. Fitting eight hundred
  // roads zooms out to five kilometres of countryside and the vehicle becomes
  // a speck; the roads are background, and background does not get a vote.
  for (const [lat, lon] of basemap.route || []) pts.push(toLocal(lat, lon));
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
/* Real surveyed geometry, drawn the way a map is drawn.
 *
 * Eight hundred roads with a casing under each one, widths by class, and
 * labels only where there is room. The previous version drew three
 * hand-placed polylines, which is a diagram — and it looked like a diagram,
 * which made the whole thing look like a drawing of a demo rather than a
 * demo.
 *
 * Everything comes from simulator/chennai_map.json, baked once from
 * OpenStreetMap and read off disk. The demo never goes online: a tile server
 * fails silently, and a grey rectangle in front of judges is the worst
 * possible failure mode.
 */

/* Width on screen for each class, as [casing, carriageway]. Scaled with zoom
 * so a road looks like a road at any scale rather than a hairline. */
const ROAD_WIDTH = {
  4: [11, 7.5],   // trunk / motorway
  3: [8, 5],      // primary / secondary
  2: [6, 3.6],    // tertiary
  1: [4.4, 2.6],  // residential / unclassified
  0: [3, 1.7],    // service
};

function roadScale(view) {
  // Below a certain zoom the small roads are noise, so they thin out rather
  // than crowding the picture.
  return Math.max(0.55, Math.min(1.5, view.scale * 3.2));
}

function drawRoads(view, w, h) {
  if (!basemap.roads.length) return;
  const k = roadScale(view);
  const margin = 60;

  // Two passes over the classes, casing first then carriageway, so junctions
  // join cleanly instead of every road drawing its own outline on top of its
  // neighbour.
  const byRank = [0, 1, 2, 3, 4];
  for (const layer of [0, 1]) {
    for (const rank of byRank) {
      ctx.strokeStyle = layer === 0 ? COLOR.roadCase : COLOR.road;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.lineWidth = (ROAD_WIDTH[rank] || ROAD_WIDTH[1])[layer] * k;
      ctx.beginPath();
      for (const road of basemap.roads) {
        if ((road.rank || 1) !== rank) continue;
        if (rank === 0 && k < 0.85) continue;      // hide lanes when zoomed out
        let on = false;
        for (const [lat, lon] of road.points) {
          const [e, n] = toLocal(lat, lon);
          const [x, y] = project(e, n, view, w, h);
          const visible = x > -margin && x < w + margin && y > -margin && y < h + margin;
          if (!visible && !on) continue;
          if (!on) { ctx.moveTo(x, y); on = true; } else { ctx.lineTo(x, y); }
        }
      }
      ctx.stroke();
    }
  }

  drawRoadLabels(view, w, h, k);
  drawPlaces(view, w, h);
}

/* Names, on the bigger roads only, and only one per road. A map that labels
 * every service lane is unreadable at a glance, and a glance is all an
 * operator gets. */
function drawRoadLabels(view, w, h, k) {
  if (k < 0.7) return;
  const placed = [];
  for (const road of basemap.roads) {
    if (!road.name || (road.rank || 1) < 2) continue;
    const pts = road.points.map(([lat, lon]) => toLocal(lat, lon));
    let best = 0, bi = 0;
    for (let i = 1; i < pts.length; i++) {
      const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      if (d > best) { best = d; bi = i; }
    }
    if (!bi) continue;
    const [ax, ay] = project(pts[bi - 1][0], pts[bi - 1][1], view, w, h);
    const [bx, by] = project(pts[bi][0], pts[bi][1], view, w, h);
    if (Math.hypot(bx - ax, by - ay) < 90) continue;

    const mx = (ax + bx) / 2, my = (ay + by) / 2;
    if (mx < 40 || mx > w - 40 || my < 20 || my > h - 20) continue;
    if (placed.some(([px, py]) => Math.hypot(px - mx, py - my) < 110)) continue;
    placed.push([mx, my]);

    let angle = Math.atan2(by - ay, bx - ax);
    if (angle > Math.PI / 2 || angle < -Math.PI / 2) angle += Math.PI;

    const name = road.name.length > 22 ? road.name.slice(0, 20) + "…" : road.name;
    ctx.save();
    ctx.translate(mx, my);
    ctx.rotate(angle);
    ctx.font = "600 10px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    // Halo, so a name stays readable where it crosses a vehicle trail.
    ctx.lineWidth = 3;
    ctx.strokeStyle = CANVAS_BG;
    ctx.strokeText(name, 0, 0);
    ctx.fillStyle = COLOR.roadText;
    ctx.fillText(name, 0, 0);
    ctx.restore();
  }
}

function drawPlaces(view, w, h) {
  for (const place of basemap.places) {
    const [e, n] = toLocal(place.at[0], place.at[1]);
    const [x, y] = project(e, n, view, w, h);
    if (x < 0 || x > w || y < 0 || y > h) continue;
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
    ctx.font = "600 10px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.lineWidth = 3;
    ctx.strokeStyle = CANVAS_BG;
    ctx.strokeText(place.name, x, y + 20);
    ctx.fillStyle = COLOR.roadText;
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
    pushTrace(snapshot);
    renderTraces(snapshot);
    renderProof(snapshot);
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

/* --- the readings themselves ---------------------------------------------
 *
 * Until this existed, only the GPS had anything to look at: two tracks on the
 * map that visibly came apart. Taking over the compass changed a number
 * nobody could see, so the demo appeared to do nothing at all for three of
 * the four sensors.
 *
 * Each chart draws two independent measurements of the same physical
 * quantity. While everything is honest the two lines sit on top of each
 * other. That is the claim of the whole project in one picture — and when a
 * judge drives one of them, the lines come apart in front of them, which is
 * the proof.
 *
 * Nothing here is computed for display. Every value is a reading off the
 * ordinary stream.
 */

const TRACE_SECONDS = 45;

const TRACES = [
  {
    id: "heading",
    title: "Which way we point",
    unit: "°",
    wrap: true,                    // degrees, so 359 -> 1 is a small change
    lines: [
      { key: "compass", name: "compass", colour: "#a35d07" },
      { key: "gyro", name: "gyro", colour: "#0b5c7a" },
    ],
    read: (snap) => {
      const st = snap.state, raw = snap.raw;
      return {
        compass: raw && raw.mag ? raw.mag.heading_deg : null,
        gyro: st ? st.gyro_heading_deg : null,
      };
    },
  },
  {
    id: "height",
    title: "How high we are",
    unit: " m",
    lines: [
      { key: "gps", name: "GPS", colour: "#a35d07" },
      { key: "own", name: "barometer", colour: "#0b5c7a" },
    ],
    // Both sides straight off the state, in the same frame and the same
    // units. Reading the GPS height out of the raw frame instead meant three
    // frames in four had no fix and the line vanished.
    read: (snap) => {
      const st = snap.state;
      return {
        gps: st && st.gnss ? st.gnss.u : null,
        own: st && st.witness ? st.witness.u : null,
      };
    },
  },
  {
    id: "speed",
    title: "How fast we are going",
    unit: " m/s",
    lines: [
      { key: "own", name: "own sensors", colour: "#0b5c7a" },
      { key: "wheels", name: "wheels", colour: "#a35d07" },
    ],
    read: (snap) => {
      const st = snap.state, raw = snap.raw;
      return {
        own: st && st.witness ? st.witness.speed_mps : null,
        wheels: raw && raw.odom ? raw.odom.wheel_speed_mps : null,
      };
    },
  },
];

const traceData = {};      // id -> { t: [], <lineKey>: [] }

function pushTrace(snapshot) {
  const t = snapshot.state ? snapshot.state.t : null;
  if (t == null) return;
  for (const spec of TRACES) {
    const store = traceData[spec.id] || (traceData[spec.id] = { t: [] });
    const values = spec.read(snapshot);
    // Only record when at least one side has something, so a chart never
    // draws a flat line out of missing data and calls it agreement.
    if (Object.values(values).every((v) => v == null)) continue;
    store.t.push(t);
    for (const line of spec.lines) {
      (store[line.key] || (store[line.key] = [])).push(values[line.key]);
    }
    while (store.t.length && t - store.t[0] > TRACE_SECONDS) {
      store.t.shift();
      for (const line of spec.lines) store[line.key].shift();
    }
  }
}

function clearTraces() {
  for (const key of Object.keys(traceData)) delete traceData[key];
}

/* Degrees wrap, so a heading crossing north jumps 360 and the chart shows a
 * cliff that is not there. Unwrap into a continuous line before drawing. */
function unwrap(series) {
  const out = [];
  let offset = 0;
  for (let i = 0; i < series.length; i++) {
    const value = series[i];
    if (value == null) { out.push(null); continue; }
    const previous = out[out.length - 1];
    if (previous != null) {
      const raw = value + offset;
      if (raw - previous > 180) offset -= 360;
      else if (previous - raw > 180) offset += 360;
    }
    out.push(value + offset);
  }
  return out;
}

function renderTraces(snapshot) {
  const panel = el("tracepanel");
  if (!snapshot.state) { panel.hidden = true; return; }
  panel.hidden = false;

  for (const spec of TRACES) {
    const store = traceData[spec.id];
    const card = el(`trace-${spec.id}`);
    if (!store || store.t.length < 2) { if (card) card.hidden = true; continue; }

    // A chart whose second sensor this vehicle does not carry is hidden
    // rather than drawn half-empty: a drone has no wheels, a lorry no
    // barometer, and an empty chart reads as a broken one.
    const usable = spec.lines.filter(
      (line) => store[line.key] && store[line.key].some((v) => v != null));
    if (usable.length < 2) { card.hidden = true; continue; }
    card.hidden = false;

    const series = {};
    for (const line of spec.lines) {
      series[line.key] = spec.wrap ? unwrap(store[line.key]) : store[line.key];
    }
    drawTrace(spec, store, series);

    // Current values, so there is a number to read as well as a shape.
    const readout = el(`trace-${spec.id}-now`);
    readout.innerHTML = "";
    for (const line of usable) {
      const values = store[line.key].filter((v) => v != null);
      if (!values.length) continue;
      const span = document.createElement("span");
      span.className = "tracenow";
      span.style.color = line.colour;
      span.textContent =
        `${line.name} ${values[values.length - 1].toFixed(spec.unit === "°" ? 0 : 1)}${spec.unit}`;
      readout.appendChild(span);
    }
  }
}

function drawTrace(spec, store, series) {
  const canvas = el(`canvas-${spec.id}`);
  const c = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = rect.width || 280, h = 58;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);

  let low = Infinity, high = -Infinity;
  for (const line of spec.lines) {
    for (const value of series[line.key] || []) {
      if (value == null) continue;
      if (value < low) low = value;
      if (value > high) high = value;
    }
  }
  if (!isFinite(low)) return;
  // A minimum span, or two identical honest readings fill the chart with
  // noise and look like violent disagreement.
  const span = Math.max(high - low, spec.unit === "°" ? 8 : 2);
  const mid = (high + low) / 2;
  low = mid - span * 0.62;
  high = mid + span * 0.62;

  const t0 = store.t[0], t1 = store.t[store.t.length - 1] || t0 + 1;
  const x = (t) => 4 + ((t - t0) / Math.max(t1 - t0, 0.001)) * (w - 8);
  const y = (v) => h - 6 - ((v - low) / (high - low)) * (h - 12);

  for (const line of spec.lines) {
    const values = series[line.key];
    if (!values) continue;
    c.strokeStyle = line.colour;
    c.lineWidth = 2;
    c.lineJoin = "round";
    c.beginPath();
    let drawing = false;
    for (let i = 0; i < values.length; i++) {
      if (values[i] == null) { drawing = false; continue; }
      const px = x(store.t[i]), py = y(values[i]);
      if (!drawing) { c.moveTo(px, py); drawing = true; } else { c.lineTo(px, py); }
    }
    c.stroke();
  }
}

/* --- why we say that -----------------------------------------------------
 *
 * The working, not the answer.
 *
 * "The GPS is lying" is an assertion. What makes it believable is that four
 * other sensors agree with each other and only one disagrees with all of
 * them — and that agreement existed only inside the detector, where nobody
 * could see it. This draws it.
 *
 * Every number here comes off the ordinary stream. Nothing is recomputed for
 * display, so what a judge reads is what the detector decided on.
 */

const SENSOR_NAME = {
  gnss: "GPS", imu: "motion", baro: "height", mag: "compass",
  odom: "wheels", road: "road map",
};

/* Where each sensor sits in the web. Fixed positions rather than laid out on
 * the fly: the picture has to look the same every run, or a judge watching
 * twice cannot tell whether the change means anything. */
const WEB_AT = {
  gnss: [0.50, 0.16],
  mag:  [0.86, 0.42],
  odom: [0.72, 0.85],
  baro: [0.72, 0.85],
  imu:  [0.28, 0.85],
  road: [0.14, 0.42],
};

function pairFailing(pair, states) {
  return (states[pair.key] || "OK") !== "OK";
}

/* One check, in words an operator reads rather than a field name. */
function checkLine(pair) {
  const times = pair.sigma > 0 ? pair.ratio : 0;
  return {
    what: pair.label,
    num: `${pair.value}${pair.unit === "deg" ? "°" : " " + pair.unit}`,
    rel: times >= 1 ? `${times.toFixed(1)}x normal` : `${times.toFixed(1)}x`,
  };
}

function renderProof(snapshot) {
  const state = snapshot.state;
  const panel = el("proofpanel");
  if (!state || !state.pairs || !state.pairs.length) {
    panel.hidden = true;
    return;
  }

  const states = state.pair_states || {};
  // A check that has never produced a reading proves nothing either way, so
  // it is left out rather than shown as agreement it has not earned.
  const usable = state.pairs.filter((p) => p.valid || p.stale);
  const failing = usable.filter((p) => pairFailing(p, states));
  const passing = usable.filter((p) => !pairFailing(p, states));

  if (!failing.length && state.state === "OK") {
    // Nothing is wrong. Still show the agreement — a judge should see what
    // "healthy" looks like before they see what a lie looks like.
    el("prooflede").innerHTML =
      `<span class="agreeing">All ${usable.length} cross-checks agree.</span> ` +
      `Every sensor is telling the same story about where this vehicle is ` +
      `and which way it is pointing.`;
  } else {
    const guilty = state.blame && state.blame.guilty;
    const named = guilty && guilty !== "cannot_isolate" ? SENSOR_NAME[guilty] : null;
    const others = [...new Set(passing.flatMap((p) => [p.a, p.b]))]
      .filter((s) => s !== guilty)
      .map((s) => SENSOR_NAME[s] || s);

    if (named && others.length) {
      el("prooflede").innerHTML =
        `The <span class="agreeing">${others.join(", ")}</span> all agree with ` +
        `each other. Only <b>${named}</b> disagrees — with ` +
        `${failing.length === 1 ? "the check it is in" : `all ${failing.length} checks it is in`}.`;
    } else if (failing.length) {
      el("prooflede").innerHTML =
        `${failing.length} check${failing.length > 1 ? "s" : ""} failing, but the ` +
        `sensors involved still back each other up elsewhere — not enough to ` +
        `name one of them yet.`;
    }
  }

  const fill = (id, groupId, list) => {
    const box = el(id);
    box.innerHTML = "";
    for (const pair of list) {
      const line = checkLine(pair);
      const li = document.createElement("li");
      li.innerHTML =
        `<span class="what"></span><span class="num"></span><span class="rel"></span>`;
      li.children[0].textContent = line.what;
      li.children[1].textContent = line.num;
      li.children[2].textContent = line.rel;
      box.appendChild(li);
    }
    el(groupId).hidden = list.length === 0;
  };
  fill("disagree", "disagreegroup", failing);
  fill("agree", "agreegroup", passing);

  panel.hidden = false;
  drawWeb(usable, states, state.blame && state.blame.guilty);
}

/* The web: every sensor that takes part in a check, and a line for each check
 * between them. Green where they agree, red where they do not. A liar shows
 * up as the one node with red running to everything it touches. */
function drawWeb(pairs, states, guilty) {
  const canvas = el("web");
  const ctx2 = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = rect.width || 300, h = 190;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  ctx2.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx2.clearRect(0, 0, w, h);

  const involved = [...new Set(pairs.flatMap((p) => [p.a, p.b]))];
  const at = {};
  for (const sensor of involved) {
    const spot = WEB_AT[sensor] || [0.5, 0.5];
    at[sensor] = [24 + spot[0] * (w - 48), 22 + spot[1] * (h - 48)];
  }

  // Edges first, so nodes sit on top of them.
  for (const pair of pairs) {
    const from = at[pair.a], to = at[pair.b];
    if (!from || !to) continue;
    const bad = (states[pair.key] || "OK") !== "OK";
    ctx2.strokeStyle = bad ? "#b3352a" : "#9dc3ad";
    ctx2.lineWidth = bad ? 2.6 : 1.2;
    if (!bad) ctx2.setLineDash([]);
    ctx2.beginPath();
    ctx2.moveTo(from[0], from[1]);
    ctx2.lineTo(to[0], to[1]);
    ctx2.stroke();
  }

  for (const sensor of involved) {
    const [x, y] = at[sensor];
    const accused = sensor === guilty;
    ctx2.beginPath();
    ctx2.arc(x, y, accused ? 15 : 12, 0, Math.PI * 2);
    ctx2.fillStyle = accused ? "#b3352a" : "#ffffff";
    ctx2.fill();
    ctx2.lineWidth = accused ? 0 : 1.4;
    ctx2.strokeStyle = "#9aa8b2";
    if (!accused) ctx2.stroke();

    ctx2.fillStyle = accused ? "#ffffff" : "#4a5c69";
    ctx2.font = accused
      ? "700 10px ui-sans-serif, system-ui, sans-serif"
      : "600 10px ui-sans-serif, system-ui, sans-serif";
    ctx2.textAlign = "center";
    ctx2.textBaseline = "middle";
    const label = SENSOR_NAME[sensor] || sensor;
    ctx2.fillText(label.length > 7 ? label.slice(0, 7) : label, x, y);
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
    ? "Press Start below, then take a sensor."
    : !fitted(TARGETS[target].sensor)
      ? `This ${type} has no ${target}. Try one of the others.`
      : TARGETS[target].hint;
}

function selectTarget(name) {
  target = name;
  for (const button of document.querySelectorAll(".target")) {
    button.classList.toggle("on", button.dataset.target === name);
  }
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

initAttackPanel();
