"""
Hybrid teleoperation input receiver with 150ms dead-man switch watchdog.
Processes WebRTC DataChannel payloads directly over LAN with <3ms latency.
"""

from __future__ import annotations

import json
import time
import logging
import threading
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger(__name__)


class Watchdog:
    """Thread-safe dead-man switch watchdog."""

    def __init__(self, timeout_s: float = 0.15) -> None:
        self.timeout_s = timeout_s
        self._last_pet_time: float = 0.0
        self._lock = threading.Lock()
        self._is_active = False

    def pet(self) -> None:
        """Feed the watchdog."""
        with self._lock:
            self._last_pet_time = time.monotonic()
            self._is_active = True

    def is_expired(self) -> bool:
        """Check if watchdog has expired due to lack of recent pet (>timeout_s)."""
        with self._lock:
            if not self._is_active:
                return True
            elapsed = time.monotonic() - self._last_pet_time
            return elapsed > self.timeout_s

    def elapsed_s(self) -> float:
        """Seconds since last pet."""
        with self._lock:
            if not self._is_active:
                return float("inf")
            return time.monotonic() - self._last_pet_time

    def trip(self) -> None:
        """Explicitly trip the watchdog (e.g. on emergency stop)."""
        with self._lock:
            self._is_active = False
            self._last_pet_time = 0.0


class RateLimiter:
    """Limits acceleration / jerk to prevent mechanical shock and gear wear on AlohaMini."""

    def __init__(self, max_accel: float = 1.5, max_accel_yaw: float = 180.0) -> None:
        self.max_accel = max_accel
        self.max_accel_yaw = max_accel_yaw
        self._last_vx = 0.0
        self._last_vy = 0.0
        self._last_vyaw = 0.0
        self._last_time = time.monotonic()

    def filter(self, target_vx: float, target_vy: float, target_vyaw: float) -> tuple[float, float, float]:
        now = time.monotonic()
        dt = max(0.001, min(0.1, now - self._last_time))
        self._last_time = now

        max_dv = self.max_accel * dt
        max_dyaw = self.max_accel_yaw * dt

        def step(current: float, target: float, max_delta: float) -> float:
            if target > current:
                return min(target, current + max_delta)
            else:
                return max(target, current - max_delta)

        self._last_vx = step(self._last_vx, target_vx, max_dv)
        self._last_vy = step(self._last_vy, target_vy, max_dv)
        self._last_vyaw = step(self._last_vyaw, target_vyaw, max_dyaw)
        return (self._last_vx, self._last_vy, self._last_vyaw)

    def reset(self) -> None:
        self._last_vx = 0.0
        self._last_vy = 0.0
        self._last_vyaw = 0.0
        self._last_time = time.monotonic()


class SoftwareLimits:
    """Kinematic software limits enforcing safety boundaries on AlohaMini."""

    def __init__(
        self,
        max_linear_vel: float = 0.4,
        max_angular_vel: float = 60.0,
        min_lift_height_mm: float = 0.0,
        max_lift_height_mm: float = 300.0,
    ) -> None:
        self.max_linear_vel = max_linear_vel
        self.max_angular_vel = max_angular_vel
        self.min_lift_height_mm = min_lift_height_mm
        self.max_lift_height_mm = max_lift_height_mm
        self.current_lift_height_mm = 50.0  # Safe initial height above table
        self.limit_hit: bool = False
        self.limit_message: str = ""

    def clamp_base_vel(self, vx: float, vy: float, vyaw: float) -> tuple[float, float, float]:
        clamped_vx = max(-self.max_linear_vel, min(self.max_linear_vel, vx))
        clamped_vy = max(-self.max_linear_vel, min(self.max_linear_vel, vy))
        clamped_vyaw = max(-self.max_angular_vel, min(self.max_angular_vel, vyaw))
        return (clamped_vx, clamped_vy, clamped_vyaw)

    def update_and_clamp_lift(self, lift_dir: int, dt: float = 0.02) -> int:
        """
        Check if lift is attempting to move past mechanical software bounds.
        lift_dir: +1 (up), -1 (down), 0 (stop).
        Returns valid clamped lift_dir.
        """
        if lift_dir < 0 and self.current_lift_height_mm <= self.min_lift_height_mm:
            self.limit_hit = True
            self.limit_message = f"Z轴软限位触发: 当前高度 {self.current_lift_height_mm:.1f}mm 已达桌面安全下限，阻止继续下降"
            return 0
        if lift_dir > 0 and self.current_lift_height_mm >= self.max_lift_height_mm:
            self.limit_hit = True
            self.limit_message = f"立柱软限位触发: 当前高度 {self.current_lift_height_mm:.1f}mm 已达行程上限"
            return 0

        self.limit_hit = False
        self.limit_message = ""
        # Simulate tracking height change (lift moves ~50mm/s)
        self.current_lift_height_mm += float(lift_dir) * 50.0 * dt
        self.current_lift_height_mm = max(
            self.min_lift_height_mm,
            min(self.max_lift_height_mm, self.current_lift_height_mm),
        )
        return lift_dir


