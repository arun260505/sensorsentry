"""
scenarios.py — Named routes for the SensorSentry simulator.

Each scenario function returns (waypoints, vehicle_type_str[, schedule]).

Available scenarios
-------------------
drone_clean       : gentle 3-minute route — straight segments, easy turns,
                    one climb and descent, a hover section.
drone_manoeuvre   : same length, aggressively violent — hard banks, rapid
                    climbs/descents, sharp accelerations. Used to prove zero
                    false alarms during hard flying.
drone_walkoff     : drone_clean route, walk-off GPS spoof starts at t≈30 s.
drone_fault       : drone_clean route, IMU accelerometer bias fault (single
                    axis) starts at t≈30 s.
drone_magnet      : drone_clean route, magnet on compass starts at t≈30 s.
truck_clean       : 3-minute truck run on a small road network — junctions, a
                    stop, a turn onto a service road and back. No attack; must
                    produce no alarms.
truck_theft       : the demo. Truck drives the highway, a walk-off GPS spoof
                    starts at t=40 s, the real truck turns off onto the service
                    road and parks at the warehouse while the reported position
                    keeps driving up the highway.

Attack scenarios carry a schedule: a list of dicts describing when to start
each injector, read by run.py. The vehicle route is the honest one — the only
difference is what gets injected mid-route.
"""

from .vehicle import Waypoint


def _wp(e, n, u, speed=15.0, max_acc=3.0, turn_rate=0.3, max_climb=3.0):
    return Waypoint(e, n, u, speed_mps=speed, max_acc=max_acc, turn_rate=turn_rate, max_climb=max_climb)


def _twp(e, n, speed=18.0, max_acc=1.5, turn_rate=0.3):
    """Truck waypoint: level ground, gentle acceleration, truck steering.

    The limits are Task 3's: a truck accelerates at ~1.5 m/s², brakes harder,
    and turns at most ~0.3 rad/s. Waypoint `up` is 0 — a truck does not climb.
    """
    return Waypoint(e, n, 0.0, speed_mps=speed, max_acc=max_acc,
                    turn_rate=turn_rate, max_climb=0.0)


def _along_route(*, stop_at: float | None = None) -> list:
    """Waypoints that follow the real carriageway.

    The route comes out of `chennai.py`, which reads it from the baked
    OpenStreetMap geometry — so the lorry drives the actual centreline of the
    Vandalur - Kelambakkam Road and the actual road it turns onto, rather than
    a drawn approximation. That matters beyond looks: the detector's road check
    measures against those same roads, and a truck driving a line the map does
    not have would fail a check it should pass.

    Points are thinned to roughly one per forty-five metres. Every surveyed
    vertex would have the lorry steering constantly around noise that a real
    driver would not follow.

    `stop_at` puts a signal stop that far along the route, because a run with
    no stop in it never tests a stationary vehicle — and a stationary vehicle
    is where several checks are hardest.
    """
    import math

    from .chennai import ROUTE

    if not ROUTE:
        return []

    kept, run, since_stop = [ROUTE[0]], 0.0, 0.0
    for previous, point in zip(ROUTE, ROUTE[1:]):
        step = math.dist(previous, point)
        run += step
        since_stop += step
        if since_stop >= 45.0:
            kept.append(point)
            since_stop = 0.0
    if kept[-1] != ROUTE[-1]:
        kept.append(ROUTE[-1])

    total = 0.0
    waypoints = [_twp(kept[0][0], kept[0][1], speed=0.0)]
    for previous, point in zip(kept, kept[1:]):
        total += math.dist(previous, point)
        # Slow through the junction, which sits at the origin, the way any
        # lorry does — and it is the moment the story turns on.
        near_junction = math.hypot(*point) < 220.0
        speed = 9.0 if near_junction else 19.0
        if stop_at is not None and abs(total - stop_at) < 60.0:
            speed = 0.0
        waypoints.append(_twp(point[0], point[1], speed=speed))
    return waypoints



