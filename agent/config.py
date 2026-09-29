"""
Configuration management for AlohaMini Edge Agent.
"""

from __future__ import annotations

import os
import uuid
import socket
from dataclasses import dataclass, field
from pathlib import Path


VALID_ROBOT_MODELS = {"alohamini1", "alohamini2", "alohamini2pro"}
VALID_CAMERA_VIEWS = {"top", "wrist"}


@dataclass
class AgentConfig:
    """Agent runtime configuration."""

    # Identity
    device_id: str = field(
        default_factory=lambda: os.getenv("DEVICE_ID", f"alohamini-{socket.gethostname()}")
    )
    robot_model: str = field(
        default_factory=lambda: os.getenv("ROBOT_MODEL", "alohamini2")
    )
    tenant_id: str = field(
        default_factory=lambda: os.getenv("TENANT_ID", "tenant-default")
    )
    device_secret: str = field(
        default_factory=lambda: os.getenv("DEVICE_SECRET", "dev-edge-secret")
    )

    # Cloud & Gateway Endpoints
    cloud_api_base: str = field(
        default_factory=lambda: os.getenv("CLOUD_API_BASE", "http://127.0.0.1:8080/api/v1")
    )
    mqtt_broker_url: str = field(
        default_factory=lambda: os.getenv("MQTT_BROKER_URL", "mqtt://127.0.0.1:1883")
    )
    go2rtc_api_url: str = field(
        default_factory=lambda: os.getenv("GO2RTC_API_URL", "http://127.0.0.1:1984")
    )
    # Robot Host (Raspberry Pi) Endpoints
    robot_host_ip: str = field(
        default_factory=lambda: os.getenv("ROBOT_HOST_IP", "127.0.0.1")
    )
    port_zmq_cmd: int = field(
        default_factory=lambda: int(os.getenv("PORT_ZMQ_CMD", "5555"))
    )
    port_zmq_obs: int = field(
        default_factory=lambda: int(os.getenv("PORT_ZMQ_OBS", "5556"))
    )
    port_zmq_camera_stream: int = field(
        default_factory=lambda: int(os.getenv("PORT_ZMQ_CAMERA_STREAM", "5557"))
    )
    camera_http_port: int = field(
        default_factory=lambda: int(os.getenv("CAMERA_HTTP_PORT", "8088"))
    )

    @property
    def zmq_cmd_url(self) -> str:
        return f"tcp://{self.robot_host_ip}:{self.port_zmq_cmd}"

    @property
    def zmq_camera_url(self) -> str:
        return f"tcp://{self.robot_host_ip}:{self.port_zmq_camera_stream}"

    # Teleoperation & Control Safety
    watchdog_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("WATCHDOG_TIMEOUT_S", "0.15"))  # 150ms dead-man switch
    )
    control_fps: int = field(
        default_factory=lambda: int(os.getenv("CONTROL_FPS", "50"))
    )
    teleop_channel_name: str = "robot_teleop"

    # Storage & Datasets
    dataset_root_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATASET_ROOT_DIR", "/tmp/alohamini_datasets"))
    )

    def __post_init__(self):
        if self.robot_model not in VALID_ROBOT_MODELS:
            raise ValueError(
                f"Invalid robot_model '{self.robot_model}'. Must be one of {sorted(VALID_ROBOT_MODELS)}"
            )
        self.dataset_root_dir = Path(self.dataset_root_dir)
        self.dataset_root_dir.mkdir(parents=True, exist_ok=True)

    def resolve_robot_lan_ip_from_cloud(
        self, station_id: str, jwt_token: str | None = None, timeout: float = 3.0
    ) -> str | None:
        """从公网实训平台查询当前工位绑定的机器人局域网 IP，并自动更新本地连接目标。"""
        import json
        import urllib.request

        url = f"{self.cloud_api_base}/workstations/{station_id}/assigned-device"
        headers = {"User-Agent": "AlohaMini-Edge-Agent"}
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                lan_ip = data.get("lan_ip")
                if lan_ip:
                    self.robot_host_ip = lan_ip
                    return lan_ip
        except Exception:
            pass
        return None
