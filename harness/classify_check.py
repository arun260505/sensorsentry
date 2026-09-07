"""Does stage 6 give the right cause? Attack, fault, or interference."""
from __future__ import annotations
import math
import numpy as np
from detector.pipeline import Pipeline
from simulator.scenarios import get_scenario
from simulator.sensors import SensorSuite
from simulator.vehicle import Vehicle, enu_to_geodetic

DT = 1/20


def verdict(sc, seed, *, spoof=0.0, magnet=0.0, fault=None,
            onset=40.0, secs=150.0, bearing=135.0):
    """fault: 'stuck_baro' | 'noisy_mag' | 'wander_mag'"""
    rng = np.random.default_rng(seed); wps, vt = get_scenario(sc)[:2]
    v = Vehicle(wps, rng); s = SensorSuite(rng, vehicle_type=vt); p = Pipeline()
    p.accept({'type':'run_start','run_id':'r','vehicle_id':'V','vehicle_type':vt,
              'seed':seed,'rate_hz':20,'gnss_rate_hz':5,'t0':0.0})
    yaw = math.radians(90.0-bearing)
    frozen = {}; wander = {'v': 0.0}
    votes = {}; last = None
    for i in range(int(secs/DT)):
        v.step(); r = s.update(v); t = i*DT
        if r['gnss'] and spoof and t >= onset:
            d = spoof*(t-onset); pos = v.position_enu
            la, lo, _ = enu_to_geodetic(pos[0]+d*math.cos(yaw), pos[1]+d*math.sin(yaw), pos[2])
            r['gnss'] = dict(r['gnss']); r['gnss']['lat'] = la; r['gnss']['lon'] = lo
        if magnet and t >= onset:
            r['mag'] = dict(r['mag']); r['mag']['heading_deg'] = (r['mag']['heading_deg']+magnet) % 360
        if fault == 'stuck_baro' and t >= onset:
            frozen.setdefault('baro', r['baro']['pressure_hpa'])
            r['baro'] = dict(r['baro']); r['baro']['pressure_hpa'] = frozen['baro']
        if fault == 'noisy_mag' and t >= onset:
            r['mag'] = dict(r['mag'])
            r['mag']['heading_deg'] = (r['mag']['heading_deg'] + rng.normal(0, 35.0)) % 360
        if fault == 'wander_mag' and t >= onset:
            # A compass whose reading wanders back and forth without any
            # consistent direction — degrading, not attacked.
            r['mag'] = dict(r['mag'])
            # A degrading compass wanders as a random walk, not a tidy sine —
            # it has no direction it is trying to go, which is exactly what
            # separates a failure from someone pulling on purpose.
            wander['v'] = wander['v'] * 0.995 + rng.normal(0, 3.0)
            r['mag']['heading_deg'] = (r['mag']['heading_deg'] + wander['v']) % 360
        st = p.accept({'vehicle_id':'V','t':t,'seq':i,'imu':r['imu'],'baro':r['baro'],
                       'mag':r['mag'],'gnss':r['gnss'],'odom':r['odom']})
        if st and st.state == 'ALERT' and st.blame.guilty:
            key = (st.blame.guilty, st.cause.label)
            votes[key] = votes.get(key, 0) + 1
            if st.cause.label != 'unclassified':
                last = st
    if not votes:
        return ('no alert', 'none'), 0.0, None
    top = max(votes, key=votes.get)
    return top, votes[top]/sum(votes.values()), last


CASES = [
    ('clean flight',        'drone_clean',     {},                    ('no alert','none')),
    ('clean manoeuvre',     'drone_manoeuvre', {},                    ('no alert','none')),
    ('GPS walk-off 3 m/s',  'drone_clean',     {'spoof':3.0},         ('gnss','attack')),
    ('GPS walk-off 5 m/s',  'drone_clean',     {'spoof':5.0},         ('gnss','attack')),
    ('magnet on compass',   'drone_clean',     {'magnet':40.0},       ('mag','interference')),
    ('compass gone noisy',  'drone_clean',     {'fault':'noisy_mag'}, ('mag','fault')),
    ('compass wandering',   'drone_clean',     {'fault':'wander_mag'},('mag','fault')),
    ('altitude sensor stuck','drone_clean',    {'fault':'stuck_baro'},('baro','fault')),
]


def main() -> int:
    print(f"{'case':24s} {'expected':22s} {'got':22s} agree")
    wrong = 0
    for name, sc, kw, expect in CASES:
        got, frac, _ = verdict(sc, 4242, **kw)
        ok = got == expect
        wrong += 0 if ok else 1
        e = f"{expect[0]}/{expect[1]}"; g = f"{got[0]}/{got[1]}"
        print(f"{name:24s} {e:22s} {g:22s} {frac*100:3.0f}%  {'ok' if ok else 'WRONG'}")
    print(f"\n{len(CASES)-wrong} of {len(CASES)} correct")

    for label, kw in [("GPS walk-off", {'spoof':3.0}),
                      ("magnet on the compass", {'magnet':40.0}),
                      ("compass gone noisy", {'fault':'noisy_mag'})]:
        _g, _f, st = verdict('drone_clean', 4242, **kw)
        if st:
            print(f"\nWhat the operator sees — {label}:")
            print(f"  {st.blame.guilty} / {st.cause.label} ({st.cause.confidence:.2f})")
            print(f"  {st.cause.reason}")
            print(f"  -> {st.cause.action}")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