# ---------------------------------------------------------------------------
# drone_clean — 3-minute, gentle flight
# ---------------------------------------------------------------------------
def drone_clean():
    """
    Gentle patrol route:
      - straight cruise east
      - gentle left turn, cruise north
      - climb to 60 m AGL
      - 15-second hover (nearly zero speed)
      - gentle right turn, cruise east-northeast
      - descent back to 30 m
      - wide sweeping return south
      - final straight back toward origin

    Total flight time ≈ 180 s at mixed speeds of 8–18 m/s.
    """
    waypoints = [
        # Start: origin, 30 m AGL
        _wp(   0,    0,  30, speed=10.0, turn_rate=0.25),

        # Straight east 400 m
        _wp( 400,    0,  30, speed=18.0, turn_rate=0.25),

        # Gentle left turn, head north-east
        _wp( 600,  200,  30, speed=15.0, turn_rate=0.2),

        # Continue north-east, begin climb
        _wp( 700,  500,  60, speed=12.0, turn_rate=0.2),

        # Hover region — very low speed
        _wp( 720,  520,  60, speed= 2.0, max_acc=1.0, turn_rate=0.1),
        _wp( 730,  530,  60, speed= 2.0, max_acc=1.0, turn_rate=0.1),

        # Resume speed, gentle right turn heading east
        _wp( 900,  450,  60, speed=16.0, turn_rate=0.25),

        # Descent
        _wp(1100,  400,  30, speed=14.0, turn_rate=0.2),

        # Sweeping south
        _wp(1000,  100,  30, speed=18.0, turn_rate=0.15),

        # Return west
        _wp( 500, -100,  30, speed=16.0, turn_rate=0.2),

        # Final approach toward origin
        _wp( 100,  -50,  30, speed=10.0, turn_rate=0.3),
        _wp(   0,    0,  30, speed= 5.0, max_acc=2.0, turn_rate=0.3),
    ]
    return waypoints, "drone"


# ---------------------------------------------------------------------------
# drone_manoeuvre — 3-minute, genuinely violent
# ---------------------------------------------------------------------------
def drone_manoeuvre():
    """
    Hard flying, at the limit of what a commercial drone actually does — hard
    banks, rapid climbs and descents, sharp braking and acceleration. Must not
    trigger detector false alarms.

    Turn rate up to 0.6 rad/s (~34 deg/s) at up to 20 m/s, which is about 1.2 g
    of lateral acceleration, plus 4 m/s^2 along track.

    These were 1.2 rad/s at 30 m/s, which is 3.7 g — aerobatic racing, not a
    delivery drone, and it demands a 75-degree bank that the 30-degree limit in
    vehicle.py cannot produce, so the accelerometer and the attitude disagreed
    by construction. The point of this scenario is to prove we stay quiet
    through hard *legitimate* flying, so it has to be flying a real vehicle
    would do. Violent enough to break dead reckoning does not test the
    detector, it only tests arithmetic.
    """
    waypoints = [
        # Start at origin, 20 m AGL
        _wp(   0,    0,   20, speed= 5.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),

        # Rapid acceleration east
        _wp( 200,    0,   20, speed=18.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),

        # Hard left bank — tight turn north
        _wp( 210,  150,   20, speed=18.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),

        # Rapid climb to 120 m AGL
        _wp( 300,  200,  120, speed=16.0, max_acc=3.5, turn_rate=0.5, max_climb=4.0),

        # Hard right bank east
        _wp( 500,  150,  120, speed=18.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),

        # Rapid descent to 15 m
        _wp( 650,   80,   15, speed=16.0, max_acc=4.0, turn_rate=0.5, max_climb=4.0),

        # Sharp braking + left turn north
        _wp( 660,  200,   15, speed=20.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),

        # Sprint north
        _wp( 600,  600,   25, speed=20.0, max_acc=4.0, turn_rate=0.5, max_climb=4.0),

        # Hard right bank, rapid climb
        _wp( 800,  550,  100, speed=18.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),

        # Hard left, rapid descent
        _wp( 750,  350,   10, speed=18.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),

        # Full-throttle east sprint
        _wp(1100,  300,   10, speed=20.0, max_acc=4.0, turn_rate=0.5, max_climb=4.0),

        # Hard stop + climb + U-turn
        _wp(1120,  350,   80, speed= 8.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),
        _wp(1000,  400,   80, speed=18.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),

        # Final aggressive descent home
        _wp( 400,  100,   20, speed=20.0, max_acc=4.0, turn_rate=0.5, max_climb=4.0),
        _wp(   0,    0,   20, speed= 8.0, max_acc=4.0, turn_rate=0.6, max_climb=4.0),
    ]
    return waypoints, "drone"


