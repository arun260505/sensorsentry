/* SensorSentry — the evidence display.
 *
 * The screen the room watches. Everything on it is a reading off the ordinary
 * stream; nothing here can start, stop, attack or steer anything. The controls
 * live on /drive, on a different screen, in the driver's hands.
 *
 * Draws its own map on a canvas. No tile server, no map library, nothing
 * fetched from the internet — the demo runs with wifi switched off, in front
 * of the judges, and a map that quietly fails to load would take the whole
 * thing with it.
 */

const canvas = el("map");
const ctx = canvas.getContext("2d");

/* The map's palette comes out of the stylesheet, not out of a table here that
 * somebody has to remember to keep in step. See themeColours() in common.js
 * for why that mattered. */
/* Read once here and again on every theme change — see refreshColours below.
 * `let`, not `const`: a page that switches to dark around a map still painting
 * itself on paper is the exact failure this whole token scheme exists to stop. */
let COLOR = themeColours({
  grid:      "--map-grid",
  gridMajor: "--map-grid-major",
  claimed:   "--claimed",
  witness:   "--witness",
  link:      "--alert",
  text:      "--ink-3",
  road:      "--map-road",
  roadCase:  "--map-road-case",
  roadText:  "--map-road-text",
  building:  "--map-building",
  builtEdge: "--map-built-edge",
  water:     "--map-water",
  waterEdge: "--map-water-edge",
  green:     "--map-green",
  sand:      "--map-sand",
  built:     "--map-built",
  campus:    "--map-campus",
  rail:      "--map-rail",
  chip:      "--map-chip",
  wash:      "--map-alert-wash",
  fade:      "--map-alert-fade",
});
let CANVAS_BG = themeColours({ bg: "--map-bg" }).bg;

let latest = null;
let rawFrame = null;   // the incoming sensor frame, for the instruments
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

/* Fit a set of points into a box. Used by the overview and by the inset, so
 * the two cannot disagree about what "the whole run" looks like. */
function fitPoints(pts, w, h, pad) {
  if (!pts.length) return { cx: 0, cy: 0, scale: 1.2 };

  let minE = Infinity, maxE = -Infinity, minN = Infinity, maxN = -Infinity;
  for (const [e, n] of pts) {
    if (e < minE) minE = e;
    if (e > maxE) maxE = e;
    if (n < minN) minN = n;
    if (n > maxN) maxN = n;
  }

  const spanE = Math.max(maxE - minE, 40);
  const spanN = Math.max(maxN - minN, 40);
  const scale = Math.min((w - pad * 2) / spanE, (h - pad * 2) / spanN);

  return { cx: (minE + maxE) / 2, cy: (minN + maxN) / 2, scale };
}

/* Everything worth keeping in frame when the whole run is the subject. */
function overviewPoints() {
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
  return pts;
}

/* --- the camera ----------------------------------------------------------
 *
 * The view used to be fitted to the whole route, every frame. That is a
 * defensible thing to do and it was killing the demo: the route spans 1331 m,
 * so on a normal window it resolved to 0.29 px per metre. At that scale the
 * 45 m the road check tolerates is thirteen pixels, our headline "34 m from
 * truth against GPS's 91" is a smudge, and the two vehicle markers — 22 and
 * 28 px wide — are individually larger than the gap they exist to show.
 *
 * So the camera follows the vehicle instead, at a scale where a metre is
 * visible, and pulls back only as far as it must to keep the spoofed position
 * on screen beside the real one. The further the attacker drags the reported
 * position, the wider the shot: the picture frames itself.
 *
 * The whole run is still on screen — as the inset, bottom right. That is not
 * a nicety. At 4 px/m the map covers ~300 m and a lorry crosses it in twenty
 * seconds, while the truck walk-off does not settle to a verdict until 64-70
 * s. Without the inset, the moment the two tracks came apart has scrolled
 * hundreds of metres off the edge by the time the console names the sensor,
 * and the evidence is gone exactly when the answer arrives.
 */

const FOLLOW_SCALE = 4.0;    // px per metre when nothing forces us wider
const FOLLOW_MIN = 1.2;      // never pull back past this; point off-screen instead
const FOLLOW_PAD = 90;       // px of room kept around the pair

let cam = null;              // the eased camera; null until the first frame

/* Fleet work is a different question — "where is the attacker" rather than
 * "what is this vehicle doing" — and it needs kilometres in frame. A zone is
 * 400 m across at minimum; at follow zoom its circle would be five times the
 * width of the map. One zoom cannot serve both, so there are two. */
function isOverview() {
  return zones.length > 0
      || Object.keys(fleet).length > 1
      || trails.witness.length === 0;
}

function followTarget(w, h) {
  const wit = trails.witness[trails.witness.length - 1];
  if (!wit) return null;

  let scale = FOLLOW_SCALE;
  const g = trails.gnss[trails.gnss.length - 1];
  if (g) {
    // Centred on where the vehicle actually is — not on the midpoint, which
    // would slide the roads underneath at half the vehicle's speed and read
    // as the map being broken. So the claimed position has to fit within half
    // the frame, which is what widens the shot.
    const gap = Math.hypot(g[0] - wit[0], g[1] - wit[1]);
    if (gap > 1) {
      const room = Math.min(w, h) / 2 - FOLLOW_PAD;
      if (room > 0) scale = Math.min(scale, room / gap);
    }
  }
  return { cx: wit[0], cy: wit[1], scale: Math.max(scale, FOLLOW_MIN) };
}

/* --- driving the camera by hand ------------------------------------------
 *
 * The follow camera is right while the demo is running and no use at all to
 * somebody who wants to see the whole route, or to look at where the two
 * tracks first came apart. So: buttons, the scroll wheel and a drag.
 *
 * Manual is sticky. It would be worse than useless if the view snapped back
 * to the vehicle a quarter of a second after you dragged it — so once you
 * take hold of the map it stays where you put it until you press Follow.
 */
let manual = null;      // {cx, cy, scale} while someone is driving it by hand

const ZOOM_STEP = 1.5;
const ZOOM_MIN = 0.05;   // the whole corridor and then some
const ZOOM_MAX = 40;     // a metre is 40 px: individual GPS samples

function takeManualControl() {
  if (!manual && cam) manual = { cx: cam.cx, cy: cam.cy, scale: cam.scale };
  el("mapmode").hidden = false;
  el("zoomfollow").hidden = false;
}

function releaseManualControl() {
  manual = null;
  el("mapmode").hidden = true;
  el("zoomfollow").hidden = true;
  draw();
}

/* Zoom about a point, so the thing under the cursor stays under the cursor.
 * Zooming about the middle instead makes the feature you were aiming at slide
 * away as you close in on it, which is why it feels broken when it is done
 * the easy way. */
function zoomBy(factor, ax, ay, w, h) {
  takeManualControl();
  const before = manual.scale;
  const after = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, before * factor));
  if (after === before) return;
  if (ax != null) {
    // world point under the cursor, held fixed across the zoom
    const we = manual.cx + (ax - w / 2) / before;
    const wn = manual.cy - (ay - h / 2) / before;
    manual.cx = we - (ax - w / 2) / after;
    manual.cy = wn + (ay - h / 2) / after;
  }
  manual.scale = after;
  draw();
}

