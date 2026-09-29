"""
Station-to-Robot MAC Binding Management.

Manages persistent binding of a physical workstation to a robot's hardware MAC address.
When the robot reboots and acquires a different DHCP IP, the binding automatically
resolves the new dynamic IP using the persistent MAC address.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .discovery import normalize_mac, resolve_mac_to_current_ip

logger = logging.getLogger("agent.binding")

DEFAULT_BINDING_PATH = Path.home() / ".config" / "alohalab" / "station_binding.json"


def get_default_binding_path() -> Path:
    return DEFAULT_BINDING_PATH


@dataclass
class StationBinding:
    station_id: str
    target_mac: str
    last_known_ip: str
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StationBinding":
        return cls(
            station_id=data["station_id"],
            target_mac=normalize_mac(data["target_mac"]),
            last_known_ip=data.get("last_known_ip", "127.0.0.1"),
            updated_at=data.get("updated_at", time.time()),
        )


def load_station_binding(filepath: Optional[Path] = None) -> Optional[StationBinding]:
    """Load persistent station-to-robot binding from local disk."""
    path = filepath or DEFAULT_BINDING_PATH
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return StationBinding.from_dict(data)
    except Exception as e:
        logger.warning("Failed to load station binding from %s: %s", path, e)
        return None


def save_station_binding(binding: StationBinding, filepath: Optional[Path] = None) -> bool:
    """Save station binding to local disk."""
    path = filepath or DEFAULT_BINDING_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(binding.to_dict(), f, indent=2)
        return True
    except Exception as e:
        logger.error("Failed to save station binding to %s: %s", path, e)
        return False


def resolve_effective_robot_ip(
    binding: StationBinding,
    filepath: Optional[Path] = None,
    port: int = 5555,
) -> str:
    """
    Resolve the current dynamic IP for the bound robot.
    If the IP has shifted due to DHCP reassignment, this automatically updates
    the binding file and returns the newly tracked IP.
    """
    current_ip = resolve_mac_to_current_ip(binding.target_mac, port=port)
    if current_ip:
        if current_ip != binding.last_known_ip:
            logger.info(
                "Detected IP shift for bound robot (%s): %s -> %s. Auto-updating binding.",
                binding.target_mac,
                binding.last_known_ip,
                current_ip,
            )
            binding.last_known_ip = current_ip
            binding.updated_at = time.time()
            save_station_binding(binding, filepath)
        return current_ip

    # If offline or cannot be resolved, fallback to last known IP
    logger.debug(
        "Could not detect fresh IP for %s; falling back to last known IP %s",
        binding.target_mac,
        binding.last_known_ip,
    )
    return binding.last_known_ip


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AlohaMini Station Binding Tool")
    parser.add_argument("--station-id", default="station_default", help="Station ID")
    parser.add_argument("--bind-mac", help="Bind to specific hardware MAC address")
    parser.add_argument("--ip", default="127.0.0.1", help="IP address associated with the MAC")
    parser.add_argument("--resolve", action="store_true", help="Resolve current IP via ARP table")
    args = parser.parse_args()

    if args.bind_mac:
        binding = StationBinding(
            station_id=args.station_id,
            target_mac=args.bind_mac,
            last_known_ip=args.ip,
        )
        save_station_binding(binding)
        print(f"✅ 已成功保存工位绑定配置到 {get_default_binding_path()}:")
        print(f"  Station ID: {binding.station_id}")
        print(f"  Bound MAC : {binding.target_mac}")
        print(f"  Initial IP: {binding.last_known_ip}")
    else:
        binding = load_station_binding()
        if not binding:
            print("ℹ️ 当前尚未绑定任何小车。可通过 --bind-mac 进行绑定。")
        else:
            print(f"📄 当前工位绑定信息 ({get_default_binding_path()}):")
            print(f"  Station ID   : {binding.station_id}")
            print(f"  Bound MAC    : {binding.target_mac}")
            print(f"  Last Known IP: {binding.last_known_ip}")
            if args.resolve:
                print("🔍 正在通过内核 ARP 表反查最新 IP...")
                effective_ip = resolve_effective_robot_ip(binding)
                print(f"  Effective IP : {effective_ip}")

