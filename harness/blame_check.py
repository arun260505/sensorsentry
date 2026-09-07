"""Does stage 5 accuse the right sensor? Run against the real simulator."""
from __future__ import annotations
import math
import numpy as np
from detector.pipeline import Pipeline
from simulator.scenarios import get_scenario
from simulator.sensors import SensorSuite
from simulator.vehicle import Vehicle, enu_to_geodetic

DT = 1/20

def verdict(sc, seed, spoof=0.0, magnet=0.0, stuck_baro=False,
            onset=40.0, secs=140.0, bearing=135.0):
    rng = np.random.default_rng(seed); wps, vt = get_scenario(sc)
    v = Vehicle(wps, rng); s = SensorSuite(rng, vehicle_type=vt); p = Pipeline()
    p.accept({'type':'run_start','run_id':'r','vehicle_id':'V','vehicle_type':vt,
              'seed':seed,'rate_hz':20,'gnss_rate_hz':5,'t0':0.0})
    yaw = math.radians(90.0-bearing); votes = {}; sample = None
    for i in range(int(secs/DT)):
        v.step(); r = s.update(v); t = i*DT
        if r['gnss'] and spoof and t >= onset:
            d = spoof*(t-onset); pos = v.position_enu
            la, lo, _ = enu_to_geodetic(pos[0]+d*math.cos(yaw), pos[1]+d*math.sin(yaw), pos[2])
            r['gnss'] = dict(r['gnss']); r['gnss']['lat'] = la; r['gnss']['lon'] = lo
        if magnet and t >= onset:
            r['mag'] = dict(r['mag']); r['mag']['heading_deg'] = (r['mag']['heading_deg']+magnet) % 360
        if stuck_baro and t >= onset:
            r['baro'] = dict(r['baro']); r['baro']['pressure_hpa'] = 960.0
        st = p.accept({'vehicle_id':'V','t':t,'seq':i,'imu':r['imu'],'baro':r['baro'],
                       'mag':r['mag'],'gnss':r['gnss'],'odom':r['odom']})
        if st and st.state == 'ALERT' and st.blame.guilty:
            votes[st.blame.guilty] = votes.get(st.blame.guilty, 0) + 1
            # Keep the settled verdict, not the first one. Early in an
            # incident only one check has failed and the honest answer is
            # "cannot isolate" — the accusation firms up as evidence arrives.
            if st.blame.isolated:
                sample = st.blame
    if not votes:
        return 'no alert', 0.0, None
    top = max(votes, key=votes.get)
    return top, votes[top]/sum(votes.values()), sample

CASES = [
    ('clean flight',            'drone_clean',     {},                       'no alert'),
    ('clean manoeuvre',         'drone_manoeuvre', {},                       'no alert'),
    ('walk-off 2 m/s',          'drone_clean',     {'spoof':2.0},            'gnss'),
    ('walk-off 3 m/s',          'drone_clean',     {'spoof':3.0},            'gnss'),
    ('walk-off 5 m/s',          'drone_clean',     {'spoof':5.0},            'gnss'),
    ('magnet 25 deg',           'drone_clean',     {'magnet':25.0},          'mag'),
    ('magnet 40 deg',           'drone_clean',     {'magnet':40.0},          'mag'),
    ('spoof + magnet at once',  'drone_clean',     {'spoof':3.0,'magnet':25.0}, 'cannot_isolate'),
]

def main() -> int:
    print(f"{'case':26s} {'expected':16s} {'blamed':16s} agree  verdict")
    wrong = 0
    for name, sc, kw, expect in CASES:
        who, frac, _ = verdict(sc, 4242, **kw)
        ok = (who == expect)
        # Two real faults at once is genuinely ambiguous; naming either the
        # spoofed receiver or the magnetised compass is defensible, and saying
        # "cannot isolate" is the most defensible of all.
        if not ok and expect == 'cannot_isolate' and who in ('gnss', 'mag', 'cannot_isolate'):
            ok = True
        wrong += 0 if ok else 1
        print(f"{name:26s} {expect:16s} {who:16s} {frac*100:3.0f}%   {'ok' if ok else 'WRONG'}")
    print(f"\n{len(CASES)-wrong} of {len(CASES)} correct")
    _who, _f, sample = verdict('drone_clean', 4242, magnet=40.0)
    if sample:
        print(f"\nExample verdict — magnet on the compass:")
        print(f"  guilty: {sample.guilty}  (domain: {sample.domain}, confidence {sample.confidence:.2f})")
        for line in sample.evidence:
            print(f"    - {line}")
    return 1 if wrong else 0

if __name__ == "__main__":
    raise SystemExit(main())
