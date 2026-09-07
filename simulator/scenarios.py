"""
scenarios.py — Named flight routes for the SensorSentry simulator.

Each scenario function returns (waypoints, vehicle_type_str).

Available scenarios
-------------------
drone_clean       : gentle 3-minute route — straight segments, easy turns,
                    one climb and descent, a hover section.
drone_manoeuvre   : same length, aggressively violent — hard banks, rapid
                    climbs/descents, sharp accelerations. Used to prove zero
                    false alarms during hard flying.
"""

from .vehicle import Waypoint


def _wp(e, n, u, speed=15.0, max_acc=3.0, turn_rate=0.3, max_climb=3.0):
    return Waypoint(e, n, u, speed_mps=speed, max_acc=max_acc, turn_rate=turn_rate, max_climb=max_climb)


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
# Registry
# ---------------------------------------------------------------------------
SCENARIOS = {
    "drone_clean":      drone_clean,
    "drone_manoeuvre":  drone_manoeuvre,
}


def list_scenarios():
    return list(SCENARIOS.keys())


def get_scenario(name: str):
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario {name!r}. Available: {list_scenarios()}")
    return SCENARIOS[name]()
