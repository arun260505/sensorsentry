/* Run the map's drawing code against a stub canvas.
 *
 *     node tests/console_draw.js
 *
 * Everything the browser does has been untested for the whole project, and
 * CLAUDE.md already records two failures that only showed up live because of
 * it. `node --check` proves a file parses; it does not prove the map draws. A
 * marker that throws halfway through a path leaves a half-painted screen and a
 * console error nobody is looking at, in front of the room.
 *
 * So this stubs enough of a browser to load the real console scripts, then
 * calls the drawing for every vehicle at every zoom the map actually uses, and
 * fails on a throw, on a non-finite coordinate, or on a call that draws
 * nothing at all. NaN is worth catching on its own: canvas silently ignores a
 * NaN coordinate, so the shape simply does not appear and nothing anywhere
 * says why.
 *
 * Node only, and dev-time only. Rule 6 is about what the demo needs to run,
 * and the demo does not run this.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const CONSOLE_DIR = path.join(__dirname, "..", "console");

const calls = [];

function recordingContext() {
  const ctx = {
    canvas: { width: 1200, height: 800 },
    fillStyle: "#000", strokeStyle: "#000", lineWidth: 1,
    lineJoin: "miter", lineCap: "butt", font: "", textAlign: "left",
    textBaseline: "alphabetic", globalAlpha: 1,
  };
  const methods = [
    "save", "restore", "translate", "rotate", "scale", "setTransform",
    "beginPath", "closePath", "moveTo", "lineTo", "arc", "rect", "fill",
    "stroke", "fillRect", "strokeRect", "clearRect", "setLineDash",
    "fillText", "strokeText", "createRadialGradient", "createLinearGradient",
    "clip", "quadraticCurveTo", "bezierCurveTo", "ellipse",
  ];
  for (const name of methods) {
    ctx[name] = (...args) => {
      calls.push(name);
      for (const a of args) {
        if (typeof a === "number" && !Number.isFinite(a)) {
          throw new Error(`${name} got a non-finite argument: ${args.join(", ")}`);
        }
      }
      return name.startsWith("create") ? { addColorStop() {} } : undefined;
    };
  }
  ctx.measureText = (t) => ({ width: String(t).length * 6 });
  return ctx;
}

const element = () => ({
  getContext: recordingContext,
  getBoundingClientRect: () => ({ width: 1200, height: 800, left: 0, top: 0 }),
  addEventListener() {}, removeEventListener() {},
  querySelectorAll: () => [], querySelector: () => null,
  appendChild() {}, setAttribute() {},
  classList: { add() {}, remove() {}, toggle() {} },
  style: {}, dataset: {}, textContent: "", innerHTML: "", hidden: false,
  children: [], value: "", checked: false,
});

const sandbox = {
  console,
  el: () => element(),
  themeColours: (spec) => {
    const out = {};
    for (const key of Object.keys(spec)) out[key] = "#808080";
    return out;
  },
  getComputedStyle: () => ({ getPropertyValue: () => "#808080" }),
  document: {
    getElementById: () => element(), querySelector: () => element(),
    querySelectorAll: () => [], createElement: () => element(),
    documentElement: element(), body: element(), addEventListener() {},
  },
  window: {
    addEventListener() {}, devicePixelRatio: 1, requestAnimationFrame() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    localStorage: { getItem: () => null, setItem() {} },
    location: { pathname: "/" },
  },
  requestAnimationFrame() {},
  EventSource: function () { this.addEventListener = () => {}; this.onmessage = null; },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  setInterval() {}, setTimeout() {},
  Math, JSON, Date, Number, String, Object, Array,
};
sandbox.globalThis = sandbox;
sandbox.window.document = sandbox.document;

vm.createContext(sandbox);
// The page loads common.js first; so must we, or display.js is missing half
// its world before it has drawn anything.
for (const file of ["common.js", "display.js"]) {
  const code = fs.readFileSync(path.join(CONSOLE_DIR, file), "utf8");
  try {
    vm.runInContext(code, sandbox, { filename: file });
  } catch (err) {
    console.log(`FAIL  ${file} would not load: ${err.message}`);
    process.exit(1);
  }
}

/* The zooms the map really uses: fit-a-whole-run at the low end, follow at
 * 4 px/m, and beyond it for a hand on the zoom control. */
const ZOOMS = [0.29, 0.5, 1.0, 2.0, 4.0, 8.0];
let failures = 0;
let ops = 0;

function check(what, fn) {
  try {
    const before = calls.length;
    fn();
    const made = calls.length - before;
    if (made === 0) {
      console.log(`FAIL  ${what}: drew nothing at all`);
      failures += 1;
      return;
    }
    ops += made;
  } catch (err) {
    console.log(`FAIL  ${what}: ${err.message}`);
    failures += 1;
  }
}

for (const kind of ["truck", "drone", ""]) {
  for (const scale of ZOOMS) {
    for (const alert of [false, true]) {
      const label = `${kind || "unknown vehicle"} at ${scale} px/m`
                  + (alert ? ", alerting" : "");
      check(label, () => sandbox.drawVehicle(
        600, 400, 0.7, "#ff8800", alert, 10, kind, scale));
    }
  }
}

// The fleet draws with no vehicle type and no scale at all. It must still
// produce a marker rather than an exception.
check("fleet marker, no kind and no scale",
      () => sandbox.drawVehicle(100, 100, 0.0, "#00ff00", false, 7));

// A vehicle that has not moved has no heading to compute, and headingOf
// returns 0 for a short trail — the marker must still draw.
check("heading of an empty trail", () => {
  if (sandbox.headingOf([]) !== 0) throw new Error("expected 0 for no points");
  sandbox.drawVehicle(50, 50, sandbox.headingOf([]), "#fff", false, 9,
                      "truck", 4.0);
});

/* Which silhouette comes out at which zoom. Printed rather than asserted
 * exactly, because the threshold is a judgement about legibility and will move
 * — but a lorry must never be drawn at a zoom where it is a smudge, and the
 * drone must always be drawn, since one at true scale is four pixels. */
function shapeAt(kind, scale) {
  const before = calls.length;
  sandbox.drawVehicle(600, 400, 0.0, "#ff8800", false, 10, kind, scale);
  const made = calls.slice(before);
  if (made.includes("rect")) return "lorry";
  return made.filter((o) => o === "arc").length >= 4 ? "drone" : "arrow";
}

const truckShapes = ZOOMS.map((s) => shapeAt("truck", s));
const droneShapes = ZOOMS.map((s) => shapeAt("drone", s));

console.log("\n  zoom (px/m) " + ZOOMS.map((s) => String(s).padStart(7)).join(""));
console.log("  truck       " + truckShapes.map((s) => s.padStart(7)).join(""));
console.log("  drone       " + droneShapes.map((s) => s.padStart(7)).join(""));

if (truckShapes[truckShapes.length - 1] !== "lorry") {
  console.log("FAIL  the truck is never drawn as a lorry, even zoomed right in");
  failures += 1;
}
if (droneShapes.some((s) => s !== "drone")) {
  console.log("FAIL  the drone must be drawn at every zoom — at true scale it "
              + "is four pixels across and would vanish");
  failures += 1;
}

console.log(`\n${failures ? failures + " failed" : "all drawing checks passed"}`
            + ` — ${ops} canvas operations exercised`);
process.exit(failures ? 1 : 0);
