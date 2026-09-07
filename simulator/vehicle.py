"""
vehicle.py — Vehicle motion model for SensorSentry simulator.

Works entirely in local ENU (East-North-Up) metres.
Origin: Coimbatore airport area, 11.0168 N, 76.9558 E.

Internal state (never sent over the wire):
  position_enu : np.ndarray [3]  — metres from origin
  velocity_enu : np.ndarray [3]  — m/s
  attitude_rad : np.ndarray [3]  — roll, pitch, yaw (rad) in body frame
  t            : float           — monotonic simulation time (seconds)
"""

import math
import numpy as np


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ORIGIN_LAT = 11.0168   # degrees
ORIGIN_LON = 76.9558   # degrees
ORIGIN_ALT = 412.0     # metres ASL

# Earth radius for small-angle lat/lon conversion (flat-earth is fine here)
R_EARTH = 6_371_000.0  # metres

GRAVITY = 9.79  # m/s²  (local Coimbatore value, close enough)

# Airframe limits. These are not cosmetic: every one of them bounds a rate the
# IMU has to report honestly, and a vehicle model that changes state faster
# than its own sensors could measure hands the detector a physically
# impossible history to integrate.
MAX_BANK_RAD = math.radians(30.0)    # steepest coordinated turn
MAX_ROLL_RATE = math.radians(120.0)  # rad/s, brisk but flyable
MAX_PITCH_RATE = math.radians(60.0)  # rad/s
MAX_CLIMB_ACC = 3.0                  # m/s^2 change in vertical speed

DT = 1.0 / 20.0  # 0.05 s — simulation timestep (20 Hz)


def enu_to_geodetic(east: float, north: float, up: float):
    """Convert local ENU metres → (lat_deg, lon_deg, alt_m_asl)."""
    lat = ORIGIN_LAT + math.degrees(north / R_EARTH)
    lon = ORIGIN_LON + math.degrees(east / (R_EARTH * math.cos(math.radians(ORIGIN_LAT))))
    alt = ORIGIN_ALT + up
    return lat, lon, alt


# ---------------------------------------------------------------------------
# Waypoint helpers
# ---------------------------------------------------------------------------
class Waypoint:
    """
    A single waypoint the vehicle will fly toward.

    speed_mps   : cruise speed when approaching this waypoint
    max_acc     : maximum acceleration when changing speed (m/s²)
    turn_rate   : maximum yaw rate (rad/s) — lower = gentler turn
    max_climb   : maximum vertical rate (m/s) — lower = gentler altitude changes
    """
    __slots__ = ("east", "north", "up", "speed_mps", "max_acc", "turn_rate", "max_climb")

    def __init__(self, east, north, up, speed_mps=15.0, max_acc=3.0, turn_rate=0.3, max_climb=3.0):
        self.east = east
        self.north = north
        self.up = up
        self.speed_mps = speed_mps
        self.max_acc = max_acc
        self.turn_rate = turn_rate
        self.max_climb = max_climb

    @property
    def position(self):
        return np.array([self.east, self.north, self.up], dtype=float)


