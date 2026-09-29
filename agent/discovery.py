"""
AlohaMini LAN Discovery and Hardware Fingerprinting.

Discovers AlohaMini robots on the local Wi-Fi subnet using:
1. System ARP table parsing (resolving IP <-> MAC address).
2. Raspberry Pi OUI (Organizationally Unique Identifier) filtering.
3. Multi-threaded TCP port 5555 probing (detecting active alohamini_host).
4. MAC-to-IP dynamic tracking (instant re-identification when IP shifts via DHCP).
"""

import ipaddress
import logging
import platform
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

logger = logging.getLogger("agent.discovery")

# Official IEEE OUI prefixes registered to Raspberry Pi Foundation & Trading Ltd
RASPBERRY_PI_OUIS = {
    "b8:27:eb",
    "dc:a6:32",
    "e4:5f:01",
    "28:cd:c1",
    "d8:3a:dd",
    "2c:cf:67",  # Raspberry Pi 5 official IEEE OUI (2024+)
}


def normalize_mac(mac_str: str) -> str:
    """Normalize MAC address string to standard lowercase 'xx:xx:xx:xx:xx:xx'."""
    raw = mac_str.strip().lower()
    if ":" in raw or "-" in raw:
        parts = re.split(r"[:-]", raw)
        if len(parts) == 6:
            return ":".join(p.zfill(2) for p in parts)
    clean = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(clean) == 12:
        return ":".join(clean[i : i + 2] for i in range(0, 12, 2))
    return raw


def is_raspberry_pi_mac(mac: str) -> bool:
    """Return True if MAC address prefix belongs to Raspberry Pi."""
    norm = normalize_mac(mac)
    prefix = norm[:8]
    return prefix in RASPBERRY_PI_OUIS


def read_system_arp_table() -> Dict[str, str]:
    """
    Parse operating system ARP table to map IP -> MAC address.
    Supports Linux (/proc/net/arp, ip neigh), macOS (arp -an), and Windows (arp -a).
    """
    ip_to_mac: Dict[str, str] = {}
    system = platform.system().lower()

    if system == "linux":
        # 1. Try reading /proc/net/arp directly (fastest, no subshell)
        try:
            with open("/proc/net/arp", "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        ip = parts[0]
                        mac = parts[3]
                        if mac != "00:00:00:00:00:00":
                            ip_to_mac[ip] = normalize_mac(mac)
            if ip_to_mac:
                return ip_to_mac
        except Exception:
            pass

        # 2. Fallback to 'ip neigh show'
        try:
            out = subprocess.check_output(["ip", "neigh", "show"], text=True, timeout=1.0)
            for line in out.splitlines():
                parts = line.split()
                # 192.168.1.101 dev wlan0 lladdr d8:3a:dd:11:22:33 REACHABLE
                if len(parts) >= 5 and "lladdr" in parts:
                    idx = parts.index("lladdr")
                    ip = parts[0]
                    mac = parts[idx + 1]
                    ip_to_mac[ip] = normalize_mac(mac)
            return ip_to_mac
        except Exception:
            pass

    # macOS or fallback for Linux
    try:
        out = subprocess.check_output(["arp", "-an"], text=True, timeout=1.0)
        # ? (192.168.1.101) at d8:3a:dd:11:22:33 on en0 ifscope [ethernet]
        for line in out.splitlines():
            m = re.search(r"\((\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\)\s+at\s+([0-9a-fA-F:]+)", line)
            if m:
                ip, mac = m.group(1), m.group(2)
                if mac != "(incomplete)" and mac != "ff:ff:ff:ff:ff:ff":
                    ip_to_mac[ip] = normalize_mac(mac)
        return ip_to_mac
    except Exception:
        pass

    return ip_to_mac


def check_host_port(ip: str, port: int = 5555, timeout: float = 0.25) -> bool:
    """Return True if TCP port (e.g. 5555 ZMQ host) is open on ip."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, port)) == 0
    except Exception:
        return False


def get_local_ipv4() -> Optional[str]:
    """Get active local LAN IPv4 address used to route outbound."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return None


def scan_local_subnet(
    subnet_cidr: Optional[str] = None,
    port: int = 5555,
    max_workers: int = 40,
) -> List[Dict[str, any]]:
    """
    Scan local /24 subnet for AlohaMini hosts.
    Returns list of discovered robots with IP, MAC, and Pi hardware identification.
    """
    if not subnet_cidr:
        local_ip = get_local_ipv4()
        if not local_ip:
            logger.warning("Could not determine local IPv4 for subnet scan.")
            return []
        # Default to /24 of current LAN
        network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    else:
        network = ipaddress.ip_network(subnet_cidr, strict=False)

    discovered: List[Dict[str, any]] = []
    active_ips: List[str] = []

    # 1. Parallel port scan for 5555 (alohamini_host)
    hosts_to_probe = [str(ip) for ip in network.hosts()]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ip = {executor.submit(check_host_port, ip, port, 0.2): ip for ip in hosts_to_probe}
        for future in as_completed(future_to_ip):
            ip = future_to_ip[future]
            try:
                if future.result():
                    active_ips.append(ip)
            except Exception:
                pass

    # 2. Re-read ARP table (kernel populated it during TCP syn/ack)
    arp_table = read_system_arp_table()

    for ip in active_ips:
        mac = arp_table.get(ip, "unknown")
        is_pi = is_raspberry_pi_mac(mac) if mac != "unknown" else False
        discovered.append({
            "ip": ip,
            "mac": mac,
            "is_raspberry_pi": is_pi,
            "port_5555_open": True,
            "device_name": f"AlohaMini ({mac[-8:] if mac != 'unknown' else ip})",
        })

    return discovered


def resolve_mac_to_current_ip(target_mac: str, port: int = 5555) -> Optional[str]:
    """
    Look up the current dynamic IP for a given target MAC address.
    If not immediately in ARP cache, scans the subnet to refresh ARP table.
    """
    target_mac_norm = normalize_mac(target_mac)

    # 1. Quick check current ARP table
    arp_table = read_system_arp_table()
    for ip, mac in arp_table.items():
        if mac == target_mac_norm:
            # Verify port is still active on this IP
            if check_host_port(ip, port, timeout=0.3):
                return ip

    # 2. If not found or stale, trigger quick subnet sweep
    discovered = scan_local_subnet(port=port)
    for device in discovered:
        if device.get("mac") == target_mac_norm:
            return device.get("ip")

    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AlohaMini LAN Discovery & ARP Tool")
    parser.add_argument("--port", type=int, default=5555, help="Port to probe (default: 5555)")
    parser.add_argument("--arp-only", action="store_true", help="Print raw system ARP table only")
    args = parser.parse_args()

    print("\n🔍 读取本地系统 ARP 缓存表中...")
    table = read_system_arp_table()
    print(f"找到 {len(table)} 条 ARP 记录:")
    for ip, mac in table.items():
        is_pi = is_raspberry_pi_mac(mac)
        tag = " [🍓 树莓派官方网卡]" if is_pi else ""
        print(f"  - IP: {ip:<15}  MAC: {mac}{tag}")

    if not args.arp_only:
        print(f"\n📡 正在执行局域网雷达快速扫描 (端口: {args.port})...")
        robots = scan_local_subnet(port=args.port)
        if robots:
            print(f"✅ 成功发现 {len(robots)} 台运行中的 AlohaMini 小车:")
            for r in robots:
                print(f"  * {r['device_name']}: IP={r['ip']}, MAC={r['mac']}")
        else:
            print(f"ℹ️ 当前局域网内未探测到 5555 端口开放的 AlohaMini。")
    print()

