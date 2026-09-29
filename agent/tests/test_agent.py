"""
Unit tests for AlohaMini Edge Agent.
Validates watchdog, teleop input safety, 150ms timeout, ICE filtering, and recording commands.
"""

import sys
import time
import tempfile
from pathlib import Path

# Add lerobot_alohamini to sys.path
root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

try:
    import numpy as np
except ImportError:
    np = None

from agent.config import AgentConfig
from agent.teleop import Watchdog, TeleopInputReceiver, SoftwareLimits, RateLimiter
from agent.streamer import VideoBridge, is_private_ip
from agent.recorder import RecordingSession


def test_agent_config():
    """Verify configuration validation and defaults."""
    cfg = AgentConfig(robot_model="alohamini2")
    assert cfg.robot_model == "alohamini2"
    assert cfg.watchdog_timeout_s == 0.15
    assert cfg.control_fps == 50

    try:
        AgentConfig(robot_model="invalid_robot_3000")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Invalid robot_model" in str(e)


def test_watchdog_pet_and_expiration():
    """Verify dead-man switch watchdog pets and expires accurately."""
    wd = Watchdog(timeout_s=0.1)
    assert wd.is_expired() is True  # initially expired / inactive

    wd.pet()
    assert wd.is_expired() is False
    assert wd.elapsed_s() < 0.05

    # Wait past 100ms timeout
    time.sleep(0.12)
    assert wd.is_expired() is True

    # Petting restores life
    wd.pet()
    assert wd.is_expired() is False

    # Trip forces expiration
    wd.trip()
    assert wd.is_expired() is True


def test_teleop_input_receiver_normal():
    """Verify normal message processing and key parsing."""
    receiver = TeleopInputReceiver(watchdog_timeout_s=0.15)
    payload = {
        "type": "teleop_input",
        "seq": 1,
        "keys": ["w", "u"],
        "vx": 0.25,
        "vy": 0.0,
        "vyaw": 0.0,
        "lift_dir": 1,
    }
    assert receiver.handle_message(payload) is True
    assert receiver.get_active_keys() == {"w", "u"}
    assert receiver.get_base_velocities() == (0.25, 0.0, 0.0)
    assert receiver.get_lift_direction() == 1

    # Action format for LeRobot
    arr = receiver.get_keyboard_numpy_action()
    if np is not None:
        assert isinstance(arr, np.ndarray)
    assert set(arr) == {"w", "u"}


def test_teleop_watchdog_timeout_braking():
    """
    CRITICAL SAFETY TEST:
    Verify that if no message arrives within 150ms, all keys and velocities brake to zero.
    """
    receiver = TeleopInputReceiver(watchdog_timeout_s=0.10)
    payload = {
        "type": "teleop_input",
        "seq": 10,
        "keys": ["w"],
        "vx": 0.4,
        "vyaw": 30.0,
        "lift_dir": 1,
    }
    receiver.handle_message(payload)
    assert receiver.get_active_keys() == {"w"}
    assert receiver.get_base_velocities() == (0.4, 0.0, 30.0)
    assert receiver.get_lift_direction() == 1

    # Wait past watchdog timeout
    time.sleep(0.12)

    # Everything MUST be zeroed out
    assert receiver.get_active_keys() == set()
    assert receiver.get_base_velocities() == (0.0, 0.0, 0.0)
    assert receiver.get_lift_direction() == 0
    assert len(receiver.get_keyboard_numpy_action()) == 0


def test_teleop_estop():
    """Verify Emergency Stop trips immediately."""
    receiver = TeleopInputReceiver(watchdog_timeout_s=0.5)
    receiver.handle_message({"type": "teleop_input", "keys": ["w"], "vx": 0.3})
    assert receiver.get_active_keys() == {"w"}

    # Trigger E-Stop
    receiver.trigger_estop()
    assert receiver.is_estopped() is True
    assert receiver.get_active_keys() == set()
    assert receiver.get_base_velocities() == (0.0, 0.0, 0.0)

    # Further messages are ignored while estopped
    receiver.handle_message({"type": "teleop_input", "keys": ["w"], "vx": 0.3})
    assert receiver.get_active_keys() == set()

    # Reset allows resumption
    receiver.reset_estop()
    assert receiver.is_estopped() is False
    receiver.handle_message({"type": "teleop_input", "keys": ["s"], "vx": -0.2})
    assert receiver.get_active_keys() == {"s"}


