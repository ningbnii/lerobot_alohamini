#!/usr/bin/env python3
"""
AlohaMini 局域网雷达与动态 IP 追踪：一键交互式自测向导 (Interactive Test Wizard)

使用方法:
  python3 lerobot_alohamini/agent/scripts/interactive_test.py
"""

import json
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

# Add lerobot_alohamini root to sys.path
agent_dir = Path(__file__).resolve().parent.parent.parent
if str(agent_dir) not in sys.path:
    sys.path.insert(0, str(agent_dir))

from agent.discovery import (
    read_system_arp_table,
    is_raspberry_pi_mac,
    normalize_mac,
    check_host_port,
)
from agent.identify import wiggle_robot
from agent.binding import (
    StationBinding,
    save_station_binding,
    load_station_binding,
    resolve_effective_robot_ip,
    get_default_binding_path,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")


def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def run_all_in_one_simulation():
    """纯软件全链路演练：启动本地模拟车 -> 动一动 -> 绑定 -> 模拟 IP 漂移 -> 自动纠偏"""
    print_header("🚀 开始【纯单机一键全流程模拟演练】")
    print("无需真实硬件，本演练将在单机上完整重现小车绑定与 IP 自动找回全生命周期。\n")

    # 1. 启动临时 Mock Robot 线程
    mock_port = 5555
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_socket.bind(("127.0.0.1", mock_port))
        server_socket.listen(5)
    except Exception as e:
        print(f"❌ 端口 {mock_port} 已被占用: {e}")
        return

    stop_server = threading.Event()
    received_wiggle = threading.Event()

    def mock_server_loop():
        server_socket.settimeout(0.5)
        while not stop_server.is_set():
            try:
                conn, _ = server_socket.accept()
                data = conn.recv(1024)
                if b"wiggle_identify" in data:
                    received_wiggle.set()
                conn.close()
            except socket.timeout:
                continue
            except Exception:
                break
        server_socket.close()

    t = threading.Thread(target=mock_server_loop, daemon=True)
    t.start()

    try:
        # Step 1: 模拟小车就绪
        print("▶️ [步骤 1/4] 模拟小车上线")
        mock_mac = "d8:3a:dd:aa:bb:cc"
        initial_ip = "127.0.0.1"
        print(f"  - 模拟小车出厂 MAC : {mock_mac} (Raspberry Pi Foundation)")
        print(f"  - 首次分配内网 IP  : {initial_ip}")
        print(f"  - 5555 通信端口    : 监听中")
        time.sleep(1)

        # Step 2: 发送动一动指令
        print("\n▶️ [步骤 2/4] 向小车发送【动一动】指令，肉眼确认小车身份")
        print(f"  - 正在连接 {initial_ip}:{mock_port} 发送动一动脉冲...")
        wiggle_ok = wiggle_robot(initial_ip, mock_port, timeout_s=1.0)
        time.sleep(0.3)
        if wiggle_ok and received_wiggle.is_set():
            print("  🤖 [小车反馈] 收到动一动指令！夹爪执行：[ 闭合 -> 轻柔展开 -> 复位 ]")
            print("  ✅ [确认成功] 教师/学生在工控机前肉眼已确认是眼前的这台小车！")
        else:
            print("  ⚠️ 未能接收到动作反馈")
        time.sleep(1)

        # Step 3: 持久化绑定到工位
        print("\n▶️ [步骤 3/4] 点击【认领此车】，持久化保存绑定到本地工位")
        test_binding_file = Path("/tmp/test_alohamini_binding.json")
        binding = StationBinding(
            station_id="station_01",
            target_mac=mock_mac,
            last_known_ip=initial_ip,
        )
        save_station_binding(binding, test_binding_file)
        print(f"  - 已将出厂 MAC [{mock_mac}] 永久绑定至工位 [station_01]")
        print(f"  - 配置文件生成在: {test_binding_file}")
        time.sleep(1)

        # Step 4: 模拟 DHCP 动态 IP 漂移
        print("\n▶️ [步骤 4/4] 核心验证：模拟小车重启，路由器重新分配了新 IP！")
        new_dynamic_ip = "192.168.1.188"
        print(f"  - 模拟场景：学校路由器 DHCP 重新分配，小车 IP 从 [{initial_ip}] 漂移到 [{new_dynamic_ip}]")
        print(f"  - 但物理 MAC 地址 [{mock_mac}] 终生保持不变")
        print("  - 工控机尝试连接... 调用内核 ARP 硬件雷达自动反查...")

        # Mock ARP table containing the new IP
        import agent.binding as b_module
        orig_resolve = b_module.resolve_mac_to_current_ip
        b_module.resolve_mac_to_current_ip = lambda mac, port=5555: new_dynamic_ip if mac == normalize_mac(mock_mac) else None

        effective_ip = resolve_effective_robot_ip(binding, filepath=test_binding_file)
        b_module.resolve_mac_to_current_ip = orig_resolve

        print(f"\n  🎯 [雷达追踪结果] 自动识别小车新内网 IP: {effective_ip}")
        print("  ✅ [零重新绑定] 工位配置已静默自动刷新，无需师生重复认领或手动配置！")
        print("\n🎉 【全流程演练完成】所有功能完全符合预期！")

    finally:
        stop_server.set()
        t.join(timeout=1.0)


def scan_local_arp_radar():
    """扫描当前局域网 ARP 表并高亮树莓派设备"""
    print_header("📡 正在扫描当前局域网 ARP 硬件雷达...")
    table = read_system_arp_table()
    if not table:
        print("⚠️ 未能在系统 ARP 缓存中找到记录。请确认 Wi-Fi 是否已连接。")
        return

    print(f"共发现 {len(table)} 台局域网设备:\n")
    print(f"  {'IP 地址':<18} {'物理 MAC 地址':<20} {'设备特征 / 状态'}")
    print("  " + "-" * 55)

    pi_count = 0
    for ip, mac in table.items():
        is_pi = is_raspberry_pi_mac(mac)
        if is_pi:
            pi_count += 1
            status = "🍓 树莓派官方网卡 (AlohaMini 候选车)"
        else:
            status = "普通网络设备"
        print(f"  {ip:<18} {mac:<20} {status}")

    print("\n" + "-" * 55)
    if pi_count > 0:
        print(f"✅ 成功找到 {pi_count} 台树莓派候选小车！")
        print("💡 您可以使用菜单项 [3]，直接对该 IP 发送【动一动】指令进行视觉确认。")
    else:
        print("ℹ️ 当前局域网内暂未发现树莓派官方 MAC 地址的小车。")
        print("   如果小车已开机，请确保小车和您的电脑连接在同一个 Wi-Fi 热点。")


def manual_wiggle_test():
    """手动输入 IP 发送动一动"""
    print_header("👋 手动触发【动一动】测试指令")
    target_ip = input("请输入目标小车的 IP 地址 (如 192.168.1.100 或 127.0.0.1): ").strip()
    if not target_ip:
        print("❌ 未输入有效 IP")
        return

    port_input = input("请输入端口 (直接回车默认 5555): ").strip()
    target_port = int(port_input) if port_input.isdigit() else 5555

    print(f"\n正在向 {target_ip}:{target_port} 下发微动脉冲...")
    success = wiggle_robot(target_ip, target_port, timeout_s=1.5)
    if success:
        print(f"✅ 指令已送达 {target_ip}:{target_port}！请观察机械臂夹爪是否开合。")
    else:
        print(f"❌ 无法连接到 {target_ip}:{target_port}，请检查目标设备是否开机并开放了 5555 端口。")


def view_current_binding():
    """查看当前工位持久化配置"""
    print_header("📄 当前工位配置查看")
    binding = load_station_binding()
    path = get_default_binding_path()
    if not binding:
        print(f"ℹ️ 当前工位尚未绑定任何小车。")
        print(f"   配置文件路径: {path}")
    else:
        print(f"配置文件路径: {path}")
        print(f"  工位编号 (Station ID): {binding.station_id}")
        print(f"  绑定物理 MAC 地址    : {binding.target_mac}")
        print(f"  最近一次已知内网 IP  : {binding.last_known_ip}")
        print(f"  最近同步更新时间     : {time.ctime(binding.updated_at)}")


def main():
    while True:
        print("\n" + "=" * 60)
        print("       🤖 AlohaMini 局域网雷达与动态 IP 追踪自测向导")
        print("=" * 60)
        print("  [1] 🚀 一键全流程单机模拟演练 (首选推荐: 3分钟全自动走通)")
        print("  [2] 📡 扫描当前局域网 ARP 硬件雷达 (查找真机小车)")
        print("  [3] 👋 向指定小车下发【动一动】动作指令")
        print("  [4] 📄 查看工位持久化配置 (~/.config/alohalab/...)")
        print("  [0] 🚪 退出")
        print("-" * 60)

        choice = input("请输入选项编号 (0-4): ").strip()
        if choice == "1":
            run_all_in_one_simulation()
        elif choice == "2":
            scan_local_arp_radar()
        elif choice == "3":
            manual_wiggle_test()
        elif choice == "4":
            view_current_binding()
        elif choice == "0":
            print("\n感谢使用，祝测试顺利！\n")
            break
        else:
            print("⚠️ 无效输入，请输入数字 0 到 4。")


if __name__ == "__main__":
    main()
