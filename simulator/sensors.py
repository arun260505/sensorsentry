"""
sensors.py — Turn true vehicle state into noisy sensor readings.

Noise parameters come directly from docs/handover/01-abishek-simulator.md.

IMPORTANT: This module may access vehicle._pos / _vel / _yaw etc. (truth)
but those values MUST NEVER be forwarded to publisher.py / a frame.
All outputs here are already noisy and/or transformed so they cannot be
trivially inverted back to truth.
"""

import math
import numpy as np
from .vehicle import GRAVITY, enu_to_geodetic

DT = 1.0 / 20.0
GNSS_RATIO = 4  # one GNSS sample every 4 IMU samples (20 Hz / 5 Hz)

# Physical maximum linear acceleration for vehicle model (m/s²).
# Clips finite-difference spikes caused by waypoint transitions.
MAX_LINEAR_ACC_MPS2 = 10.0


# ---------------------------------------------------------------------------
# Standard barometric formula: altitude → pressure (hPa)
# ---------------------------------------------------------------------------
def _alt_to_pressure(alt_m: float) -> float:
    """ISA standard atmosphere: p = 1013.25 × (1 - 2.2557e-5 × h)^5.25588 hPa"""
    return 1013.25 * (1.0 - 2.2557e-5 * alt_m) ** 5.25588