function computeView(w, h) {
  if (manual) return manual;

  const target = (isOverview() ? null : followTarget(w, h))
              || fitPoints(overviewPoints(), w, h, 70);

  if (!cam) {
    cam = { cx: target.cx, cy: target.cy, scale: target.scale };
    return cam;
  }

  // Eased, or every change of separation snaps the world sideways. Scale is
  // eased in log space so pulling out by four feels like pushing in by four.
  const k = 0.14;
  cam.cx += (target.cx - cam.cx) * k;
  cam.cy += (target.cy - cam.cy) * k;
  cam.scale = Math.exp(
    Math.log(cam.scale) + (Math.log(target.scale) - Math.log(cam.scale)) * k);
  return cam;
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

let basemap = { roads: [], areas: [], rails: [], places: [] };
let tick = 0;                       // drives the slow pulse on live elements
let lastDraw = 0;

function draw() {
  const rect = canvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;

  ctx.fillStyle = CANVAS_BG;
  ctx.fillRect(0, 0, w, h);

  const view = computeView(w, h);
  prepareRoads();
  prepareAreas();

  // A map has either a graticule or streets, never both. When the baked road
  // network is there it is the ground; the grid and the range rings only come
  // out as the fallback for when it is not, where a bare canvas would leave
  // no sense of distance at all.
  if (basemap.roads.length) {
    drawAreas(view, w, h);
    drawRoads(view, w, h);
  } else {
    drawGrid(w, h, view);
    drawRangeRings(w, h, view);
  }

  // The two HTML overlays, measured off the elements themselves rather than
  // guessed, so moving them in the stylesheet cannot silently start hiding
  // labels underneath them again.
  for (const node of [el("legend"), document.querySelector(".scalebar"),
                      document.querySelector(".mapctl"),
                      document.querySelector(".mapmode")]) {
    if (!node) continue;
    const b = node.getBoundingClientRect();
    if (b.width) reserve(b.left - rect.left - 6, b.top - rect.top - 6,
                         b.width + 12, b.height + 12);
  }

  drawZones(view, w, h);
  drawFleet(view, w, h);

  drawSensorTracks(view, w, h);
  drawHeightGauge(view, w, h);

  // The claimed track sits under the real one: when they overlap, what the
  // vehicle actually did should be the line you see.
  drawTrail(trails.gnss, COLOR.claimed, view, w, h, 4);
  drawTrail(trails.witness, COLOR.witness, view, w, h, 4.5);

  drawSeparation(view, w, h);
  drawHeads(view, w, h);
  drawOffscreen(view, w, h);
  drawCompassRays(view, w, h);
  drawCompass(w, h);
  drawInset(view, w, h);
  flushLabels(w, h);
  updateScaleBar(view);
}

/* --- the whole run, small -------------------------------------------------
 *
 * The follow camera can only show a few hundred metres, and the truck
 * walk-off takes 64-70 s to reach a verdict. Without this, the place where
 * the two tracks separated is long off the edge by the time the console names
 * the sensor. Here it stays visible for the entire run, with a box showing
 * which part of it the big map is currently looking at.
 */
function drawInset(view, w, h) {
  if (isOverview() && !manual) return;
  if (trails.witness.length < 2) return;

  // Bottom left, above the scale bar. It used to sit bottom right, which is
  // where the zoom controls now are; two things in one corner is how a demo
  // ends up with a button you cannot press.
  const iw = Math.min(240, w * 0.3), ih = Math.min(170, h * 0.32);
  const x0 = 18, y0 = h - ih - 52;

  const pts = trails.gnss.concat(trails.witness);
  for (const [lat, lon] of basemap.route || []) pts.push(toLocal(lat, lon));
  const v = fitPoints(pts, iw, ih, 14);
  const at = (e, n) => [x0 + iw / 2 + (e - v.cx) * v.scale,
                        y0 + ih / 2 - (n - v.cy) * v.scale];

  ctx.save();
  ctx.fillStyle = COLOR.chip;
  ctx.strokeStyle = COLOR.roadCase;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.rect(x0, y0, iw, ih);
  ctx.fill();
  ctx.stroke();
  ctx.clip();

  // The route it is supposed to be driving, then what each side says it did.
  ctx.strokeStyle = COLOR.roadCase;
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.beginPath();
  let on = false;
  for (const [lat, lon] of basemap.route || []) {
    const [x, y] = at(...toLocal(lat, lon));
    if (!on) { ctx.moveTo(x, y); on = true; } else { ctx.lineTo(x, y); }
  }
  ctx.stroke();

  for (const [points, colour] of [[trails.gnss, COLOR.claimed],
                                  [trails.witness, COLOR.witness]]) {
    if (points.length < 2) continue;
    ctx.strokeStyle = colour;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    points.forEach(([e, n], i) => {
      const [x, y] = at(e, n);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  // Where the big map is looking.
  const bw = (w / view.scale) * v.scale, bh = (h / view.scale) * v.scale;
  const [bx, by] = at(view.cx, view.cy);
  ctx.strokeStyle = COLOR.text;
  ctx.lineWidth = 1.2;
  ctx.setLineDash([3, 3]);
  ctx.strokeRect(bx - bw / 2, by - bh / 2, bw, bh);
  ctx.setLineDash([]);
  ctx.restore();

  ctx.font = "600 10px system-ui, sans-serif";
  ctx.fillStyle = COLOR.text;
  ctx.font = "600 11px system-ui, sans-serif";
  ctx.fillText("the whole run", x0, y0 - 6);
  reserve(x0 - 6, y0 - 18, iw + 12, ih + 24);
}

/* When the spoof has dragged the reported position further than the camera is
 * willing to pull back for, say which way it went and how far — rather than
 * letting it leave the frame with nothing to mark that it was ever there. */
function drawOffscreen(view, w, h) {
  const g = trails.gnss[trails.gnss.length - 1];
  const wit = trails.witness[trails.witness.length - 1];
  if (!g || !wit || isOverview()) return;

  const [x, y] = project(g[0], g[1], view, w, h);
  const m = 26;
  if (x >= m && x <= w - m && y >= m && y <= h - m) return;

  const [cx, cy] = project(wit[0], wit[1], view, w, h);
  const angle = Math.atan2(y - cy, x - cx);
  const ex = Math.min(Math.max(x, m), w - m);
  const ey = Math.min(Math.max(y, m), h - m);

  ctx.save();
  ctx.translate(ex, ey);
  ctx.rotate(angle);
  ctx.fillStyle = COLOR.claimed;
  ctx.beginPath();
  ctx.moveTo(11, 0);
  ctx.lineTo(-7, 7);
  ctx.lineTo(-7, -7);
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  const gap = latest && latest.residual ? latest.residual.horizontal_m : 0;
  label(ex, ey - 16, `GPS SAYS ${gap.toFixed(0)} m THAT WAY`,
        COLOR.claimed, true, "center", PRIORITY.offscreen);
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

/* Real surveyed geometry, drawn the way a map is drawn.
 *
 * Everything comes from simulator/chennai_map.json, baked once from
 * OpenStreetMap and read off disk. The demo never goes online: a tile server
 * fails silently, and a grey rectangle in front of judges is the worst
 * possible failure mode.
 *
 * Not decoration, either. When the fake track runs neatly up the highway while
 * the real truck sits at the warehouse, the whole cargo-theft story is on
 * screen instead of being narrated — and a spoofed position that wanders into
 * a field is visibly in a field.
 */

/* How wide each class of road actually is on the ground, as
 * [with verges, carriageway] in metres.
 *
 * Drawn to scale rather than at a fixed pixel width, which matters more than
 * it sounds. At follow zoom a fixed 16 px trunk road is 4 m wide while the
 * lorry drawn on it is 5 — a vehicle wider than the national highway it is
 * driving down, which reads as a broken picture. To scale, the truck sits in
 * a lane, and a spoofed position that has left the carriageway is visibly off
 * the road instead of being described as off the road. That is the road check
 * from blame.py, drawn.
 */
const ROAD_METRES = {
  4: [24, 20],    // trunk / motorway
  3: [15, 12],    // primary / secondary
  2: [10, 8],     // tertiary
  1: [7.5, 6],    // residential / unclassified
  0: [5, 4],      // service
};

/* Pulled far enough out, a road drawn to scale is thinner than a hair. These
 * are the floors that keep the network legible in the overview. */
const ROAD_MIN_PX = {
  4: [11, 7.5],
  3: [8, 5],
  2: [6, 3.6],
  1: [4.4, 2.6],
  0: [3, 1.7],
};

function roadWidth(rank, layer, scale) {
  const metres = (ROAD_METRES[rank] || ROAD_METRES[1])[layer];
  const floor = (ROAD_MIN_PX[rank] || ROAD_MIN_PX[1])[layer];
  return Math.max(metres * scale, floor);
}

/* Which classes of road are worth drawing at this zoom.
 *
 * 682 of the 787 baked roads are service lanes and residential streets. Drawn
 * all at once over a 1.3 km view they are not a map, they are a texture — and
 * the two tracks that matter have to compete with it. Real cartography drops
 * features as it zooms out rather than thinning them, so this does too: pull
 * back far enough and only the trunk road the lorry is actually on survives.
 */
function roadRanks(scale) {
  if (scale >= 2.5) return [0, 1, 2, 3, 4];
  if (scale >= 1.0) return [1, 2, 3, 4];
  if (scale >= 0.45) return [2, 3, 4];
  return [3, 4];
}

/* The roads do not move. Projecting all 4662 of their points on every frame
 * cost 233,000 calls a second, each one a Math.cos of a latitude that is the
 * same every time — and at follow zoom all five classes draw, so nearly all
 * of that work was for geometry hundreds of metres outside the frame.
 *
 * Projected once per run instead, with a bounding box so a road outside the
 * view is skipped whole rather than point by point. Keyed on the reference
 * object, which is replaced when a run starts, so a new run re-projects. */
let roadsPreparedFor = null;
let areasPreparedFor = null;

function prepareRoads() {
  if (!reference || !basemap.roads.length) return;
  if (roadsPreparedFor === reference) return;
  for (const road of basemap.roads) {
    const local = road.points.map(([lat, lon]) => toLocal(lat, lon));
    let minE = Infinity, maxE = -Infinity, minN = Infinity, maxN = -Infinity;
    for (const [e, n] of local) {
      if (e < minE) minE = e;
      if (e > maxE) maxE = e;
      if (n < minN) minN = n;
      if (n > maxN) maxN = n;
    }
    road.local = local;
    road.bbox = [minE, minN, maxE, maxN];
  }
  roadsPreparedFor = reference;
}

/* Areas and rails get the same treatment as the roads: projected once per run,
 * with a bounding box, because they are just as static and there are hundreds
 * of them. Re-projecting them every frame would undo the saving that made the
 * paint loop affordable in the first place. */
function prepareAreas() {
  if (!reference) return;
  if (areasPreparedFor === reference) return;
  for (const shape of (basemap.areas || []).concat(basemap.rails || [])) {
    const local = shape.points.map(([lat, lon]) => toLocal(lat, lon));
    let minE = Infinity, maxE = -Infinity, minN = Infinity, maxN = -Infinity;
    for (const [e, n] of local) {
      if (e < minE) minE = e;
      if (e > maxE) maxE = e;
      if (n < minN) minN = n;
      if (n > maxN) maxN = n;
    }
    shape.local = local;
    shape.bbox = [minE, minN, maxE, maxN];
    shape.span = Math.max(maxE - minE, maxN - minN);
  }
  areasPreparedFor = reference;
}

const AREA_FILL = {
  water: "water", green: "green", sand: "sand",
  built: "built", campus: "campus", building: "building",
};

/* Drawn before the roads and long before the trails, so nothing here can
 * paint over the two tracks — which are the only things on this map that
 * anybody actually has to see. */
function drawAreas(view, w, h) {
  if (!basemap.areas || !basemap.areas.length) return;
  if (!basemap.areas[0].local) return;

  const margin = 40;
  const halfW = (w / 2 + margin) / view.scale;
  const halfH = (h / 2 + margin) / view.scale;
  const inView = (b) => b[0] <= view.cx + halfW && b[2] >= view.cx - halfW
                     && b[1] <= view.cy + halfH && b[3] >= view.cy - halfH;

  // A shape narrower than a couple of pixels is noise, not information. At
  // fit-everything zoom that silently drops every building, which is right:
  // eighty three-pixel smudges do not tell you anything and they compete with
  // the thing that does.
  const tooSmall = 3 / view.scale;

  const traceRing = (shape) => {
    ctx.beginPath();
    let first = true;
    for (const [e, n] of shape.local) {
      const [x, y] = project(e, n, view, w, h);
      if (first) { ctx.moveTo(x, y); first = false; } else { ctx.lineTo(x, y); }
    }
    ctx.closePath();
  };

  // Washes first, biggest first (the baker sorted them), then water on top of
  // land, then buildings on top of everything — the order a paper map uses.
  for (const pass of ["land", "water", "building"]) {
    for (const shape of basemap.areas) {
      const kind = AREA_FILL[shape.kind];
      if (!kind) continue;
      if (pass === "water" ? kind !== "water"
        : pass === "building" ? kind !== "building"
        : (kind === "water" || kind === "building")) continue;
      if (shape.span < tooSmall || !inView(shape.bbox)) continue;

      traceRing(shape);
      ctx.fillStyle = COLOR[kind];
      ctx.fill();
      if (kind === "water" || kind === "building") {
        ctx.strokeStyle = kind === "water" ? COLOR.waterEdge : COLOR.builtEdge;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }
  }

  // Names, for the ones big enough on screen to carry one. Measured in pixels
  // rather than in hectares, so the same rule works at every zoom: a lake
  // fills the frame at follow zoom and is a thumbnail at whole-run, and it
  // should be named in the first case and not the second.
  for (const shape of basemap.areas) {
    if (!shape.name || !inView(shape.bbox)) continue;
    if (shape.span * view.scale < 70) continue;
    const cx = (shape.bbox[0] + shape.bbox[2]) / 2;
    const cy = (shape.bbox[1] + shape.bbox[3]) / 2;
    const [x, y] = project(cx, cy, view, w, h);
    if (x < 0 || x > w || y < 0 || y > h) continue;
    const text = shape.name.length > 26
      ? shape.name.slice(0, 24) + "…" : shape.name;
    label(x, y, text, COLOR.roadText, false, "center", PRIORITY.area);
  }

  for (const rail of basemap.rails || []) {
    if (!rail.local || !inView(rail.bbox)) continue;
    ctx.strokeStyle = COLOR.rail;
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 5]);
    ctx.beginPath();
    let on = false;
    for (const [e, n] of rail.local) {
      const [x, y] = project(e, n, view, w, h);
      if (!on) { ctx.moveTo(x, y); on = true; } else { ctx.lineTo(x, y); }
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawRoads(view, w, h) {
  if (!basemap.roads.length || !basemap.roads[0].local) return;
  const shown = roadRanks(view.scale);
  const margin = 60;

  // What the frame covers, in metres, plus a margin so a road entering the
  // view is already being drawn when its first point arrives.
  const halfW = (w / 2 + margin) / view.scale;
  const halfH = (h / 2 + margin) / view.scale;
  const inView = (b) => b[0] <= view.cx + halfW && b[2] >= view.cx - halfW
                     && b[1] <= view.cy + halfH && b[3] >= view.cy - halfH;

  // Two passes over the classes, casing first then carriageway, so junctions
  // join cleanly instead of every road drawing its own outline on top of its
  // neighbour.
  for (const layer of [0, 1]) {
    for (const rank of shown) {
      ctx.strokeStyle = layer === 0 ? COLOR.roadCase : COLOR.road;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.lineWidth = roadWidth(rank, layer, view.scale);
      ctx.beginPath();
      for (const road of basemap.roads) {
        if ((road.rank || 1) !== rank) continue;
        if (!inView(road.bbox)) continue;
        let on = false;
        for (const [e, n] of road.local) {
          const [x, y] = project(e, n, view, w, h);
          if (x < -margin || x > w + margin || y < -margin || y > h + margin) {
            on = false;
            continue;
          }
          if (!on) { ctx.moveTo(x, y); on = true; } else { ctx.lineTo(x, y); }
        }
      }
      ctx.stroke();
    }
  }

  drawRoadLabels(view, w, h, shown);
  drawPlaces(view, w, h);
}

function drawRoadLabels(view, w, h, shown) {
  if (view.scale < 0.3) return;
  const placed = [];
  for (const road of basemap.roads) {
    const rank = road.rank || 1;
    if (!road.name || rank < 2 || !shown.includes(rank)) continue;
    const pts = road.local;
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
    if (placed.some(([px, py]) => Math.hypot(px - mx, py - my) < 130)) continue;
    placed.push([mx, my]);

    let angle = Math.atan2(by - ay, bx - ax);
    if (angle > Math.PI / 2 || angle < -Math.PI / 2) angle += Math.PI;

    const name = road.name.length > 22 ? road.name.slice(0, 20) + "…" : road.name;
    ctx.save();
    ctx.translate(mx, my);
    ctx.rotate(angle);
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    // Halo, so a name stays readable where it crosses a vehicle trail.
    ctx.lineWidth = 3.5;
    ctx.strokeStyle = CANVAS_BG;
    ctx.strokeText(name, 0, 0);
    ctx.fillStyle = COLOR.roadText;
    ctx.fillText(name, 0, 0);
    ctx.restore();
  }
  ctx.textBaseline = "alphabetic";
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
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.lineWidth = 3.5;
    ctx.strokeStyle = CANVAS_BG;
    ctx.strokeText(place.name, x, y + 20);
    ctx.fillStyle = COLOR.roadText;
    ctx.fillText(place.name, x, y + 20);
    ctx.textAlign = "left";
  }
}

/* Range rings around the focused vehicle. A grid tells you a metre is a
 * metre; rings tell you how far away something is at a glance. Only drawn in
 * the no-basemap fallback — with roads on screen they are clutter. */
function drawRangeRings(w, h, view) {
  const here = focus && trails.witness.length
    ? trails.witness[trails.witness.length - 1] : null;
  if (!here) return;
  const [cx, cy] = project(here[0], here[1], view, w, h);
  ctx.strokeStyle = COLOR.grid;
  ctx.lineWidth = 1;
  const step = gridStep(view.scale) * 2;
  for (let i = 1; i <= 3; i++) {
    const r = step * i * view.scale;
    if (r > Math.hypot(w, h)) break;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
  }
}

/* The history is the evidence: where the two tracks came apart is the oldest
 * part of the divergence, so it must not be the faintest thing on screen. The
 * old fade bottomed out at 0.15 and took the moment of the attack with it. */
/* Drawn in a handful of bands rather than a stroke per segment. Per-segment
 * was 1063 canvas strokes per trail 55 seconds into a run and still climbing
 * — 53,000 a second across the two of them, which is the kind of cost that
 * shows up as a stutter on the one laptop that matters. Ten bands look the
 * same and cost ten strokes. Bands share an endpoint so the line stays
 * continuous. */
const TRAIL_BANDS = 10;

function drawTrail(points, color, view, w, h, width) {
  const n = points.length;
  if (n < 2) return;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = color;

  const per = Math.max(1, Math.ceil((n - 1) / TRAIL_BANDS));
  let start = 0;
  while (start < n - 1) {
    const end = Math.min(start + per, n - 1);
    const age = end / (n - 1);
    ctx.globalAlpha = 0.45 + 0.55 * age;
    ctx.lineWidth = width * (0.7 + 0.3 * age);
    ctx.beginPath();
    for (let i = start; i <= end; i++) {
      const [x, y] = project(points[i][0], points[i][1], view, w, h);
      if (i === start) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    start = end;
  }
  ctx.globalAlpha = 1;
}

function drawFleet(view, w, h) {
  for (const [id, v] of Object.entries(fleet)) {
    if (focus && id === focus) continue;     // drawn full strength below
    ctx.save();
    ctx.globalAlpha = 0.5;
    drawTrail(v.trails.witness, COLOR.witness, view, w, h, 2.5);
    drawTrail(v.trails.gnss, COLOR.claimed, view, w, h, 2);
    ctx.restore();

    const head = v.trails.witness[v.trails.witness.length - 1];
    if (!head) continue;
    const [x, y] = project(head[0], head[1], view, w, h);
    drawVehicle(x, y, headingOf(v.trails.witness), COLOR.witness,
                v.state === "ALERT", 7);
    label(x + 12, y + 4, id, COLOR.text, false, "left", PRIORITY.fleet);
  }
}

/* Where the attacker probably is. A circle rather than a point, because that
 * is genuinely what we know — and saying so is more credible than a pin. */
function drawZones(view, w, h) {
  for (const z of zones) {
    if (z.e === undefined) continue;
    const [x, y] = project(z.e, z.n, view, w, h);
    const r = Math.max(z.radius_m * view.scale, 24);

    const glow = ctx.createRadialGradient(x, y, 0, x, y, r);
    glow.addColorStop(0, COLOR.wash);
    glow.addColorStop(1, COLOR.fade);
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

    ctx.strokeStyle = COLOR.link;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x - 7, y); ctx.lineTo(x + 7, y);
    ctx.moveTo(x, y - 7); ctx.lineTo(x, y + 7);
    ctx.stroke();

    label(x, y - r - 8, "LIKELY TRANSMITTER", COLOR.link, true, "center", PRIORITY.zone);
  }
}

/* The gap itself, called out in metres. This is the number the whole project
 * exists to produce, so it is written on the map and not only in a panel. */
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
  label((x1 + x2) / 2, (y1 + y2) / 2 - 10, text, COLOR.link, true, "center",
        PRIORITY.separation);
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
    ctx.fillStyle = COLOR.wash;
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

/* Pixels between the two markers before they are labelled separately. */
const HEADS_APART_PX = 26;

function drawHeads(view, w, h) {
  const g = trails.gnss[trails.gnss.length - 1];
  const wit = trails.witness[trails.witness.length - 1];
  const alert = latest && latest.state === "ALERT";

  const gp = g ? project(g[0], g[1], view, w, h) : null;
  const wp = wit ? project(wit[0], wit[1], view, w, h) : null;

  if (gp) drawVehicle(gp[0], gp[1], headingOf(trails.gnss), COLOR.claimed, false, 8);
  if (wp) drawVehicle(wp[0], wp[1], headingOf(trails.witness), COLOR.witness, alert, 10);

  // While the two agree they are the same vehicle, so they get one name.
  //
  // Labelling them "GPS SAYS" and "ACTUALLY HERE" the whole time meant that
  // on an honest run — every cross-check agreeing — the map still announced a
  // disagreement, with both captions stacked on the same pixel. Someone who
  // had just selected the compass read it as the display ignoring them and
  // talking about the GPS instead. The two names are worth having only at the
  // moment they stop describing the same place.
  const together = gp && wp
    && Math.hypot(gp[0] - wp[0], gp[1] - wp[1]) < HEADS_APART_PX;

  if (together) {
    const name = latest && latest.vehicle_id ? latest.vehicle_id : "vehicle";
    label(wp[0] + 17, wp[1] + 4, name, COLOR.witness, true, "left", PRIORITY.witness);
    return;
  }
  if (gp) label(gp[0] + 15, gp[1] + 4, "GPS SAYS", COLOR.claimed, true,
                "left", PRIORITY.gnss);
  if (wp) label(wp[0] + 17, wp[1] + 4, "ACTUALLY HERE", COLOR.witness, true,
                "left", PRIORITY.witness);
}

/* --- labels --------------------------------------------------------------
 *
 * Ten places on this map wanted to write on it, and every one of them drew
 * straight to the canvas at whatever coordinate suited it. With four vehicles
 * in a cluster that produced exactly what you would expect: LIKELY
 * TRANSMITTER across a drone, a vehicle name across the separation figure,
 * and a compass note printed over the top of both with its beginning lost
 * behind another chip.
 *
 * So nothing draws a label any more; it asks for one. They are all placed
 * together at the end of the frame, most important first, and anything that
 * cannot find clear space is nudged — and if it still cannot fit, dropped.
 *
 * Dropped, deliberately. A map that hides its fourth-most-important note is
 * readable; one that prints all ten on top of each other is not, and it is
 * the important ones that get buried, because they are drawn first.
 */

const LABEL_PAD = 5;
const LABEL_H = 17;

/* Higher wins the space. The two positions and the gap between them are the
 * claim of the whole project; a fleet member's name is not. */
const PRIORITY = {
  separation: 100,
  witness:     95,
  gnss:        90,
  offscreen:   85,
  zone:        80,
  gauge:       55,
  fleet:       50,
  track:       45,
  compass:     40,
  // Bottom of the pile on purpose. A place name is context; it must never be
  // the thing that pushes the separation figure off the map.
  area:        20,
};

let labelQueue = [];

/* Areas already spoken for. The legend and the scale bar are HTML sitting on
 * top of the canvas, so the canvas cannot see them and was happily writing
 * underneath both; the dials and the inset it draws itself. Reserved before
 * anything is placed, so a label goes somewhere it can actually be read. */
let labelReserved = [];

function reserve(x, y, w, h) {
  labelReserved.push({ x, y, w, h });
}

function label(x, y, text, color, strong, align, priority) {
  labelQueue.push({ x, y, text, color, strong, align, priority: priority || 50 });
}

/* Candidate offsets, in the order they are tried: where it asked to go, then
 * directly under, over, and progressively further away. */
const LABEL_TRIES = [
  [0, 0], [0, 17], [0, -17], [0, 34], [0, -34],
  [14, 25], [-14, 25], [14, -25], [-14, -25], [0, 51], [0, -51],
];

function flushLabels(w, h) {
  const placed = labelReserved.slice();
  const clear = (r) => !placed.some((p) =>
    r.x < p.x + p.w && r.x + r.w > p.x && r.y < p.y + p.h && r.y + r.h > p.y);

  for (const item of labelQueue.sort((a, b) => b.priority - a.priority)) {
    ctx.font = item.strong ? "700 12px system-ui, sans-serif"
                           : "500 12px system-ui, sans-serif";
    const width = ctx.measureText(item.text).width + LABEL_PAD * 2;

    let box = null;
    for (const [dx, dy] of LABEL_TRIES) {
      const cx = item.x + dx, cy = item.y + dy;
      const left = item.align === "center" ? cx - width / 2 : cx - LABEL_PAD;
      const candidate = { x: left, y: cy - 12, w: width, h: LABEL_H, cx, cy };
      // Off the edge of the canvas is no better than on top of something.
      if (candidate.x < 2 || candidate.x + candidate.w > w - 2) continue;
      if (candidate.y < 2 || candidate.y + candidate.h > h - 2) continue;
      if (clear(candidate)) { box = candidate; break; }
    }
    if (!box) continue;
    placed.push(box);

    ctx.textAlign = item.align || "left";
    ctx.fillStyle = COLOR.chip;
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.fillStyle = item.color;
    ctx.fillText(item.text, box.cx, box.cy);
    ctx.textAlign = "left";
  }
  labelQueue = [];
  labelReserved = [];
}



/* --- a line for every sensor ---------------------------------------------
 *
 * The map drew two tracks — where the GPS says we are, and where the vehicle
 * worked out it is — while the legend named four sensors. So three of them
 * had a colour and no line, and driving any of them changed nothing on the
 * map. That is the complaint, and it was a fair one.
 *
 * Each sensor now gets the track it would have produced **if you believed
 * only it**. Take the compass over and turn it forty degrees and its line
 * peels off across country, because a vehicle navigating on that compass
 * really would go that way. Freeze the wheels and their line stops dead while
 * the others carry on.
 *
 * Everything here is integrated from the ordinary stream — the same readings
 * the detector gets, and nothing else. These lines are drawn for the operator
 * and are never fed back into any check.
 *
 * Height is deliberately absent. It is a level, not a direction, and there is
 * no honest way to draw it on a plan view; it has the HEIGHT dial and its own
 * trace instead, and the legend row says so.
 */

const TRACK_MAX = 900;          // points kept per line, about 45 s at 20 Hz

let sensorTracks = { mag: [], odom: [] };
let trackLastT = null;

function clearSensorTracks() {
  sensorTracks = { mag: [], odom: [] };
  trackLastT = null;
}

function advance(from, headingDeg, metres) {
  const rad = headingDeg * Math.PI / 180;      // compass bearing: 0 = north
  return [from[0] + Math.sin(rad) * metres, from[1] + Math.cos(rad) * metres];
}

function pushSensorTracks(snapshot) {
  const st = snapshot.state, raw = snapshot.raw;
  if (!st || !st.witness || st.witness.lat == null) return;

  const t = st.t;
  if (trackLastT === null) {
    // Both start where the vehicle really is, so any daylight between them
    // afterwards is the sensor's own doing rather than a different origin.
    const seed = toLocal(st.witness.lat, st.witness.lon);
    sensorTracks.mag = [seed];
    sensorTracks.odom = [seed];
    trackLastT = t;
    return;
  }
  const dt = t - trackLastT;
  trackLastT = t;
  if (dt <= 0 || dt > 1.0) return;             // paused, or a new run

  const ownSpeed = st.witness.speed_mps || 0;
  const ownHeading = st.gyro_heading_deg;
  const compass = raw && raw.mag ? raw.mag.heading_deg : null;
  const wheels = raw && raw.odom ? raw.odom.wheel_speed_mps : null;

  // Believe the compass about direction, everything else about speed.
  if (compass != null) {
    const track = sensorTracks.mag;
    track.push(advance(track[track.length - 1], compass, ownSpeed * dt));
    if (track.length > TRACK_MAX) track.shift();
  }
  // Believe the wheels about distance, everything else about direction.
  if (fittedHere("odom") && wheels != null && ownHeading != null) {
    const track = sensorTracks.odom;
    track.push(advance(track[track.length - 1], ownHeading, wheels * dt));
    if (track.length > TRACK_MAX) track.shift();
  }
}

/* Height, drawn beside the vehicle as a gauge.
 *
 * A plan view has nowhere to put an altitude, so height was the one sensor
 * with a legend row and nothing on the map. A short vertical scale at the
 * vehicle fixes that honestly: two ticks, one where the GPS says it is and
 * one where the barometer says, joined by a line whose length *is* the
 * disagreement. Squeeze the barometer and the two ticks pull apart in front
 * of you.
 *
 * Only on a vehicle that carries one, which is the drone.
 */
function drawHeightGauge(view, w, h) {
  if (!fittedHere("baro")) return;
  const st = latest;
  const wit = trails.witness[trails.witness.length - 1];
  if (!st || !wit || !st.gnss || !st.witness) return;
  const gps = st.gnss.u, own = st.witness.u;
  if (gps == null || own == null) return;

  const [vx, vy] = project(wit[0], wit[1], view, w, h);
  const x = vx - 34;                       // clear of the vehicle marker
  const mid = vy;
  const half = 34;

  // Scale so the two always sit inside the gauge, with a floor so a pair of
  // honest readings a metre apart do not fill it and look like a crisis.
  const span = Math.max(20, Math.abs(gps - own) * 1.7);
  const at = (v) => mid - ((v - (gps + own) / 2) / span) * half;

  ctx.strokeStyle = COLOR.gridMajor;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, mid - half);
  ctx.lineTo(x, mid + half);
  ctx.stroke();

  const tick = (v, colour, width) => {
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(x - 7, at(v));
    ctx.lineTo(x + 7, at(v));
    ctx.stroke();
  };

  const gap = Math.abs(gps - own);
  if (gap > 8) {
    // The line between them is the disagreement itself.
    ctx.strokeStyle = SENSOR_COLOUR.baro;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x, at(gps));
    ctx.lineTo(x, at(own));
    ctx.stroke();
    label(x - 12, at((gps + own) / 2) + 4,
          `height ${gap.toFixed(0)} m apart`, SENSOR_COLOUR.baro, true, "right",
          PRIORITY.gauge);
  }
  tick(own, COLOR.witness, 2);
  tick(gps, COLOR.claimed, 2.6);
}

/* Drawn under the two position tracks, thinner, so the map still reads as a
 * map when everything agrees and the four lines lie on top of one another. */
function drawSensorTracks(view, w, h) {
  const lines = [
    ["mag", SENSOR_COLOUR.mag, "compass"],
    ["odom", SENSOR_COLOUR.odom, "wheels"],
  ];
  const wit = trails.witness[trails.witness.length - 1];

  for (const [key, colour, name] of lines) {
    const track = sensorTracks[key];
    if (!track || track.length < 2) continue;

    ctx.strokeStyle = colour;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    track.forEach(([e, n], i) => {
      const [x, y] = project(e, n, view, w, h);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Name the end of the line, but only once it has separated enough to be
    // a line of its own rather than ink on top of the others.
    const end = track[track.length - 1];
    if (!wit) continue;
    if (Math.hypot(end[0] - wit[0], end[1] - wit[1]) < 25) continue;
    const [x, y] = project(end[0], end[1], view, w, h);
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = colour;
    ctx.fill();
    label(x + 9, y + 4, `if you believed the ${name}`, colour, true,
          "left", PRIORITY.track);
  }
}

/* --- the legend, one line per sensor -------------------------------------
 *
 * It used to say two things, both about position: "GPS says" and "own sensors
 * say". So whichever sensor you were driving, the map talked about the GPS —
 * take over the compass, turn it forty degrees, and the only caption on the
 * map still concerned a position that had not moved.
 *
 * Every sensor now has its own colour and its own line, showing what it is
 * currently disagreeing by, in its own units. Nothing wrong reads as five
 * quiet rows; one sensor lying reads as one row in red with a number in it.
 */

let SENSOR_COLOUR = themeColours({
  mag:  "--sensor-mag",
  odom: "--sensor-odom",
  baro: "--sensor-baro",
});

function angleGap(a, b) {
  return Math.abs(((a - b + 540) % 360) - 180);
}

/* What each sensor is out by, right now, in the unit that sensor measures in.
 * Read off the ordinary stream — nothing is recomputed for display. */
function sensorDeviations() {
  const st = latest;
  if (!st) return {};
  const out = {};

  // GPS: metres between where it claims to be and where the vehicle worked
  // out it is.
  //
  // Note the wording: "from our estimate", not "off". Touch only the compass
  // and the GPS reading itself stays perfectly honest — but our own GPS-free
  // estimate leans on the compass to know which way it is going, so the two
  // positions separate anyway. The gap is real and worth showing; calling it
  // the GPS being wrong would be a lie, and it is the exact confusion this
  // legend exists to end.
  if (st.gnss && st.witness && st.gnss.e != null && st.witness.e != null) {
    out.gnss = {
      off: Math.hypot(st.gnss.e - st.witness.e, st.gnss.n - st.witness.n),
      text: (v) => `${v.toFixed(0)} m from our estimate`,
      quiet: 12,
    };
  }
  // Compass: degrees between it and the gyro's own integrated heading.
  const compass = rawFrame && rawFrame.mag ? rawFrame.mag.heading_deg : null;
  if (compass != null && st.gyro_heading_deg != null) {
    out.mag = {
      off: angleGap(compass, st.gyro_heading_deg),
      text: (v) => `${v.toFixed(0)}° off`,
      quiet: 10,
    };
  }
  // Wheels: metres per second against our own estimate of speed.
  const wheels = rawFrame && rawFrame.odom ? rawFrame.odom.wheel_speed_mps : null;
  if (fittedHere("odom") && wheels != null && st.witness
      && st.witness.speed_mps != null) {
    out.odom = {
      off: Math.abs(wheels - st.witness.speed_mps),
      text: (v) => `${v.toFixed(1)} m/s off`,
      quiet: 4,
    };
  }
  // Height: metres between the GPS altitude and the barometric one. Only on
  // a vehicle that has a barometer — a lorry does not, and the row must not
  // appear for it however available the numbers are.
  if (fittedHere("baro")
      && st.gnss && st.witness && st.gnss.u != null && st.witness.u != null) {
    out.baro = {
      off: Math.abs(st.gnss.u - st.witness.u),
      text: (v) => `${v.toFixed(0)} m off`,
      quiet: 10,
    };
  }
  return out;
}

function renderLegend() {
  const dev = sensorDeviations();
  for (const row of document.querySelectorAll(".legend .lg")) {
    const key = row.dataset.key;
    if (key === "witness") continue;          // the reference; nothing to report
    const value = el(`lg-${key}`);
    const reading = dev[key];
    // A sensor this vehicle does not carry is hidden rather than shown as a
    // dash — a lorry has no barometer, and an empty row reads as a fault.
    row.hidden = !reading;
    if (!reading) continue;
    value.textContent = reading.text(reading.off);

    // Red is reserved for the sensor the detector has actually named. Every
    // number here is a fact; the accusation is not ours to make from a gap.
    //
    // This is the difference the question "if I only touch the compass, the
    // GPS should be perfect" is really asking about. It is: the GPS reading
    // is untouched. The positions still separate, because our own estimate
    // is built partly on the compass — and the system still says compass,
    // not GPS, which is the whole reason blame exists.
    const named = latest && latest.blame ? latest.blame.guilty : null;
    const accused = key === named;
    row.classList.toggle("off", accused);
    row.classList.toggle("agree", !accused && reading.off <= reading.quiet);
  }
}

/* --- the compass, drawn on the map ---------------------------------------
 *
 * The GPS had two positions to pull apart and the compass had nothing, so
 * turning it was invisible on the map however far it went. Two rays from the
 * vehicle, one for where the compass says we point and one for where the gyro
 * says, with the angle between them written on it once they part.
 */
function drawCompassRays(view, w, h) {
  const wit = trails.witness[trails.witness.length - 1];
  const compass = rawFrame && rawFrame.mag ? rawFrame.mag.heading_deg : null;
  const gyro = latest ? latest.gyro_heading_deg : null;
  if (!wit || compass == null || gyro == null) return;

  const gap = angleGap(compass, gyro);
  if (gap < 6) return;         // agreeing: one arrow already says everything

  const [x, y] = project(wit[0], wit[1], view, w, h);
  const ray = (deg, colour, width, len) => {
    const rad = (deg - 90) * Math.PI / 180;
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + Math.cos(rad) * len, y + Math.sin(rad) * len);
    ctx.stroke();
  };
  ray(gyro, COLOR.witness, 2, 52);
  ray(compass, SENSOR_COLOUR.mag, 3, 62);

  const mid = (compass - 90) * Math.PI / 180;
  label(x + Math.cos(mid) * 70, y + Math.sin(mid) * 70,
        `compass ${Math.round(gap)}° off`, SENSOR_COLOUR.mag, true,
        "left", PRIORITY.compass);
}

/* --- instruments ---------------------------------------------------------
 *
 * Three dials in the corner, each showing two independent measurements of one
 * physical quantity: what the sensor reports, and what the rest of the
 * vehicle works out without it.
 *
 * This was a static north arrow, which is why driving anything other than the
 * GPS looked like it did nothing. The GPS had the two tracks on the map to
 * pull apart; the compass, the barometer and the wheels had no picture at
 * all, so a judge turning the compass forty degrees watched a number change
 * somewhere and nothing else move.
 *
 * Only the instruments this vehicle actually carries are drawn. A lorry has
 * no barometer and a drone has no wheels, and an empty dial reads as broken.
 */

const DIAL_R = 26;
const DIAL_GAP = 26;   // room for the two source names under each dial

/* Does this vehicle actually carry the sensor?
 *
 * Answered from the checks the detector is running, not from the vehicle's
 * name and not from whether a reading happens to be in the frame. Both are
 * misleading: the simulator sends a barometer reading on a lorry, and the
 * detector's truck profile ignores it, so a height row built from what
 * arrives in the frame would have shown a barometer the system does not use.
 *
 * The pair list is the detector's own statement of what it relies on, so
 * asking it means a new vehicle profile needs no change here.
 */
function fittedHere(sensor) {
  const st = latest;
  if (!st || !st.pair_states) return false;
  for (const key of Object.keys(st.pair_states)) {
    const [left, right] = key.split(":");
    if (left === "health") {
      if (right === sensor) return true;
      continue;
    }
    if (left.split("-").includes(sensor)) return true;
  }
  return false;
}

function drawCompass(w, h) {
  // Kept under the old name because draw() calls it; it is now the whole
  // cluster, laid out down the right-hand edge.
  let y = 20 + DIAL_R;
  const x = w - 20 - DIAL_R;

  drawHeadingDial(x, y);
  if (fittedHere("baro")) { y += DIAL_R * 2 + DIAL_GAP; drawHeightDial(x, y); }
  if (fittedHere("odom")) { y += DIAL_R * 2 + DIAL_GAP; drawSpeedDial(x, y); }
  reserve(x - DIAL_R - 12, 8, DIAL_R * 2 + 24, y + DIAL_R + DIAL_GAP);
}

/* Name both needles under the dial, in their own colours.
 *
 * Orange means "GPS says" on the map, and the same orange was drawing the
 * compass needle here — so a judge who selected the compass saw an orange
 * arrow and quite reasonably read it as the GPS. Reusing a colour across two
 * meanings is the mistake; saying which is which fixes it without giving up
 * the one honest convention the page has, that orange is the reading under
 * suspicion and blue is what the vehicle worked out for itself.
 */
function dialKey(x, y, claimed, own) {
  ctx.font = "600 7.5px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillStyle = COLOR.claimed;
  ctx.fillText(claimed, x - DIAL_R * 0.52, y + DIAL_R + 19);
  ctx.fillStyle = COLOR.witness;
  ctx.fillText(own, x + DIAL_R * 0.52, y + DIAL_R + 19);
  ctx.textAlign = "left";
}

function dialFace(x, y, label) {
  ctx.beginPath();
  ctx.arc(x, y, DIAL_R, 0, Math.PI * 2);
  ctx.fillStyle = CANVAS_BG;
  ctx.globalAlpha = 0.88;
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.strokeStyle = COLOR.gridMajor;
  ctx.lineWidth = 1.4;
  ctx.stroke();

  ctx.fillStyle = COLOR.text;
  ctx.font = "600 8px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(label, x, y + DIAL_R + 10);
  ctx.textAlign = "left";
}

function needle(x, y, degrees, colour, width, length) {
  const rad = (degrees - 90) * Math.PI / 180;   // 0 deg points up
  ctx.strokeStyle = colour;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(x - Math.cos(rad) * length * 0.28, y - Math.sin(rad) * length * 0.28);
  ctx.lineTo(x + Math.cos(rad) * length, y + Math.sin(rad) * length);
  ctx.stroke();
}

/* Which way we are pointing: the compass against the gyro's own heading.
 * A magnet or a hand on the compass swings one needle and leaves the other,
 * which is the entire interference case as a picture. */
function drawHeadingDial(x, y) {
  dialFace(x, y, "HEADING");
  ctx.fillStyle = COLOR.text;
  ctx.font = "700 8px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("N", x, y - DIAL_R + 8);
  ctx.textAlign = "left";

  const gyro = latest ? latest.gyro_heading_deg : null;
  const compass = rawFrame && rawFrame.mag ? rawFrame.mag.heading_deg : null;
  if (gyro != null) needle(x, y, gyro, COLOR.witness, 2, DIAL_R - 8);
  if (compass != null) needle(x, y, compass, COLOR.claimed, 2.6, DIAL_R - 6);

  dialKey(x, y, "compass", "gyro");

  if (compass != null && gyro != null) {
    let gap = Math.abs(((compass - gyro + 540) % 360) - 180);
    if (gap > 12) dialAlarm(x, y, `${Math.round(gap)}°`);
  }
}

/* How high we are: GPS against the barometer. */
function drawHeightDial(x, y) {
  dialFace(x, y, "HEIGHT");
  const gps = latest && latest.gnss ? latest.gnss.u : null;
  const own = latest && latest.witness ? latest.witness.u : null;
  if (gps == null && own == null) return;

  // A tape rather than a needle: height is a level, not a direction.
  const span = Math.max(30, Math.abs((gps || 0) - (own || 0)) * 1.6);
  const mark = (value, colour, width) => {
    if (value == null) return;
    const t = Math.max(-1, Math.min(1, value / span));
    const py = y + DIAL_R * 0.62 * -t;
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(x - DIAL_R * 0.55, py);
    ctx.lineTo(x + DIAL_R * 0.55, py);
    ctx.stroke();
  };
  mark(own, COLOR.witness, 2);
  mark(gps, COLOR.claimed, 2.6);

  dialKey(x, y, "GPS", "baro");

  if (gps != null && own != null && Math.abs(gps - own) > 12) {
    dialAlarm(x, y, `${Math.round(Math.abs(gps - own))}m`);
  }
}

/* How fast we are going: the wheels against our own estimate. */
function drawSpeedDial(x, y) {
  dialFace(x, y, "SPEED");
  const wheels = rawFrame && rawFrame.odom ? rawFrame.odom.wheel_speed_mps : null;
  const own = latest && latest.witness ? latest.witness.speed_mps : null;
  const top = Math.max(25, wheels || 0, own || 0);
  // Sweep from -120 to +120 degrees, the way a speedometer reads.
  const sweep = (v) => -120 + Math.max(0, Math.min(1, v / top)) * 240;
  if (own != null) needle(x, y, sweep(own), COLOR.witness, 2, DIAL_R - 8);
  if (wheels != null) needle(x, y, sweep(wheels), COLOR.claimed, 2.6, DIAL_R - 6);

  dialKey(x, y, "wheels", "own");

  if (wheels != null && own != null && Math.abs(wheels - own) > 4) {
    dialAlarm(x, y, `${Math.round(Math.abs(wheels - own))}`);
  }
}

/* A ring and a figure when the two measurements have come apart. The number
 * is the gap itself, so the disagreement is on screen rather than implied. */
function dialAlarm(x, y, text) {
  ctx.strokeStyle = COLOR.link;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(x, y, DIAL_R + 2.5, 0, Math.PI * 2);
  ctx.stroke();

  ctx.font = "700 9px ui-monospace, monospace";
  ctx.textAlign = "center";
  ctx.lineWidth = 3;
  ctx.strokeStyle = CANVAS_BG;
  ctx.strokeText(text, x, y - DIAL_R - 5);
  ctx.fillStyle = COLOR.link;
  ctx.fillText(text, x, y - DIAL_R - 5);
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

/* --- the answer, in a band that never scrolls ----------------------------
 *
 * The rail was eight panels of equal weight in one scrolling column, and the
 * verdict — the entire point of the product — could sit below the fold while
 * a table of reference numbers sat above it. That is how an answer gets
 * missed in a room.
 *
 * So the answer lives here, fixed, and everything else scrolls underneath.
 * Nothing was deleted: the verdict panel, the working, the readings and the
 * reference numbers are all still there, further down.
 *
 * Every word comes off the ordinary stream. Where the detector refuses to
 * name a sensor this says so, in those words, because refusing to answer is
 * the feature and hiding it would be the lie.
 */
function renderAnswer(snapshot) {
  const band = el("answer");
  const s = snapshot ? snapshot.state : null;

  if (!s) {
    band.dataset.state = "IDLE";
    el("answerlabel").textContent = "Waiting";
    el("answerwhat").textContent = "No run";
    el("answerwhy").textContent = "Start a scenario from the control screen.";
    el("sep").textContent = "—";
    return;
  }

  const state = s.anchored ? s.state : "IDLE";
  band.dataset.state = state;

  const blame = s.blame || {};
  const cause = s.cause || {};
  const named = blame.guilty && blame.guilty !== "cannot_isolate";
  const who = named ? (SENSOR_LABEL[blame.guilty] || blame.guilty) : null;

  if (!s.anchored) {
    el("answerlabel").textContent = "Settling";
    el("answerwhat").textContent = "Anchoring";
    el("answerwhy").textContent =
      "Building enough history to have an opinion. Nothing is being judged yet.";
  } else if (state === "ALERT" && named) {
    const word = { attack: "is being spoofed", fault: "has failed",
                   interference: "is being interfered with" }[cause.label]
                 || "cannot be trusted";
    el("answerlabel").textContent = "The answer";
    el("answerwhat").textContent = `${who} ${word}`;
    el("answerwhy").textContent = cause.action || cause.reason || "";
  } else if (state === "ALERT") {
    // The truck run reads this for about twenty seconds before it settles on
    // a sensor, and a judge who is not told to expect it reads the gap as
    // flakiness rather than as restraint.
    el("answerlabel").textContent = "The answer";
    el("answerwhat").textContent = "Something is wrong";
    el("answerwhy").textContent =
      "Not enough evidence yet to say which sensor. It will not guess.";
  } else if (state === "WATCH") {
    el("answerlabel").textContent = "Watching";
    el("answerwhat").textContent = "Something may be wrong";
    el("answerwhy").textContent =
      "One check is drifting. Not yet enough to raise an alert.";
  } else {
    const checks = (s.pairs || []).filter((p) => p.valid || p.stale).length;
    el("answerlabel").textContent = "All quiet";
    el("answerwhat").textContent = "Everything agrees";
    el("answerwhy").textContent = checks
      ? `All ${checks} cross-checks agree. Every sensor is telling the same story.`
      : "Every sensor is telling the same story.";
  }
}

function renderPanels(snapshot) {
  const s = snapshot.state;
  if (!s) return;

  const count = Object.keys(fleet).length;
  el("mode").textContent = count > 1 ? `FLEET · ${count} VEHICLES` : "SINGLE VEHICLE";
  el("mode").dataset.mode = count > 1 ? "fleet" : "single";

  const state = s.anchored ? s.state : "IDLE";

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

/* --- the stream ---------------------------------------------------------
 *
 * The run is started from the other page now, so this one cannot reset itself
 * when a button is pressed — it has to notice. A new run_id means a new
 * origin, and an origin left anchored on the previous run puts the whole map
 * a kilometre out with nothing on screen saying so.
 */
let lastRunId = null;

function onNewRun() {
  reference = null;
  trails = { gnss: [], witness: [] };
  fleet = {}; zones = []; advisories = []; latest = null;
  // Or the camera eases across from wherever the last run finished, panning
  // through a kilometre of countryside while the new one is already driving.
  cam = null;
  roadsPreparedFor = null;   // a new run anchors a new origin
  clearTraces();
  clearSensorTracks();
}

let lastSnapshot = null;

onSnapshot((snapshot) => {
  lastSnapshot = snapshot;
  const runId = snapshot.state ? snapshot.state.run_id : null;
  if (runId !== lastRunId) {
    lastRunId = runId;
    onNewRun();
  }

  latest = snapshot.state;
  rawFrame = snapshot.raw;
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

  if (!renderStatus(snapshot)) {
    // No run. Everything that describes one goes away rather than sitting
    // there showing the last one's numbers as though they were current.
    for (const id of ["banner", "verdictpanel", "navpanel", "fleetpanel",
                      "proofpanel", "tracepanel"]) {
      el(id).hidden = true;
    }
    el("mode").textContent = "SINGLE VEHICLE";
    renderAnswer(null);
    draw();
    return;
  }

  renderPanels(snapshot);
  renderAnswer(snapshot);
  renderLegend();
  pushTrace(snapshot);
  pushSensorTracks(snapshot);
  renderTraces(snapshot);
  renderProof(snapshot);
  draw();
});

async function loadBasemap() {
  let data = null;
  try {
    data = await (await fetch("/basemap")).json();
  } catch (err) {
    data = null;                           // a grid is still usable
  }
  // Normalised rather than trusted. draw() reads roads.length on every frame,
  // so a response missing that key does not lose the roads — it throws out of
  // the paint loop and leaves a blank rectangle in front of the room, with
  // nothing in the console saying why.
  basemap = {
    roads: (data && Array.isArray(data.roads)) ? data.roads : [],
    areas: (data && Array.isArray(data.areas)) ? data.areas : [],
    rails: (data && Array.isArray(data.rails)) ? data.rails : [],
    places: (data && Array.isArray(data.places)) ? data.places : [],
    route: (data && Array.isArray(data.route)) ? data.route : [],
  };
  // A map baked before areas existed has none, and must still draw.
  areasPreparedFor = null;
  draw();
}

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
      { key: "compass", name: "compass", colour: "claimed" },
      { key: "gyro", name: "gyro", colour: "witness" },
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
      { key: "gps", name: "GPS", colour: "claimed" },
      { key: "own", name: "barometer", colour: "witness" },
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
      { key: "own", name: "own sensors", colour: "witness" },
      { key: "wheels", name: "wheels", colour: "claimed" },
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
      span.style.color = COLOR[line.colour] || line.colour;
      span.textContent =
        `${line.name} ${values[values.length - 1].toFixed(spec.unit === "°" ? 0 : 1)}${spec.unit}`;
      readout.appendChild(span);
    }
  }
}

function drawTrace(spec, store, series) {
  const chart = el(`canvas-${spec.id}`);
  const c = chart.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = chart.getBoundingClientRect();
  const w = rect.width || 280, h = 58;
  chart.width = Math.round(w * dpr);
  chart.height = Math.round(h * dpr);
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
    c.strokeStyle = COLOR[line.colour] || line.colour;
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

let WEB_COLOUR = themeColours({
  bad:   "--alert",
  good:  "--web-good",
  node:  "--panel",
  edge:  "--ink-3",
  text:  "--ink-2",
  onBad: "--panel",
});

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
  const web = el("web");
  const ctx2 = web.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = web.getBoundingClientRect();
  const w = rect.width || 300, h = 190;
  web.width = Math.round(w * dpr);
  web.height = Math.round(h * dpr);
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
    ctx2.strokeStyle = bad ? WEB_COLOUR.bad : WEB_COLOUR.good;
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
    ctx2.fillStyle = accused ? WEB_COLOUR.bad : WEB_COLOUR.node;
    ctx2.fill();
    ctx2.lineWidth = accused ? 0 : 1.4;
    ctx2.strokeStyle = WEB_COLOUR.edge;
    if (!accused) ctx2.stroke();

    ctx2.fillStyle = accused ? WEB_COLOUR.onBad : WEB_COLOUR.text;
    ctx2.font = accused
      ? "700 10px ui-sans-serif, system-ui, sans-serif"
      : "600 10px ui-sans-serif, system-ui, sans-serif";
    ctx2.textAlign = "center";
    ctx2.textBaseline = "middle";
    const name = SENSOR_NAME[sensor] || sensor;
    ctx2.fillText(name.length > 7 ? name.slice(0, 7) : name, x, y);
  }
}

/* --- wiring the map controls --------------------------------------------- */

function initMapControls() {
  const size = () => {
    const r = canvas.getBoundingClientRect();
    return [r.width, r.height, r];
  };

  el("zoomin").addEventListener("click", () => {
    const [w, h] = size();
    zoomBy(ZOOM_STEP, w / 2, h / 2, w, h);
  });
  el("zoomout").addEventListener("click", () => {
    const [w, h] = size();
    zoomBy(1 / ZOOM_STEP, w / 2, h / 2, w, h);
  });

  // The whole run, framed — the answer to "I cannot see where it started".
  el("zoomfit").addEventListener("click", () => {
    const [w, h] = size();
    takeManualControl();
    const fit = fitPoints(overviewPoints(), w, h, 70);
    manual.cx = fit.cx; manual.cy = fit.cy; manual.scale = fit.scale;
    draw();
  });

  el("zoomfollow").addEventListener("click", releaseManualControl);

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const [w, h, r] = size();
    zoomBy(event.deltaY < 0 ? 1.12 : 1 / 1.12,
           event.clientX - r.left, event.clientY - r.top, w, h);
  }, { passive: false });

  // Drag to pan. Pointer events rather than mouse events, so it works with a
  // finger and a trackpad on the day.
  let dragging = null;
  canvas.addEventListener("pointerdown", (event) => {
    takeManualControl();
    dragging = { x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
    canvas.parentElement.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging || !manual) return;
    manual.cx -= (event.clientX - dragging.x) / manual.scale;
    manual.cy += (event.clientY - dragging.y) / manual.scale;
    dragging = { x: event.clientX, y: event.clientY };
    draw();
  });
  const endDrag = (event) => {
    dragging = null;
    canvas.parentElement.classList.remove("dragging");
    if (event && event.pointerId != null && canvas.hasPointerCapture(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  // Keys, for a driver whose hands are already on the keyboard.
  window.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    const [w, h] = size();
    if (event.key === "+" || event.key === "=") zoomBy(ZOOM_STEP, w / 2, h / 2, w, h);
    else if (event.key === "-" || event.key === "_") zoomBy(1 / ZOOM_STEP, w / 2, h / 2, w, h);
    else if (event.key === "0") releaseManualControl();
  });
}

/* --- the theme -----------------------------------------------------------
 *
 * Everything drawn on canvas read its colours once, at load. A theme switch
 * changes the stylesheet and nothing else, so without this the page turns
 * dark around a map still painting roads on paper — and the legend, whose
 * swatches are CSS, would disagree with the tracks it labels.
 */
function refreshColours() {
  COLOR = themeColours({
    grid:      "--map-grid",
    gridMajor: "--map-grid-major",
    claimed:   "--claimed",
    witness:   "--witness",
    link:      "--alert",
    text:      "--ink-3",
    road:      "--map-road",
    roadCase:  "--map-road-case",
    roadText:  "--map-road-text",
    building:  "--map-building",
    builtEdge: "--map-built-edge",
    water:     "--map-water",
    waterEdge: "--map-water-edge",
    green:     "--map-green",
    sand:      "--map-sand",
    built:     "--map-built",
    campus:    "--map-campus",
    rail:      "--map-rail",
    chip:      "--map-chip",
    wash:      "--map-alert-wash",
    fade:      "--map-alert-fade",
  });
  CANVAS_BG = themeColours({ bg: "--map-bg" }).bg;
  SENSOR_COLOUR = themeColours({
    mag:  "--sensor-mag",
    odom: "--sensor-odom",
    baro: "--sensor-baro",
  });
  WEB_COLOUR = themeColours({
    bad:   "--alert",
    good:  "--web-good",
    node:  "--panel",
    edge:  "--ink-3",
    text:  "--ink-2",
    onBad: "--panel",
  });
  draw();
  // The traces and the agreement web only redraw when a frame arrives, and a
  // paused or finished run does not send one.
  if (lastSnapshot) {
    renderTraces(lastSnapshot);
    renderProof(lastSnapshot);
  }
}

onThemeChange(refreshColours);

/* --- go ------------------------------------------------------------------ */

initTheme();
initMapControls();
resize();
loadBasemap();
startStream();
requestAnimationFrame(animate);