# ---------------------------------------------------------------------------
# drone_walkoff — walk-off GPS spoof, starts at t=30 s
# ---------------------------------------------------------------------------
def drone_walkoff():
    """
    Normal drone_clean route. At t≈30 s, a walk-off GPS attack starts:
    the reported position drifts 2 m/s east (bearing 90°).

    The schedule is a list of dicts:
      t_start   : simulation time (s) to arm the injector
      kind      : 'attack' | 'fault' | 'interference'
      cls       : injector class name (string, looked up in run.py)
      kwargs    : constructor arguments for that class
    """
    waypoints, vehicle_type = drone_clean()
    schedule = [
        {
            "t_start": 30.0,
            "kind":    "attack",
            "cls":     "WalkOff",
            "kwargs":  {"speed_mps": 2.0, "bearing_deg": 90.0},
        }
    ]
    return waypoints, vehicle_type, schedule


# ---------------------------------------------------------------------------
# drone_fault — IMU accelerometer bias fault, starts at t=30 s
# ---------------------------------------------------------------------------
def drone_fault():
    """
    Normal drone_clean route. At t≈30 s, the IMU's forward accelerometer
    develops a slow bias fault: ax gets a ramp of 0.05 m/s² per second while
    the other five channels stay honest. One axis only — no real sensor fails
    all six at once, and ramping every field made the fault read as six
    independent failures.
    """
    waypoints, vehicle_type = drone_clean()
    schedule = [
        {
            "t_start": 30.0,
            "kind":    "fault",
            "cls":     "Bias",
            "kwargs":  {"sensor": "imu", "rate_per_s": 0.05, "axis": "ax"},
        }
    ]
    return waypoints, vehicle_type, schedule


# ---------------------------------------------------------------------------
# drone_magnet — magnet on compass, starts at t=30 s
# ---------------------------------------------------------------------------
def drone_magnet():
    """
    Normal drone_clean route. At t≈30 s, a 45° magnetic offset is applied to
    the compass. The gyro is deliberately NOT changed — that discrepancy is
    exactly what lets the detector classify this as interference, not a turn.
    """
    waypoints, vehicle_type = drone_clean()
    schedule = [
        {
            "t_start": 30.0,
            "kind":    "interference",
            "cls":     "Magnet",
            "kwargs":  {"offset_deg": 45.0},
        }
    ]
    return waypoints, vehicle_type, schedule


# ---------------------------------------------------------------------------
# truck_clean — 3-minute run on a small road network, nothing injected
# ---------------------------------------------------------------------------
def truck_clean():
    """
    A container run down the Vandalur - Kelambakkam Road, and nothing goes
    wrong.

    Real geometry: out of VIT Chennai heading south-east, slowing for the
    Mambakkam junction where the Medavakkam road crosses, turning off, and
    running south away from the traffic. One signal stop on the way.

    No attack. Three minutes must pass without a single alarm. This is the
    zero-false-alarm gate for trucks, and it is also the run an operator will
    judge us on: a system that cries wolf on an honest delivery gets switched
    off in a week.
    """
    return _along_route(stop_at=1500.0), "truck"