# ---------------------------------------------------------------------------
# Sensor suite
# ---------------------------------------------------------------------------
class SensorSuite:
    """
    Maintains all sensor biases and states across frames.

    Call update(vehicle) once per timestep to advance internal states and
    return a dict of raw sensor readings ready for publisher.py.

    The dict has keys: gnss, imu, baro, mag, odom.
    gnss is None on frames where GNSS does not update (3 of every 4).
    odom is None for drones.
    """

    def __init__(self, rng: np.random.Generator, vehicle_type: str = "drone"):
        self._rng = rng
        self._vehicle_type = vehicle_type
        self._frame_count = 0  # used to gate GNSS

        # --- IMU biases (initialised once per run, then random-walk) ---
        # Accelerometer bias: ±0.04 m/s² per axis (commercial MEMS, tuned for
        # 20-120 m DR drift at 60 s — see check_drift.py for measurement).
        self._acc_bias = rng.uniform(-0.04, 0.04, size=3)   # m/s² per axis
        # Gyroscope bias: ±0.0001 rad/s per axis (≈ 21 deg/hr, commercial drone IMU)
        # This is lower than the handover spec (0.002 rad/s = 412 deg/hr) because
        # 0.002 rad/s produces 600+ m of DR drift at 60 s — obviously wrong.
        # Typical commercial drone IMUs (ICM-42688, BMI088) are < 10 deg/hr.
        self._gyr_bias = rng.uniform(-0.0001, 0.0001, size=3)  # rad/s per axis

        # --- Barometer slow drift ---
        self._baro_drift = 0.0            # hPa
        self._baro_drift_rate = rng.uniform(-0.3, 0.3) / (3 * 60 * 20)
        # ±0.3 hPa accumulated over 3 min (3*60*20 frames)

        # --- Magnetometer fixed offset ---
        self._mag_offset_deg = rng.uniform(-2.0, 2.0)  # degrees, constant per run

        # --- Odometry scale error (trucks only) ---
        self._odom_scale = 1.0 + rng.uniform(-0.01, 0.01)  # ±1%

        # --- GNSS slow-changing values ---
        self._gnss_sats = int(rng.integers(8, 13))
        self._gnss_hdop = float(rng.uniform(0.8, 1.5))
        self._gnss_cn0 = float(rng.uniform(38.0, 45.0))

        # --- Previous state for finite differences ---
        self._prev_vel = None   # initialised on first update
        self._prev_yaw = None

    # ------------------------------------------------------------------
    def update(self, vehicle) -> dict:
        """
        Advance sensors by one timestep and return sensor reading dict.

        vehicle : Vehicle — read-only access to truth state.
        """
        rng = self._rng
        fc = self._frame_count
        self._frame_count += 1

        # Current truth state
        v_vel = vehicle.velocity_enu
        r, p, y = vehicle.roll_rad, vehicle.pitch_rad, vehicle.yaw_rad

        # -------- IMU (20 Hz — every frame) --------
        # Random-walk biases
        self._acc_bias += rng.normal(0, 0.0005 * math.sqrt(DT), size=3)
        self._gyr_bias += rng.normal(0, 0.00005 * math.sqrt(DT), size=3)

        # ----- Linear acceleration in ENU -----
        if self._prev_vel is None:
            self._prev_vel = v_vel.copy()
        lin_acc_enu = (v_vel - self._prev_vel) / DT
        # Clamp to physical limit to suppress waypoint-transition spikes
        acc_mag = float(np.linalg.norm(lin_acc_enu))
        if acc_mag > MAX_LINEAR_ACC_MPS2:
            lin_acc_enu = lin_acc_enu * (MAX_LINEAR_ACC_MPS2 / acc_mag)
        self._prev_vel = v_vel.copy()

        # ----- Rotate ENU linear accel into body frame -----
        # Simple yaw-only rotation (roll/pitch are small for a drone)
        cy, sy = math.cos(y), math.sin(y)
        lin_acc_body_x =  lin_acc_enu[0] * cy + lin_acc_enu[1] * sy
        lin_acc_body_y = -lin_acc_enu[0] * sy + lin_acc_enu[1] * cy
        lin_acc_body_z =  lin_acc_enu[2]

        # ----- Gravity component in body frame -----
        # Small-angle approximation: gravity appears as pitch and roll offsets
        # az ≈ +g at rest (body z points up for level flight)
        grav_body_x = -math.sin(p) * GRAVITY          # pitch tilts body x
        grav_body_y =  math.sin(r) * GRAVITY           # roll tilts body y
        grav_body_z =  math.cos(r) * math.cos(p) * GRAVITY  # az ≈ g at rest

        true_ax = lin_acc_body_x + grav_body_x
        true_ay = lin_acc_body_y + grav_body_y
        true_az = lin_acc_body_z + grav_body_z

        # Add bias + white noise
        ax = true_ax + self._acc_bias[0] + rng.normal(0, 0.02)
        ay = true_ay + self._acc_bias[1] + rng.normal(0, 0.02)
        az = true_az + self._acc_bias[2] + rng.normal(0, 0.02)

        # ----- Angular rates in body frame -----
        if self._prev_yaw is None:
            self._prev_yaw = y
        yaw_rate = _wrap_pi(y - self._prev_yaw) / DT
        # Clamp yaw rate to physical limit (max turn_rate in scenarios = 1.2 rad/s)
        yaw_rate = float(np.clip(yaw_rate, -2.0, 2.0))
        self._prev_yaw = y

        # For a drone in near-level flight, gz dominates; gx, gy are small
        pitch_rate = 0.0
        roll_rate  = 0.0

        gx = roll_rate  + self._gyr_bias[0] + rng.normal(0, 0.002)
        gy = pitch_rate + self._gyr_bias[1] + rng.normal(0, 0.002)
        gz = yaw_rate   + self._gyr_bias[2] + rng.normal(0, 0.002)

        imu = {
            "ax": round(float(ax), 4),
            "ay": round(float(ay), 4),
            "az": round(float(az), 4),
            "gx": round(float(gx), 4),
            "gy": round(float(gy), 4),
            "gz": round(float(gz), 4),
        }

        # -------- Barometer (20 Hz) --------
        pos = vehicle.position_enu
        true_alt_asl = pos[2] + 412.0  # ORIGIN_ALT
        self._baro_drift += self._baro_drift_rate
        true_pressure = _alt_to_pressure(true_alt_asl)
        baro_pressure = true_pressure + self._baro_drift + rng.normal(0, 0.08)
        baro = {"pressure_hpa": round(float(baro_pressure), 3)}

        # -------- Magnetometer (20 Hz) --------
        # Compass bearing: North-referenced, clockwise (0 = North, 90 = East).
        # Vehicle yaw (ENU): 0 = East, increasing CCW.
        # Conversion: compass_heading = (90° - yaw_deg) mod 360°
        true_heading_deg = (90.0 - math.degrees(y)) % 360.0
        noisy_heading = true_heading_deg + self._mag_offset_deg + rng.normal(0, 1.5)
        noisy_heading = noisy_heading % 360.0
        mag = {"heading_deg": round(float(noisy_heading), 2)}

        # -------- GNSS (5 Hz — every 4th frame) --------
        gnss = None
        if fc % GNSS_RATIO == 0:
            lat_true, lon_true, alt_true = enu_to_geodetic(pos[0], pos[1], pos[2])
            noisy_lat = lat_true + rng.normal(0, 1.5 / 111_320)  # 1.5 m in degrees
            noisy_lon = lon_true + rng.normal(
                0, 1.5 / (111_320 * math.cos(math.radians(lat_true)))
            )
            noisy_alt = alt_true + rng.normal(0, 3.0)

            # Slowly jitter GNSS quality values
            self._gnss_sats = int(np.clip(
                self._gnss_sats + rng.integers(-1, 2), 8, 12
            ))
            self._gnss_hdop = float(np.clip(
                self._gnss_hdop + rng.normal(0, 0.03), 0.8, 1.5
            ))
            self._gnss_cn0 = float(np.clip(
                self._gnss_cn0 + rng.normal(0, 0.3), 38.0, 45.0
            ))

            gnss = {
                "lat": round(float(noisy_lat), 6),
                "lon": round(float(noisy_lon), 6),
                "alt": round(float(noisy_alt), 1),
                "fix": 3,
                "sats": self._gnss_sats,
                "hdop": round(self._gnss_hdop, 2),
                "cn0_mean": round(self._gnss_cn0, 1),
            }

        # -------- Odometry (trucks only) --------
        odom = None
        if self._vehicle_type == "truck":
            true_speed = vehicle.speed_mps
            wheel_speed = true_speed * self._odom_scale + rng.normal(0, 0.05)
            odom = {"wheel_speed_mps": round(float(wheel_speed), 3)}

        return {
            "gnss": gnss,
            "imu": imu,
            "baro": baro,
            "mag": mag,
            "odom": odom,
        }


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi
