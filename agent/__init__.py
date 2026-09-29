"""
AlohaMini Agent - Edge Daemon for AlohaLab Physical Robots.
Bypass orchestrator for OSS LeRobot. Zero modifications to LeRobot source.
"""

from agent.config import AgentConfig

try:
    from agent.teleop import Watchdog, TeleopInputReceiver
    from agent.recorder import RecordingSession
    from agent.uploader import DatasetUploader
    from agent.streamer import VideoBridge
    from agent.calibration import CalibrationSession, CalibrationState
except ImportError:
    pass

from agent.discovery import normalize_mac, is_raspberry_pi_mac, read_system_arp_table, scan_local_subnet, resolve_mac_to_current_ip
from agent.binding import StationBinding, load_station_binding, save_station_binding, resolve_effective_robot_ip
from agent.identify import wiggle_robot, capture_preview_jpeg

__version__ = "0.1.0"
__all__ = [
    "AgentConfig",
    "normalize_mac",
    "is_raspberry_pi_mac",
    "read_system_arp_table",
    "scan_local_subnet",
    "resolve_mac_to_current_ip",
    "StationBinding",
    "load_station_binding",
    "save_station_binding",
    "resolve_effective_robot_ip",
    "wiggle_robot",
    "capture_preview_jpeg",
    "CalibrationSession",
    "CalibrationState",
]

