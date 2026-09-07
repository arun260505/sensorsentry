"""Four vehicles, one attacker. Does the zone appear, and who gets warned?

The claim a single-vehicle system cannot make: one drone reporting a lying
GPS might be a broken receiver, but several in the same square kilometre
inside the same minute is somebody with a transmitter — and the overlap says
roughly where.
"""
from __future__ import annotations
import math
import numpy as np
from detector.pipeline import Pipeline
from fleet.advisory import VehicleState, advise
from fleet.cluster import Incident, find_zones, metres_between
from simulator.scenarios import get_scenario
from simulator.sensors import SensorSuite
from simulator.vehicle import Vehicle, enu_to_geodetic

DT = 1/20
ONSET = 40.0
SPOOF_MPS = 3.0

# Four vehicles spread across a few kilometres. The first three sit within
# reach of one transmitter; the fourth is further out and clean.
FLEET = [
    ("DRONE-11", 0.0000, 0.0000, True),
    ("DRONE-12", 0.0070, 0.0030, True),
    ("DRONE-13", 0.0030, 0.0080, True),
    ("DRONE-14", 0.0180, 0.0150, False),
]
# DRONE-14 sits about two kilometres outside the zone and closing, which at
# its cruise is a bit over a minute and a half — comfortably inside the
# warning horizon. It was first placed at 0.0250/0.0210, which put it 185
# seconds out against a 180 second horizon and produced no warning. The logic
# was right and the test was standing on the line; moved the vehicle rather
# than widened the threshold, since the threshold is the thing under test.


def fly(vehicle_id, dlat, dlon, attacked, secs=110.0, seed=4242):
    """One vehicle. Returns its incidents and its final state."""
    wps, vt = get_scenario('drone_clean')[:2]
    rng = np.random.default_rng(seed)
    v = Vehicle(wps, rng); s = SensorSuite(rng, vehicle_type=vt); p = Pipeline()
    p.accept({'type':'run_start','run_id':vehicle_id,'vehicle_id':vehicle_id,
              'vehicle_type':vt,'seed':seed,'rate_hz':20,'gnss_rate_hz':5,'t0':0.0})
    yaw = math.radians(90.0 - 135.0)
    incidents = []; final = None
    for i in range(int(secs/DT)):
        v.step(); r = s.update(v); t = i*DT
        if r['gnss']:
            pos = v.position_enu
            e, n = pos[0], pos[1]
            if attacked and t >= ONSET:
                d = SPOOF_MPS * (t - ONSET)
                e += d*math.cos(yaw); n += d*math.sin(yaw)
            la, lo, al = enu_to_geodetic(e, n, pos[2])
            r['gnss'] = dict(r['gnss'])
            r['gnss']['lat'] = la + dlat
            r['gnss']['lon'] = lo + dlon
        st = p.accept({'vehicle_id':vehicle_id,'t':t,'seq':i,'imu':r['imu'],
                       'baro':r['baro'],'mag':r['mag'],'gnss':r['gnss'],'odom':r['odom']})
        if st is None or st.navigation is None:
            continue
        final = st
        if st.state == 'ALERT' and st.blame.isolated and st.cause.label != 'unclassified':
            incidents.append(Incident(
                vehicle_id=vehicle_id, t=t,
                # The vehicle's own estimate, never the reported fix — during
                # a spoof the reported fix is the attacker's choice.
                lat=st.navigation.lat, lon=st.navigation.lon,
                guilty=st.blame.guilty, cause=st.cause.label,
                confidence=st.cause.confidence))
    return incidents, final


def main() -> int:
    all_incidents = []; states = []
    print("Flying four vehicles. Three are inside one attacker's reach.")
    for vid, dlat, dlon, attacked in FLEET:
        inc, final = fly(vid, dlat, dlon, attacked)
        all_incidents.extend(inc)
        verdict = "no alert"
        if inc:
            verdict = f"{inc[-1].guilty}/{inc[-1].cause}"
        print(f"  {vid}  {'attacked' if attacked else 'clean    '}  -> {verdict}")
        states.append(VehicleState(
            vehicle_id=vid,
            lat=final.navigation.lat, lon=final.navigation.lon,
            speed_mps=math.hypot(final.witness.velocity.e, final.witness.velocity.n),
            under_attack=bool(inc)))

    zones = find_zones(all_incidents)
    print()
    if not zones:
        print("  -> FAIL: no attack zone formed")
        return 1
    z = zones[0]
    print(f"ATTACK ZONE  {z.lat:.5f}, {z.lon:.5f}")
    print(f"  radius     {z.radius_m:.0f} m")
    print(f"  vehicles   {', '.join(z.vehicles)}")
    print(f"  confidence {z.confidence:.2f}")
    print(f"  {z.describe()}")

    advisories = advise(states, zones)
    print()
    if advisories:
        for a in advisories:
            print(f"ADVISORY to {a.vehicle_id}: {a.message()}")
    else:
        print("no advisories issued")

    expected = {vid for vid, _a, _b, att in FLEET if att}
    ok_zone = set(z.vehicles) == expected
    ok_warn = [a.vehicle_id for a in advisories] == ["DRONE-14"]
    print()
    print(f"  -> zone names exactly the attacked vehicles: {'PASS' if ok_zone else 'FAIL'}")
    print(f"  -> the clean vehicle is warned:              {'PASS' if ok_warn else 'FAIL'}")
    return 0 if (ok_zone and ok_warn) else 1


if __name__ == "__main__":
    raise SystemExit(main())