def test_ice_candidate_filtering_adr023():
    """Verify ADR-023: accept only private host candidates, reject srflx/relay/public."""
    cfg = AgentConfig()
    bridge = VideoBridge(cfg)

    # Valid LAN host candidates
    assert is_private_ip("192.168.1.150") is True
    assert is_private_ip("10.0.0.5") is True
    assert is_private_ip("172.20.10.2") is True
    assert bridge.validate_ice_candidate("candidate:1 1 udp 2122260223 192.168.1.150 54321 typ host") is True
    assert bridge.validate_ice_candidate("candidate:2 1 udp 2122260223 10.0.0.5 54321 typ host") is True

    # Invalid non-host candidates (srflx, relay)
    assert bridge.validate_ice_candidate("candidate:3 1 udp 1686052863 203.0.113.1 54321 typ srflx raddr 192.168.1.150 rport 54321") is False
    assert bridge.validate_ice_candidate("candidate:4 1 udp 41885439 203.0.113.2 54321 typ relay") is False

    # Invalid public host candidate
    assert is_private_ip("8.8.8.8") is False
    assert bridge.validate_ice_candidate("candidate:5 1 udp 2122260223 8.8.8.8 54321 typ host") is False


def test_recording_command_push_to_hub_flag():
    """Verify that build_record_command enforces --dataset.push_to_hub=false."""
    cfg = AgentConfig()
    session = RecordingSession(cfg)
    session.start_recording(experiment_id="exp-test-123", target_episodes=50)

    cmd = session.build_record_command()
    cmd_str = " ".join(cmd)
    assert "--dataset.push_to_hub=false" in cmd_str
    assert "--dataset.num_episodes=50" in cmd_str
    assert "exp-test-123" in cmd_str


def test_robot_host_ip_config_and_urls():
    """Verify Raspberry Pi Host IP configuration and derived ZMQ URLs."""
    cfg = AgentConfig(robot_host_ip="192.168.1.100", port_zmq_cmd=5555, port_zmq_camera_stream=5557)
    assert cfg.robot_host_ip == "192.168.1.100"
    assert cfg.zmq_cmd_url == "tcp://192.168.1.100:5555"
    assert cfg.zmq_camera_url == "tcp://192.168.1.100:5557"


def test_zmq_action_payload_building():
    """Verify teleop receiver builds correct ZMQ action payloads for Pi Host."""
    receiver = TeleopInputReceiver(watchdog_timeout_s=0.15)
    receiver.handle_message({
        "type": "teleop_input",
        "keys": ["w", "d", "u"],
        "vx": 0.25,
        "vy": -0.25,
        "vyaw": 30.0,
        "lift_dir": 1,
    })

    payload = receiver.build_zmq_action_payload()
    assert payload["x.vel"] == 0.25
    assert payload["y.vel"] == -0.25
    assert payload["theta.vel"] == 30.0
    assert payload["lift_axis.vel"] == 1000.0

    # Wait past watchdog timeout: payload must zero out
    time.sleep(0.18)
    zero_payload = receiver.build_zmq_action_payload()
    assert zero_payload["x.vel"] == 0.0
    assert zero_payload["y.vel"] == 0.0
    assert zero_payload["theta.vel"] == 0.0
    assert zero_payload["lift_axis.vel"] == 0.0


