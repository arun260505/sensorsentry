# Data Contract — FROZEN

Everything crossing a process boundary is defined here. **Do not change these
shapes without telling the whole team** — three people build against them in
parallel.

Frozen: 7 September 2026.

---

## 1. Sensor frame — simulator → detector

JSON over **UDP port 5005**, one frame per datagram, **20 Hz**.

```json
{
  "vehicle_id": "TRUCK-42",
  "t": 1725701482.40,
  "seq": 8492,

  "gnss": {
    "lat": 11.024891,
    "lon": 76.955203,
    "alt": 412.7,
    "fix": 3,
    "sats": 11,
    "hdop": 0.9,
    "cn0_mean": 41.2
  },

  "imu": {
    "ax": 0.184, "ay": -0.037, "az": 9.792,
    "gx": 0.002, "gy": 0.011, "gz": -0.004
  },

  "baro": { "pressure_hpa": 964.31 },
  "mag":  { "heading_deg": 71.4 },
  "odom": { "wheel_speed_mps": 13.8 }
}
```

### Field notes

| Field | Type | Notes |
|---|---|---|
| `vehicle_id` | string | Unique per vehicle. Four of these run at once in Phase 9. |
| `t` | float | Seconds, monotonic, simulator clock. Not wall time. |
| `seq` | int | Increments by 1 every frame. Lets the detector spot dropped frames. |
| `gnss` | object or `null` | **`null` between GNSS updates** — GNSS runs at 5 Hz, frames at 20 Hz, so 3 of every 4 frames carry `null` here. |
| `gnss.alt` | float | Metres above sea level. |
| `gnss.fix` | int | `0` = no fix, `2` = 2D, `3` = 3D. |
| `gnss.cn0_mean` | float | Mean carrier-to-noise across tracked satellites, dB-Hz. |
| `imu` | object | Always present. `ax/ay/az` m/s² in body frame (az ≈ +9.79 at rest). `gx/gy/gz` rad/s. |
| `baro` | object | Always present. |
| `mag.heading_deg` | float | 0–360, 0 = north, increasing clockwise. |
| `odom` | object or `null` | **`null` for drones.** Trucks only. |

### Never present

These fields must **never** appear in a sensor frame. The detector cannot be
allowed to cheat, and this is what makes the demo provable:

```
true_lat  true_lon  true_alt  true_heading
attack_active  attack_type  attack_strength
fault_active  fault_type
scenario  is_spoofed  ground_truth  anything similar
```

The detector rejects unknown fields loudly at ingest. If you find yourself
wanting to add one for debugging, log it to a file on the simulator side
instead.

---

## 2. Stream header — simulator → detector

Sent once when a run starts, on the same UDP port.

```json
{
  "type": "run_start",
  "run_id": "r-20260907-142201-8f3a",
  "vehicle_id": "TRUCK-42",
  "vehicle_type": "truck",
  "seed": 748392011,
  "rate_hz": 20,
  "gnss_rate_hz": 5,
  "t0": 1725701400.00
}
```

`seed` is recorded so an incident can be replayed exactly later. It is a new
random value on every run — which is why detection times differ slightly run to
run, and that difference is our proof the demo isn't scripted.

`vehicle_type` is `"drone"` or `"truck"`. It tells the detector which sensors to
expect and which pairs to score. It does **not** tell it anything about attacks.

---

## 3. Verdict frame — detector → console

JSON over **Server-Sent Events**, `GET http://127.0.0.1:8080/stream`, ~10 Hz.

> **Changed 7 Sep 2026, was WebSocket.** The stream is one-way — the detector
> talks, the browser listens — so a WebSocket bought nothing, while SSE needs
> only the Python standard library where a WebSocket needs either a dependency
> or a hundred lines of hand-rolled frame encoding to fail on demo day.
> Controls stay HTTP POST (section 4). Nobody had built against the WebSocket
> yet; if you had, say so.

Each event carries the whole console state, not a delta — a browser that
reconnects mid-run is immediately correct with no replay logic:

