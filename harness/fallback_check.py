"""Does the vehicle keep its true position while being spoofed?

The claim the demo rests on: when GPS is dropped, what the vehicle believes
stays near where it actually is, while the reported fix walks away. This
measures both distances against the simulator's truth.
"""
from __future__ import annotations
import math
import numpy as np
from detector.geo import Origin, enu_from_llh
from detector.pipeline import Pipeline
from simulator.scenarios import get_scenario
from simulator.sensors import SensorSuite
from simulator.vehicle import Vehicle, enu_to_geodetic

DT = 1/20
ONSET = 30.0


def run(spoof_mps=3.0, secs=150.0, seed=4242, bearing=135.0):
    wps, vt = get_scenario('drone_clean')[:2]
    rng = np.random.default_rng(seed)
    v = Vehicle(wps, rng); s = SensorSuite(rng, vehicle_type=vt); p = Pipeline()
    p.accept({'type':'run_start','run_id':'fb','vehicle_id':'V','vehicle_type':vt,
              'seed':seed,'rate_hz':20,'gnss_rate_hz':5,'t0':0.0})
    yaw = math.radians(90.0 - bearing)
    rows = []
    for i in range(int(secs/DT)):
        v.step(); r = s.update(v); t = i*DT
        reported = None
        if r['gnss']:
            if spoof_mps and t >= ONSET:
                d = spoof_mps * (t - ONSET); pos = v.position_enu
                la, lo, al = enu_to_geodetic(pos[0]+d*math.cos(yaw), pos[1]+d*math.sin(yaw), pos[2])
                r['gnss'] = dict(r['gnss']); r['gnss']['lat']=la; r['gnss']['lon']=lo
            reported = (r['gnss']['lat'], r['gnss']['lon'], r['gnss']['alt'])
        st = p.accept({'vehicle_id':'V','t':t,'seq':i,'imu':r['imu'],'baro':r['baro'],
                       'mag':r['mag'],'gnss':r['gnss'],'odom':r['odom']})
        if st is None or st.navigation is None:
            continue
        nav = st.navigation
        origin = p.tracker.origin
        truth = v.position_enu
        anchor_true = rows[0][4] if rows else truth.copy()

        believed = enu_from_llh(nav.lat, nav.lon, nav.alt, origin)
        true_rel = (truth[0]-anchor_true[0], truth[1]-anchor_true[1])
        believed_err = math.hypot(believed.e-true_rel[0], believed.n-true_rel[1])

        gps_err = None
        if reported:
            gp = enu_from_llh(reported[0], reported[1], reported[2], origin)
            gps_err = math.hypot(gp.e-true_rel[0], gp.n-true_rel[1])
        rows.append((t, nav, believed_err, gps_err, anchor_true))
    return rows


def main() -> int:
    rows = run(spoof_mps=3.0)
    print("GPS walk-off at 3 m/s from t=30 s. Errors are against the truth.")
    print(f"  {'t':>6}  {'source':>14}  {'we believe':>11}  {'GPS claims':>11}  budget")
    for want in (20, 40, 60, 90, 120, 145):
        row = min(rows, key=lambda r: abs(r[0]-want))
        t, nav, ours, theirs, _a = row
        g = f"{theirs:7.0f} m" if theirs is not None else "      -"
        print(f"  {t:6.0f}  {nav.source:>14}  {ours:8.0f} m  {g}  {nav.error_budget_m:6.0f} m")

    # 1. During the window the budget says is usable, are we better than GPS?
    usable = [r for r in rows
              if r[1].source == "dead_reckoning"
              and r[1].seconds_remaining is not None and r[1].seconds_remaining > 0
              and r[3] is not None]
    ours = sum(r[2] for r in usable)/len(usable)
    theirs = sum(r[3] for r in usable)/len(usable)
    better = ours < theirs
    print()
    print(f"  While the budget says usable ({usable[0][0]:.0f}-{usable[-1][0]:.0f} s):")
    print(f"    we believe we are {ours:.0f} m from truth,")
    print(f"    GPS claims we are {theirs:.0f} m from truth.")
    print(f"  -> {'PASS' if better else 'FAIL'}: "
          f"{'better than the spoofed fix' if better else 'no better than the spoof'}")

    # 2. Does the budget ever promise better than it delivers? This is the one
    #    that actually matters. An operator deciding whether to press on is
    #    entitled to a number that is pessimistic, never optimistic.
    # Only while we are still claiming to be usable. Past that the system has
    # already said "stop or land", which is a stronger statement than any
    # number, and continuing to publish a precise figure would be theatre.
    understated = [r for r in rows
                   if r[1].source == "dead_reckoning"
                   and (r[1].seconds_remaining or 0) > 0
                   and r[2] > r[1].error_budget_m]
    honest = not understated
    print()
    if honest:
        print("  -> PASS: the error budget never claimed to be better than it was")
    else:
        worst = max(understated, key=lambda r: r[2]-r[1].error_budget_m)
        print(f"  -> FAIL: at t={worst[0]:.0f}s we were {worst[2]:.0f} m out "
              f"while claiming {worst[1].error_budget_m:.0f} m")
    ok = better and honest

    print()
    print("  Operator sees:", rows[-1][1].note)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
