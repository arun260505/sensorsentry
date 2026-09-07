"""
publisher.py — Build and transmit sensor frames over UDP.

Responsibilities
----------------
1. Send run_start header once at the start of each run.
2. Build sensor frames that comply EXACTLY with docs/schema.md.
3. Send frames to UDP 127.0.0.1:5005 at 20 Hz.

HARD RULE: This file must never include truth state or attack metadata.
Any field not in the schema is a bug. The detector rejects unknown fields.
"""

import json
import socket
import time


UDP_HOST = "127.0.0.1"
UDP_PORT = 5005
FRAME_RATE_HZ = 20


class Publisher:
    """
    UDP frame publisher.

    Usage
    -----
    pub = Publisher(vehicle_id="DRONE-01", vehicle_type="drone",
                    run_id="r-...", seed=12345, t0=1234567890.0)
    pub.send_run_start()
    while simulation_running:
        sensors = sensor_suite.update(vehicle)
        vehicle.step()
        pub.send_frame(vehicle.t, seq, sensors)
        time.sleep(1/20)
    pub.close()
    """

    def __init__(
        self,
        vehicle_id: str,
        vehicle_type: str,
        run_id: str,
        seed: int,
        t0: float,
        host: str = UDP_HOST,
        port: int = UDP_PORT,
    ):
        self._vehicle_id = vehicle_id
        self._vehicle_type = vehicle_type
        self._run_id = run_id
        self._seed = seed
        self._t0 = t0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._addr = (host, port)

    # ------------------------------------------------------------------
    # Run-start header (schema §2)
    # ------------------------------------------------------------------
    def send_run_start(self):
        header = {
            "type": "run_start",
            "run_id": self._run_id,
            "vehicle_id": self._vehicle_id,
            "vehicle_type": self._vehicle_type,
            "seed": self._seed,
            "rate_hz": FRAME_RATE_HZ,
            "gnss_rate_hz": FRAME_RATE_HZ // 4,   # 5 Hz
            "t0": self._t0,
        }
        self._send(header)

    # ------------------------------------------------------------------
    # Sensor frame (schema §1)
    # ------------------------------------------------------------------
    def send_frame(self, t: float, seq: int, sensors: dict) -> dict:
        """
        Build and transmit a sensor frame.

        sensors : dict returned by SensorSuite.update()
                  keys: gnss, imu, baro, mag, odom

        Returns the frame dict (for caller to print/log if desired).
        """
        frame = {
            "vehicle_id": self._vehicle_id,
            "t": round(t, 3),
            "seq": seq,
            "gnss": sensors["gnss"],   # may be None — that is correct
            "imu": sensors["imu"],
            "baro": sensors["baro"],
            "mag": sensors["mag"],
            "odom": sensors["odom"],   # None for drones — that is correct
        }
        self._send(frame)
        return frame

    # ------------------------------------------------------------------
    def _send(self, obj: dict):
        payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self._sock.sendto(payload, self._addr)

    def close(self):
        self._sock.close()