```json
{
  "version": 4127,
  "state":   { ... the object below ... },
  "raw":     { ... the last sensor frame verbatim, for the feed panel ... },
  "violation": null,
  "trails":  { "gnss": [[e, n], ...], "witness": [[e, n], ...] }
}
```

`trails` are in local metres and are capped at 4000 points each. `violation`
is a string when a frame arrived carrying truth or attack state, and the
console shows it as a banner rather than hiding it.

The `state` object today reflects stages 1-3 and the residual. `trust`,
`pairs` and a real `verdict` arrive with phases 5-7; until then `state` is a
provisional threshold on the residual ratio and is labelled as such in the
code. The shape below is the target once those phases land.

```json
{
  "vehicle_id": "TRUCK-42",
  "t": 1725701482.40,

  "estimate": {
    "lat": 11.024871, "lon": 76.955198, "alt": 412.4,
    "source": "gnss_fused",
    "error_budget_m": 4.2
  },

  "trust": {
    "gnss": 0.18, "imu": 0.97, "baro": 0.95,
    "mag": 0.96, "odom": 0.98
  },

  "pairs": [
    { "a": "gnss", "b": "imu",  "residual": 142.8, "score": 0.04 },
    { "a": "gnss", "b": "odom", "residual": 138.1, "score": 0.05 }
  ],

  "verdict": {
    "state": "ALERT",
    "guilty": "gnss",
    "cause": "attack",
    "confidence": 0.91,
    "evidence": [
      "gnss-imu 142.8 m for 17 s",
      "gnss-odom 138.1 m for 17 s"
    ],
    "action": "Stop trusting GPS. Navigating on motion sensors. Alert control room."
  }
}
```

| Field | Values |
|---|---|
| `estimate.source` | `"gnss_fused"` or `"dead_reckoning"` |
| `verdict.state` | `"OK"`, `"WATCH"`, `"ALERT"` |
| `verdict.guilty` | sensor name, or `"cannot_isolate"`, or `null` when state is OK |
| `verdict.cause` | `"attack"`, `"fault"`, `"interference"`, `"unclassified"`, or `null` |

---

## 4. Control endpoint — console → simulator

HTTP on **port 5010**. Small and unceremonious on purpose.

| Method | Path | Body | Does |
|---|---|---|---|
| `POST` | `/start` | `{"scenario": "drone_clean"}` | Begins a run |
| `POST` | `/reset` | — | Stops and clears, ready to start again |
| `POST` | `/inject` | see below | Starts an attack or fault mid-run |
| `GET` | `/scenarios` | — | Lists available scenario names |

```json
POST /inject
{
  "kind": "attack",
  "type": "walkoff",
  "strength": 2.0,
  "bearing_deg": 90
}
```

`kind` is `"attack"` or `"fault"`. `strength` means metres per second of drift
for `walkoff`; each injector documents its own units.

---

## 5. Incident record — detector → disk

Append-only JSON lines in `evidence/<run_id>.jsonl`. Written when state leaves
`OK`. Must include the `seed` from the stream header so replay is exact.

### Fleet fields, added 7 Sep 2026

Positions now leave the detector as **lat/lon as well as local metres**, and
the snapshot carries every vehicle rather than one:

```json
{
  "focus": "DRONE-11",
  "vehicles": { "DRONE-11": { "state": {...}, "trails": {...} }, ... },
  "zones":    [ { "lat":..., "lon":..., "radius_m": 948,
                  "vehicles": ["DRONE-11","DRONE-12","DRONE-13"],
                  "confidence": 0.6, "describe": "..." } ],
  "advisories": [ { "vehicle_id": "DRONE-14", "distance_m": 1097,
                    "seconds_away": 94, "inside": false, "message": "..." } ]
}
```

Trails are **lat/lon pairs**, not metres. Each vehicle anchors its own local
origin on its own first fix, so its metres mean nothing to any other vehicle —
a fleet map built from them draws four vehicles on top of each other and puts
one three kilometres away next door. lat/lon is the only frame they share, and
the console converts everything into one reference of its own.
