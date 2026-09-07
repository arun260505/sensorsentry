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
        climb_rate = float(np.clip(alt_err * 1.0, -max_climb, max_climb))

        horiz_speed = new_speed * math.cos(math.atan2(abs(climb_rate), max(new_speed, 0.1)))

        self._vel[0] = horiz_dir_e * horiz_speed
        self._vel[1] = horiz_dir_n * horiz_speed
        self._vel[2] = climb_rate

        # Attitude — roll proportional to yaw rate (coordinated turn).
        # Cap at ±30° so gravity projection errors stay manageable.
        self._roll = float(np.clip(-yaw_err * 1.0, -math.radians(30), math.radians(30)))
        self._pitch = math.atan2(-self._vel[2], max(horiz_speed, 0.1))

        # Integrate position
        self._pos += self._vel * DT
        self._t += DT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _wrap_pi(angle: float) -> float:
    """Wrap angle to [-π, π]."""
    return (angle + math.pi) % (2 * math.pi) - math.pi