# ---------------------------------------------------------------------------
# Vehicle
# ---------------------------------------------------------------------------
class Vehicle:
    """
    Drives a drone along a list of Waypoints.

    All state is private/internal — nothing here goes into a sensor frame
    without going through sensors.py first.
    """

    def __init__(self, waypoints, rng: np.random.Generator):
        self._wp = waypoints
        self._wp_idx = 0
        self._rng = rng

        first = waypoints[0]
        self._pos = first.position.copy()          # ENU metres
        self._vel = np.zeros(3)                    # ENU m/s
        self._roll = 0.0                           # rad
        self._pitch = 0.0                          # rad
        self._yaw = 0.0                            # rad — 0 = East
        self._climb_rate = 0.0                     # m/s, rate-limited
        self._t = 0.0

    # ------------------------------------------------------------------
    # Public read-only properties (used by sensors.py, never by publisher)
    # ------------------------------------------------------------------
    @property
    def position_enu(self) -> np.ndarray:
        return self._pos.copy()

    @property
    def velocity_enu(self) -> np.ndarray:
        return self._vel.copy()

    @property
    def roll_rad(self) -> float:
        return self._roll

    @property
    def pitch_rad(self) -> float:
        return self._pitch

    @property
    def yaw_rad(self) -> float:
        return self._yaw

    @property
    def speed_mps(self) -> float:
        return float(np.linalg.norm(self._vel))

    @property
    def t(self) -> float:
        return self._t

    @property
    def done(self) -> bool:
        return self._wp_idx >= len(self._wp)

    # ------------------------------------------------------------------
    # Step
    # ------------------------------------------------------------------
    def step(self):
        """Advance simulation by one timestep (DT seconds)."""
        if self.done:
            return

        wp = self._wp[self._wp_idx]
        to_wp = wp.position - self._pos

        dist = np.linalg.norm(to_wp)

        # Arrived at waypoint?
        arrival_threshold = max(2.0, self.speed_mps * DT * 2)
        if dist < arrival_threshold:
            self._wp_idx += 1
            if self.done:
                return
            wp = self._wp[self._wp_idx]
            to_wp = wp.position - self._pos
            dist = np.linalg.norm(to_wp)

        # Desired direction (unit vector)
        if dist > 0.01:
            direction = to_wp / dist
        else:
            direction = np.array([1.0, 0.0, 0.0])

        # Desired yaw — atan2(east, north) since yaw=0 is East? 
        # Actually we define yaw=0 as East, increasing CCW (standard ENU).
        # Heading for mag is measured from North, CW — handled in sensors.py.
        desired_yaw = math.atan2(direction[1], direction[0])  # ENU: atan2(N, E)? 
        # atan2(north_component, east_component) gives angle from East axis CCW — that's our yaw.

        # Steer yaw gradually
        yaw_err = _wrap_pi(desired_yaw - self._yaw)
        yaw_change = np.clip(yaw_err, -wp.turn_rate * DT, wp.turn_rate * DT)
        self._yaw += yaw_change

        # Desired speed along heading direction
        desired_speed = wp.speed_mps
        current_speed = self.speed_mps

        # Accelerate/decelerate toward desired speed
        speed_err = desired_speed - current_speed
        acc = np.clip(speed_err / DT, -wp.max_acc, wp.max_acc)
        new_speed = current_speed + acc * DT

        # Horizontal velocity from yaw, vertical from up-component of direction
        horiz_dir_e = math.cos(self._yaw)
        horiz_dir_n = math.sin(self._yaw)

        # Vertical: proportional control to reach waypoint altitude.
        # Max climb rate 3 m/s keeps pitch angles below ~12°, limiting
        # gravity-projection errors in the dead-reckoning integrator.
        alt_err = wp.up - self._pos[2]
        max_climb = getattr(wp, 'max_climb', 3.0)
        wanted_climb = float(np.clip(alt_err * 1.0, -max_climb, max_climb))
        # Rate-limited, not assigned: reaching a waypoint steps alt_err, and a
        # step in vertical velocity is an infinite acceleration the IMU would
        # have to report.
        self._climb_rate += float(
            np.clip(wanted_climb - self._climb_rate, -MAX_CLIMB_ACC * DT, MAX_CLIMB_ACC * DT)
        )
        climb_rate = self._climb_rate

        horiz_speed = new_speed * math.cos(math.atan2(abs(climb_rate), max(new_speed, 0.1)))

        self._vel[0] = horiz_dir_e * horiz_speed
        self._vel[1] = horiz_dir_n * horiz_speed
        self._vel[2] = climb_rate

        # Attitude — roll proportional to yaw error (coordinated turn), pitch
        # from the flight path angle. Both move toward the demand at a rate a
        # real airframe could achieve.
        #
        # Assigning these directly is what broke dead reckoning: arriving at a
        # waypoint steps the desired heading, so roll snapped from 0 to 30
        # degrees inside one 50 ms frame — 600 deg/s. No airframe rolls like
        # that and no gyro can report it, so the accelerometer showed a vehicle
        # steeply banked while the gyro showed one that never moved. The
        # detector believed the gyro, held its attitude level, and read the
        # tilted gravity vector as forward thrust that was never applied.
        wanted_roll = float(np.clip(-yaw_err, -MAX_BANK_RAD, MAX_BANK_RAD))
        self._roll += float(
            np.clip(wanted_roll - self._roll, -MAX_ROLL_RATE * DT, MAX_ROLL_RATE * DT)
        )

        wanted_pitch = math.atan2(-self._vel[2], max(horiz_speed, 0.1))
        self._pitch += float(
            np.clip(wanted_pitch - self._pitch, -MAX_PITCH_RATE * DT, MAX_PITCH_RATE * DT)
        )

        # Integrate position
        self._pos += self._vel * DT
        self._t += DT