def test_software_limits_and_safety_guard():
    """Verify kinematic velocity clamping and lift software limit protection."""
    from agent.teleop import SoftwareLimits
    limits = SoftwareLimits(max_linear_vel=0.35, max_angular_vel=50.0, min_lift_height_mm=0.0, max_lift_height_mm=300.0)

    # Velocity clamping
    vx, vy, vyaw = limits.clamp_base_vel(1.5, -0.9, 120.0)
    assert vx == 0.35
    assert vy == -0.35
    assert vyaw == 50.0

    # Lift bounds check: cannot move below 0
    limits.current_lift_height_mm = 0.0
    clamped_dir = limits.update_and_clamp_lift(-1)
    assert clamped_dir == 0  # Blocked from crashing into table
    assert limits.limit_hit is True
    assert "Z轴软限位触发" in limits.limit_message

    # Upward is allowed
    clamped_dir = limits.update_and_clamp_lift(1)
    assert clamped_dir == 1


def test_rate_limiter_acceleration_smoothing():
    """Verify rate limiter prevents instantaneous velocity jumps."""
    from agent.teleop import RateLimiter
    limiter = RateLimiter(max_accel=1.0)
    # Filter step from 0 to 1.0 m/s
    v1, _, _ = limiter.filter(1.0, 0.0, 0.0)
    # In tiny dt, v1 must be smoothed, not jump to 1.0 immediately
    assert 0.0 <= v1 < 1.0


def test_gamepad_analog_input_processing():
    """Verify gamepad analog inputs with deadzone filtering."""
    receiver = TeleopInputReceiver(watchdog_timeout_s=0.2)
    # Tiny stick drift within deadzone (< 0.08)
    receiver.handle_message({
        "type": "teleop_input",
        "is_gamepad": True,
        "analog_vx": 0.05,
        "analog_vy": -0.04,
        "analog_vyaw": 0.0,
        "gripper_pos": 0.8,
    })
    vx, vy, _ = receiver.get_base_velocities()
    assert vx == 0.0
    assert vy == 0.0
    assert receiver.is_gamepad_mode() is True
    assert receiver.get_gripper_position() == 0.8

    # Intentional stick motion
    receiver.handle_message({
        "type": "teleop_input",
        "is_gamepad": True,
        "analog_vx": 0.30,
        "analog_vy": 0.20,
        "gripper_pos": 0.2,
    })
    assert receiver.get_gripper_position() == 0.2


def test_recording_keep_and_discard_retake():
    """Verify dataset episode keep and discard/retake functionality."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cfg = AgentConfig()
        session = RecordingSession(cfg)
        session.output_dir = tmp_path

        # Start and finish episode 1
        session.keep_episode(episode_index=1, tags=["perfect_grasp"], quality_score=5)
        assert session.current_episode == 2

        # Fake create an episode 2 file
        ep2_file = tmp_path / "episode_000002.parquet"
        ep2_file.write_text("data")
        assert ep2_file.exists()

        # Discard episode 2 (retake scenario)
        session.discard_current_episode(episode_index=2, reason="gripper_slipped")
        # File must be cleaned up, and current_episode remains 2 so student retakes ep 2
        assert not ep2_file.exists()
        assert session.current_episode == 2
        assert session.get_status()["manifest_entries"] == 2


if __name__ == "__main__":
    tests = [
        test_agent_config,
        test_watchdog_pet_and_expiration,
        test_teleop_input_receiver_normal,
        test_teleop_watchdog_timeout_braking,
        test_teleop_estop,
        test_ice_candidate_filtering_adr023,
        test_recording_command_push_to_hub_flag,
        test_robot_host_ip_config_and_urls,
        test_zmq_action_payload_building,
        test_software_limits_and_safety_guard,
        test_rate_limiter_acceleration_smoothing,
        test_gamepad_analog_input_processing,
        test_recording_keep_and_discard_retake,
    ]
    print(f"\nRunning {len(tests)} test cases in test_agent.py...")
    for t in tests:
        t()
        print(f"  ✓ {t.__name__} passed")
    print(f"All {len(tests)} tests passed successfully!\n")