class TeleopInputReceiver:
    """
    Receives and sanitizes remote teleoperation inputs.
    Bridges WebRTC DataChannel frames into LeRobot robot actions.
    Now with software limits, rate limiting (smoothing), and gamepad analog support.
    """

    def __init__(
        self,
        watchdog_timeout_s: float = 0.15,
        enable_rate_limiter: bool = False,
    ) -> None:
        self.watchdog = Watchdog(timeout_s=watchdog_timeout_s)
        self.limits = SoftwareLimits()
        self.enable_rate_limiter = enable_rate_limiter
        self.rate_limiter = RateLimiter()
        self.on_reply: Any = None
        self.calibration_session: Any = None
        self._lock = threading.Lock()
        self._pressed_keys: set[str] = set()
        self._vx: float = 0.0
        self._vy: float = 0.0
        self._vyaw: float = 0.0
        self._lift_dir: int = 0  # +1: up, -1: down, 0: stop
        self._gripper_pos: float = 1.0  # 1.0: fully open, 0.0: fully closed
        self._is_gamepad: bool = False
        self._estop: bool = False
        self._last_seq: int = -1

    def set_status_callback(self, cb: Any) -> None:
        self.on_reply = cb

    def _on_calib_status(self, status: dict[str, Any]) -> None:
        """Callback triggered when calibration session updates state or joint positions."""
        if self.on_reply:
            try:
                self.on_reply(status)
            except Exception as e:
                logger.debug("Failed delivering calib status to on_reply: %s", e)

    def handle_message(self, raw_message: str | bytes | dict[str, Any]) -> bool:
        """
        Handle an incoming WebRTC DataChannel message.
        Expected format:
        {
            "type": "teleop_input",
            "seq": 1024,
            "keys": ["w", "a"],
            "vx": 0.25,
            "vy": 0.0,
            "vyaw": 0.0,
            "lift_dir": 1,
            "estop": false,
            "timestamp_ms": 1727160000123
        }
        """
        try:
            if isinstance(raw_message, (str, bytes)):
                data = json.loads(raw_message)
            else:
                data = raw_message

            msg_type = data.get("type", "teleop_input")

            # Handle Emergency Stop immediately
            if data.get("estop", False) or msg_type == "estop":
                self.trigger_estop()
                return True

            # Handle Wiggle to Identify pulse (DataChannel P2P direct path)
            if msg_type == "wiggle_identify":
                target_ip = data.get("target_ip") or "127.0.0.1"
                port = int(data.get("port", 5555))
                logger.info("Received Wiggle Identify command via DataChannel for target %s:%d", target_ip, port)
                try:
                    from agent.identify import wiggle_robot
                    wiggle_robot(target_ip, port=port)
                except Exception as e:
                    logger.warning("Error triggering wiggle_robot from DataChannel: %s", e)
            # Handle Calibration Session messages
            if msg_type.startswith("calib_"):
                if self.calibration_session is None:
                    from agent.calibration import CalibrationSession
                    self.calibration_session = CalibrationSession(on_status_change=self._on_calib_status)

                if msg_type == "calib_start":
                    robot_model = data.get("robot_model", "alohamini2")
                    no_follower = bool(data.get("no_follower", False))
                    res = self.calibration_session.start(robot_model=robot_model, no_follower=no_follower)
                    return bool(res.get("success", False))

                if msg_type == "calib_confirm_middle":
                    arm = data.get("arm", "left")
                    res = self.calibration_session.confirm_middle(arm=arm)
                    return bool(res.get("success", False))

                if msg_type == "calib_finish_rom":
                    arm = data.get("arm", "left")
                    res = self.calibration_session.finish_rom(arm=arm)
                    return bool(res.get("success", False))

                if msg_type == "calib_save":
                    cloud_api_base = data.get("cloud_api_base", "")
                    device_id = data.get("device_id", "")
                    res = self.calibration_session.save_and_apply(cloud_api_base=cloud_api_base, device_id=device_id)
                    return bool(res.get("success", False))

                if msg_type == "calib_cancel":
                    res = self.calibration_session.cancel()
                    return bool(res.get("success", False))

                if msg_type == "calib_reset":
                    cloud_api_base = data.get("cloud_api_base", "")
                    device_id = data.get("device_id", "")
                    res = self.calibration_session.reset_calibration(cloud_api_base=cloud_api_base, device_id=device_id)
                    return bool(res.get("success", False))

                if msg_type == "calib_export_report":
                    device_id = data.get("device_id", "AlohaMini")
                    report = self.calibration_session.generate_calibration_report(device_id=device_id)
                    self._on_calib_status(report)
                    return True

                if msg_type == "calib_get_status":
                    status = self.calibration_session.get_status()
                    self._on_calib_status(status)
                    return True

            if msg_type == "teleop_input":
                seq = data.get("seq", 0)
                # Ignore stale/out-of-order packets if seq is present
                if seq > 0 and seq < self._last_seq:
                    return False
                self._last_seq = seq

                keys = set(data.get("keys", []))
                is_gamepad = bool(data.get("is_gamepad", False))

                # Extract base velocity values (either keyboard-derived or gamepad analog)
                if is_gamepad and "analog_vx" in data:
                    # Continuous analog stick input
                    raw_vx = float(data.get("analog_vx", 0.0))
                    raw_vy = float(data.get("analog_vy", 0.0))
                    raw_vyaw = float(data.get("analog_vyaw", 0.0))
                    # Apply deadzone for stick drift
                    deadzone = 0.08
                    vx = 0.0 if abs(raw_vx) < deadzone else raw_vx
                    vy = 0.0 if abs(raw_vy) < deadzone else raw_vy
                    vyaw = 0.0 if abs(raw_vyaw) < deadzone else raw_vyaw
                else:
                    vx = float(data.get("vx", 0.0))
                    vy = float(data.get("vy", 0.0))
                    vyaw = float(data.get("vyaw", 0.0))

                lift_dir = int(data.get("lift_dir", 0))
                gripper_pos = float(data.get("gripper_pos", 1.0))
                gripper_pos = max(0.0, min(1.0, gripper_pos))

                # Apply kinematic software limits (workspace boundaries & velocity clamping)
                vx, vy, vyaw = self.limits.clamp_base_vel(vx, vy, vyaw)
                lift_dir = self.limits.update_and_clamp_lift(lift_dir)

                with self._lock:
                    if not self._estop:
                        self._pressed_keys = keys
                        self._is_gamepad = is_gamepad
                        self._vx = vx
                        self._vy = vy
                        self._vyaw = vyaw
                        self._lift_dir = lift_dir
                        self._gripper_pos = gripper_pos
                        self.watchdog.pet()
                return True

            return False
        except Exception as e:
            logger.warning("Failed to parse teleop message: %s", e)
            return False

    def trigger_estop(self) -> None:
        """Trigger Emergency Stop: zero out everything immediately."""
        with self._lock:
            self._estop = True
            self._pressed_keys.clear()
            self._vx = 0.0
            self._vy = 0.0
            self._vyaw = 0.0
            self._lift_dir = 0
            self.rate_limiter.reset()
            self.watchdog.trip()
        logger.critical("EMERGENCY STOP (E-STOP) TRIGGERED in TeleopInputReceiver")

    def reset_estop(self) -> None:
        """Reset Emergency Stop flag."""
        with self._lock:
            self._estop = False
            self.rate_limiter.reset()
            self.watchdog.trip()

    def is_estopped(self) -> bool:
        with self._lock:
            return self._estop

    def get_active_keys(self) -> set[str]:
        """
        Get currently pressed keys.
        Returns empty set if watchdog has expired (>150ms timeout) or under E-Stop.
        """
        with self._lock:
            if self._estop or self.watchdog.is_expired():
                return set()
            return set(self._pressed_keys)

    def get_base_velocities(self) -> tuple[float, float, float]:
        """
        Get (vx, vy, vyaw).
        Returns (0.0, 0.0, 0.0) if watchdog expired or under E-Stop.
        Smooths acceleration via RateLimiter if enabled.
        """
        with self._lock:
            if self._estop or self.watchdog.is_expired():
                self.rate_limiter.reset()
                return (0.0, 0.0, 0.0)
            target_vx, target_vy, target_vyaw = self._vx, self._vy, self._vyaw

        if self.enable_rate_limiter:
            return self.rate_limiter.filter(target_vx, target_vy, target_vyaw)
        return (target_vx, target_vy, target_vyaw)

    def get_lift_direction(self) -> int:
        """
        Get lift direction (+1: up, -1: down, 0: stop).
        Returns 0 if watchdog expired or under E-Stop.
        """
        with self._lock:
            if self._estop or self.watchdog.is_expired():
                return 0
            return self._lift_dir

    def get_gripper_position(self) -> float:
        """Get target gripper position [0.0 = closed, 1.0 = fully open]."""
        with self._lock:
            return self._gripper_pos

    def is_gamepad_mode(self) -> bool:
        """Returns True if the current control stream is driven by a Gamepad."""
        with self._lock:
            return self._is_gamepad

    def get_safety_status(self) -> dict[str, Any]:
        """Returns comprehensive safety and limit metrics for teleop telemetry."""
        with self._lock:
            return {
                "watchdog_active": not self.watchdog.is_expired(),
                "watchdog_elapsed_s": self.watchdog.elapsed_s(),
                "estopped": self._estop,
                "limit_hit": self.limits.limit_hit,
                "limit_message": self.limits.limit_message,
                "current_lift_height_mm": self.limits.current_lift_height_mm,
                "rate_limiter_enabled": self.enable_rate_limiter,
                "is_gamepad": self._is_gamepad,
            }

    def get_keyboard_numpy_action(self) -> Any:
        """
        Returns a numpy array of pressed key strings, matching LeRobot's
        `keyboard.get_action()` return type. If numpy is not installed, returns a list.
        """
        active_keys = self.get_active_keys()
        if np is not None:
            if not active_keys:
                return np.array([], dtype=str)
            return np.array(list(active_keys), dtype=str)
        return list(active_keys)

    def build_zmq_action_payload(self) -> dict[str, float]:
        """
        Build a payload dictionary formatted for AlohaMini Host's ZMQ PULL socket (:5555).
        Includes base velocities (x.vel, y.vel, theta.vel) and lift velocity (lift_axis.vel).
        Zeroed out when watchdog is expired or during E-Stop.
        """
        vx, vy, vyaw = self.get_base_velocities()
        lift_dir = self.get_lift_direction()
        lift_vel = 1000.0 * float(lift_dir)
        return {
            "x.vel": float(vx),
            "y.vel": float(vy),
            "theta.vel": float(vyaw),
            "lift_axis.vel": float(lift_vel),
            "gripper.pos": float(self.get_gripper_position()),
        }

