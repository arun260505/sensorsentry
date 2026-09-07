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
    A delivery truck working a small network called from roads.py:
      - leaves the depot and accelerates onto NH-544 east
      - cruises the highway, passes the service-road junction
      - turns onto the service road, stops at a red light mid-way
      - pulls away, returns up the service road, rejoins the highway
      - cruises west back to the depot and parks

    Junctions, a stop, a turn onto a service road and a turn back — everything
    truck_clean is supposed to contain. No attack. Must produce no alarms for
    the full three minutes: this is the zero-false-alarm gate for trucks.
    """
    waypoints = [
        _twp(   0,    0, speed=0.0),                 # depot — parked
        _twp( 400,   20, speed=18.0),                # accelerate onto NH-544
        _twp( 800,   60, speed=18.0),                # cruise east
        _twp(1200,  100, speed=14.0),                # service-road junction ahead
        _twp(1180,  -50, speed=12.0),                # turn onto the service road
        _twp(1150, -200, speed=0.0),                 # STOP at a red light
        _twp(1180,  -50, speed=12.0),                # pull away, back up the road
        _twp(1200,  100, speed=14.0),                # rejoin the highway
        _twp( 800,   60, speed=18.0),                # head west for home
        _twp( 400,   20, speed=10.0),                # slow for the depot
        _twp(   0,    0, speed=0.0),                 # arrive and park
    ]
    return waypoints, "truck"


# ---------------------------------------------------------------------------
# truck_theft — the demo: walk-off spoof, truck quietly diverted to warehouse
# ---------------------------------------------------------------------------
def truck_theft():
    """
    The cargo-theft story, played out:

      1. Truck drives NH-544 east at ~18 m/s, running normally.
      2. At t = 40 s a walk-off spoof starts (18 m/s along bearing 90°).
      3. The real truck turns off at the junction, down the service road, and
         parks at the warehouse with the engine off.
      4. The reported GPS position keeps driving up the highway.

    The reported path runs away east along the road while the real truck sits
    still at the warehouse — the map picture is the whole story.
    """
    waypoints = [
        _twp(   0,    0, speed=0.0),                 # depot — parked
        _twp( 400,   20, speed=18.0),                # accelerate onto NH-544
        _twp( 800,   60, speed=18.0),                # cruise east
        _twp(1200,  100, speed=14.0),                # junction — slow for it
        _twp(1180,  -50, speed=12.0),                # turn onto the service road
        _twp(1150, -200, speed=10.0),                # continue south
        _twp(1120, -350, speed= 8.0),                # bottom of the service road
        _twp(1100, -400, speed= 6.0),                # into the warehouse lane
        _twp(1050, -430, speed=0.0),                 # warehouse — park
    ]
    schedule = [
        {
            "t_start": 40.0,
            "kind":    "attack",
            "cls":     "WalkOff",
            # 3.5 m/s, not the 18 it was. Eighteen metres a second is faster
            # than the truck drives — the reported position leaves the road
            # immediately, drags the inertial estimate with it before the
            # alert can fire, and stops being a walk-off at all. A thief
            # wants the control room to see a truck making normal progress.
            "kwargs":  {"speed_mps": 3.5, "bearing_deg": 90.0},
        }
    ]
    return waypoints, "truck", schedule


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
SCENARIOS = {
    "drone_clean":      drone_clean,
    "drone_manoeuvre":  drone_manoeuvre,
    "drone_walkoff":    drone_walkoff,
    "drone_fault":      drone_fault,
    "drone_magnet":     drone_magnet,
    "truck_clean":      truck_clean,
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
