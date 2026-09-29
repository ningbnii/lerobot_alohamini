import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.binding import (
    StationBinding,
    load_station_binding,
    resolve_effective_robot_ip,
    save_station_binding,
)
from agent.discovery import (
    is_raspberry_pi_mac,
    normalize_mac,
    resolve_mac_to_current_ip,
)
from agent.identify import wiggle_robot


class TestDiscoveryAndBinding(unittest.TestCase):
    def test_normalize_mac(self):
        self.assertEqual(normalize_mac("D8:3A:DD:11:22:33"), "d8:3a:dd:11:22:33")
        self.assertEqual(normalize_mac("d8-3a-dd-11-22-33"), "d8:3a:dd:11:22:33")
        self.assertEqual(normalize_mac("d83add112233"), "d8:3a:dd:11:22:33")

    def test_is_raspberry_pi_mac(self):
        self.assertTrue(is_raspberry_pi_mac("d8:3a:dd:01:02:03"))
        self.assertTrue(is_raspberry_pi_mac("b8:27:eb:ff:ee:dd"))
        self.assertTrue(is_raspberry_pi_mac("dc:a6:32:12:34:56"))
        self.assertFalse(is_raspberry_pi_mac("00:50:56:c0:00:08"))  # VMware
        self.assertFalse(is_raspberry_pi_mac("ac:de:48:00:11:22"))  # Apple

    def test_station_binding_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "binding.json"

            binding = StationBinding(
                station_id="station-01",
                target_mac="D8-3A-DD-11-22-33",
                last_known_ip="192.168.1.105",
            )
            saved = save_station_binding(binding, file_path)
            self.assertTrue(saved)
            self.assertTrue(file_path.exists())

            loaded = load_station_binding(file_path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.station_id, "station-01")
            self.assertEqual(loaded.target_mac, "d8:3a:dd:11:22:33")
            self.assertEqual(loaded.last_known_ip, "192.168.1.105")

    def test_resolve_mac_with_mock_arp(self):
        mock_arp = {
            "192.168.1.101": "d8:3a:dd:11:22:33",
            "192.168.1.102": "00:11:22:33:44:55",
        }
        with patch("agent.discovery.read_system_arp_table", return_value=mock_arp):
            with patch("agent.discovery.check_host_port", return_value=True):
                ip = resolve_mac_to_current_ip("d8:3a:dd:11:22:33")
                self.assertEqual(ip, "192.168.1.101")

                # Unknown MAC
                unknown_ip = resolve_mac_to_current_ip("aa:bb:cc:dd:ee:ff")
                self.assertIsNone(unknown_ip)

    def test_resolve_effective_robot_ip_auto_update(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "binding.json"
            binding = StationBinding(
                station_id="station-01",
                target_mac="d8:3a:dd:11:22:33",
                last_known_ip="192.168.1.101",
            )
            save_station_binding(binding, file_path)

            # 模拟 DHCP 变动：IP 漂移到了 192.168.1.188
            mock_arp = {"192.168.1.188": "d8:3a:dd:11:22:33"}
            with patch("agent.discovery.read_system_arp_table", return_value=mock_arp):
                with patch("agent.discovery.check_host_port", return_value=True):
                    effective_ip = resolve_effective_robot_ip(binding, filepath=file_path)
                    self.assertEqual(effective_ip, "192.168.1.188")
                    self.assertEqual(binding.last_known_ip, "192.168.1.188")

                    # 验证本地文件被自动更新持久化
                    reloaded = load_station_binding(file_path)
                    self.assertEqual(reloaded.last_known_ip, "192.168.1.188")

    def test_wiggle_robot_graceful_on_closed_port(self):
        # 针对一个不可达的本地端口，应安全返回 False 而非崩溃
        res = wiggle_robot("127.0.0.1", port=59999, timeout_s=0.1)
        self.assertFalse(res)


if __name__ == "__main__":
    unittest.main()