def truck_theft():
    """
    The same run, and somebody is spoofing the tracker.

    The lorry really does turn off at Mambakkam and carry on south. The
    reported position keeps running down the Kelambakkam road at road speed,
    so the control room watches a container making normal progress toward a
    delivery that is not happening.

    This is how the theft works, and why the tracking system is no help:
    nothing in it is broken. The receiver is lied to before the position is
    ever transmitted, and everything downstream is a very reliable pipe for
    the lie.

    3.5 m/s of drift — a lorry's difference in speed, not a teleport. The
    point is that the screen looks ordinary.
    """
    schedule = [
        {
            "t_start": 35.0,
            "kind":    "attack",
            "cls":     "WalkOff",
            "kwargs":  {"speed_mps": 3.5, "bearing_deg": 115.0},
        }
    ]
    return _along_route(), "truck", schedule


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# The truck versions of the scripted drone runs
#
# Both vehicles now offer the same set, which matters for more than symmetry:
# the interesting differences between them only show up when the same attack
# is run on each. A walk-off is caught in seconds on a drone by the course
# check and takes a minute on a lorry — then the road check names it outright,
# which a drone can never do. Having only the drone versions meant that
# comparison could be described but not shown.
# ---------------------------------------------------------------------------
def truck_walkoff():
    """The Kelambakkam run, with a 3 m/s walk-off from t=35 s.

    Slower to catch than the drone's, and worth saying so: a lorry travels
    more slowly, so the same sideways drag swings its course over the ground
    less. What the lorry has instead is a road it must be on."""
    waypoints, vehicle_type = truck_clean()
    schedule = [
        {
            "t_start": 35.0,
            "kind":    "attack",
            "cls":     "WalkOff",
            "kwargs":  {"speed_mps": 3.0, "bearing_deg": 90.0},
        }
    ]
    return waypoints, vehicle_type, schedule


def truck_fault():
    """The same run, with a slow accelerometer bias from t=30 s.

    A failing part, not an attack, and the console must say so — same
    injection as `drone_fault`, so the two can be compared directly."""
    waypoints, vehicle_type = truck_clean()
    schedule = [
        {
            "t_start": 30.0,
            "kind":    "fault",
            "cls":     "Bias",
            "kwargs":  {"sensor": "imu", "rate_per_s": 0.05, "axis": "ax"},
        }
    ]
    return waypoints, vehicle_type, schedule


def truck_magnet():
    """The same run, with a 45 degree magnetic offset on the compass from
    t=30 s. The gyro is deliberately untouched: that disagreement is what
    separates interference from a turn."""
    waypoints, vehicle_type = truck_clean()
    schedule = [
        {
            "t_start": 30.0,
            "kind":    "interference",
            "cls":     "Magnet",
            "kwargs":  {"offset_deg": 45.0},
        }
    ]
    return waypoints, vehicle_type, schedule


SCENARIOS = {
    "drone_clean":      drone_clean,
    "drone_manoeuvre":  drone_manoeuvre,
    "drone_walkoff":    drone_walkoff,
    "drone_fault":      drone_fault,
    "drone_magnet":     drone_magnet,
    "truck_clean":      truck_clean,
    "truck_walkoff":    truck_walkoff,
    "truck_fault":      truck_fault,
    "truck_magnet":     truck_magnet,
    "truck_theft":      truck_theft,
}


def list_scenarios():
    return list(SCENARIOS.keys())


def get_scenario(name: str):
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario {name!r}. Available: {list_scenarios()}")
    result = SCENARIOS[name]()
    # Normalise: scenarios without a schedule return (wps, vtype);
    # attack scenarios return (wps, vtype, schedule).
    if len(result) == 2:
        return result[0], result[1], []   # no schedule
    return result   # (wps, vtype, schedule)