# ---------------------------------------------------------------------------
# TruckVehicle — stays on the road, cannot fly, cannot turn on the spot
# ---------------------------------------------------------------------------
class TruckVehicle(Vehicle):
    """
    Trucks drive on roads: level (up == 0 always), limited steering and
    braking, and they stop. The motion limits are the ones in Task 3:

        cruise speed    15–22 m/s
        max acceleration  1.5 m/s²
        max braking       3.0 m/s²
        max turn rate     0.3 rad/s

    The base Vehicle model is airframe-flavoured: it climbs with a rate limit
    and roles into coordinated turns. A truck does neither, so this overrides
    `step()` to keep altitude at exactly the waypoint's `up` (which the truck
    scenarios set to a constant) and to steer with the gentler limits above.

    Nothing here reaches the wire; sensors.py reads this through the same
    read-only properties as the drone.
    """

    MAX_SPEED_MPS    = 22.0    # hard ceiling, cruise is set per waypoint
    MAX_ACC_MPS2     = 1.5     # acceleration demand
    MAX_BRAKE_MPS2   = 3.0     # braking demand
    MAX_TURN_RAD_S   = 0.3     # max yaw rate
    MAX_TURN_ACC_MPS2 = 6.0    # lateral g at speed, cut below road-limit worry
    MAX_BODY_ROLL    = math.radians(2.0)   # a truck does not bank like a drone

    STOP_HOLD_S      = 5.0     # dwell at a speed-0 waypoint (red light, depot)
    STOP_RADIUS_M    = 25.0    # how close counts as "at" the stop waypoint
    STOP_APPROACH_MPS = 8.0    # speed cap while braking toward a stop

    def __init__(self, waypoints, rng: np.random.Generator):
        # Forced ZYX-rotation-free truck: leave the drone roll rates untouched
        # at 0 so the gyro stays calm and the accelerometer only sees flat.
        self._wp = waypoints
        self._wp_idx = 0
        self._rng = rng
        self._stop_hold_s = 0.0

        first = waypoints[0]
        self._pos = first.position.copy()
        self._pos[2] = first.up              # start exactly at road altitude
        self._vel = np.zeros(3)
        self._roll = 0.0
        self._pitch = 0.0
        self._yaw = 0.0
        self._climb_rate = 0.0
        self._t = 0.0

    # -- truck speed / rate-friendly properties ------------------------
    @property
    def speed_mps(self) -> float:
        return math.hypot(self._vel[0], self._vel[1])

    def step(self):
        """Advance the truck by one timestep (DT seconds)."""
        if self.done:
            return

        wp = self._wp[self._wp_idx]
        to_wp = wp.position - self._pos
        to_wp[2] = 0.0                       # flat ground only
        dist = math.hypot(to_wp[0], to_wp[1])

        if wp.speed_mps == 0.0:
            self._handle_stop(wp, to_wp, dist)
            return

        # --- ordinary waypoint: cruise, brake, or accelerate -------------
        # Arrived?
        if dist < max(2.0, self.speed_mps * DT * 2):
            self._wp_idx += 1
            self._stop_hold_s = 0.0
            if self.done:
                return
            wp = self._wp[self._wp_idx]
            to_wp = wp.position - self._pos
            to_wp[2] = 0.0
            dist = math.hypot(to_wp[0], to_wp[1])
            if wp.speed_mps == 0.0:
                self._handle_stop(wp, to_wp, dist)
                return

        # Desired direction (unit vector, horizontal)
        if dist > 0.01:
            direction_e = to_wp[0] / dist
            direction_n = to_wp[1] / dist
        else:
            direction_e, direction_n = 1.0, 0.0

        # Desired heading — yaw = atan2(north, east) since 0 = East.
        desired_yaw = math.atan2(direction_n, direction_e)

        # Steer, limited by speed: a truck cannot turn on the spot. Lateral
        # acceleration is speed x yaw rate, so cap the yaw rate so that
        #   speed * yaw_rate <= MAX_TURN_ACC_MPS2.
        yaw_err = _wrap_pi(desired_yaw - self._yaw)
        lat_limited = (
            self.MAX_TURN_ACC_MPS2 / max(self.speed_mps, 1e-3)
            if self.speed_mps > 0.1 else self.MAX_TURN_RAD_S
        )
        yaw_rate_limit = min(self.MAX_TURN_RAD_S, lat_limited)
        yaw_change = np.clip(yaw_err, -yaw_rate_limit * DT, yaw_rate_limit * DT)
        self._yaw += float(yaw_change)

        # Desired speed — decelerate for the turn/stops with the braking limit
        desired_speed = min(wp.speed_mps, self.MAX_SPEED_MPS)
        current_speed = self.speed_mps
        speed_err = desired_speed - current_speed

        if speed_err >= 0.0:
            max_dv = self.MAX_ACC_MPS2 * DT
        else:
            max_dv = self.MAX_BRAKE_MPS2 * DT
        new_speed = max(0.0, current_speed + float(np.clip(speed_err, -max_dv, max_dv)))

        # Horizontal velocity from speed and heading. Altitude pinned to the
        # road level this waypoint is stated at.
        self._vel[0] = new_speed * math.cos(self._yaw)
        self._vel[1] = new_speed * math.sin(self._yaw)
        self._vel[2] = 0.0

        # Integrate
        self._pos += self._vel * DT
        self._pos[2] = wp.up
        self._t += DT

    # ------------------------------------------------------------------
    def _handle_stop(self, wp, to_wp, dist: float):
        """Brake to a halt at a speed-0 waypoint, hold, then move on.

        A plain "brake when close" never quits: braking distance at 3 m/s^2
        from 12 m/s is 24 m, so the truck sits short of the waypoint forever
        without the dwell logic, and gets within a couple of metres only by
        arriving at walking pace. Real stops also linger — a red light holds
        the truck for seconds, and the demo wants to *see* the wheels read
        0.0 while it waits.
        """
        # Heading to the stop point.
        if dist > 0.01:
            direction_e = to_wp[0] / dist
            direction_n = to_wp[1] / dist
        else:
            direction_e, direction_n = self._vel[0], self._vel[1]
            norm = math.hypot(direction_e, direction_n) or 1.0
            direction_e, direction_n = direction_e / norm, direction_n / norm

        current_speed = self.speed_mps

        # At rest and close enough: hold for the stop duration, then leave.
        if current_speed < 0.1 and dist < self.STOP_RADIUS_M:
            self._vel[0] = self._vel[1] = self._vel[2] = 0.0
            self._stop_hold_s += DT
            if self._stop_hold_s >= self.STOP_HOLD_S:
                self._wp_idx += 1
                self._stop_hold_s = 0.0
            self._t += DT
            return

        # Shape speed so the truck can always brake to a stop short of the
        # waypoint: target = what it has room to brake away from. Far away it
        # approaches at a modest cap; close in it smoothly bleeds off.
        room = max(0.0, dist - 2.0)
        stop_target = math.sqrt(2.0 * self.MAX_BRAKE_MPS2 * room)
        target_speed = min(stop_target, self.STOP_APPROACH_MPS)
        speed_err = target_speed - current_speed
        max_dv = (
            self.MAX_ACC_MPS2 * DT if speed_err >= 0.0
            else self.MAX_BRAKE_MPS2 * DT
        )
        new_speed = max(0.0, current_speed + float(np.clip(speed_err, -max_dv, max_dv)))

        # Steer toward the stop, still never turning on the spot.
        desired_yaw = math.atan2(direction_n, direction_e)
        yaw_err = _wrap_pi(desired_yaw - self._yaw)
        lat_limited = (
            self.MAX_TURN_ACC_MPS2 / max(current_speed, 1e-3)
            if current_speed > 0.1 else self.MAX_TURN_RAD_S
        )
        yaw_rate_limit = min(self.MAX_TURN_RAD_S, lat_limited)
        self._yaw += float(np.clip(yaw_err, -yaw_rate_limit * DT, yaw_rate_limit * DT))

        self._vel[0] = new_speed * math.cos(self._yaw)
        self._vel[1] = new_speed * math.sin(self._yaw)
        self._vel[2] = 0.0
        self._pos += self._vel * DT
        self._pos[2] = wp.up
        self._t += DT


# ---------------------------------------------------------------------------
# Factory — pick the motion model for a vehicle type
# ---------------------------------------------------------------------------
def make_vehicle(vehicle_type: str, waypoints, rng: np.random.Generator):
    """Return a Vehicle subclass for `vehicle_type` ('drone' or 'truck')."""
    if vehicle_type == "truck":
        return TruckVehicle(waypoints, rng)
    return Vehicle(waypoints, rng)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _wrap_pi(angle: float) -> float:
    """Wrap angle to [-π, π]."""
    return (angle + math.pi) % (2 * math.pi) - math.pi
