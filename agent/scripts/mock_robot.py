#!/usr/bin/env python3
"""
AlohaMini Mock Robot (Zero-Dependency Local Simulator)

Simulates an AlohaMini Raspberry Pi robot for local testing:
- Listens on 0.0.0.0:5555 (default AlohaMini ZMQ/TCP port)
- Handles 'wiggle_robot' identification pulses
- Prints visual feedback in console so the developer can see the robot 'reacting'
"""

import argparse
import json
import logging
import socket
import sys
import threading
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MockRobot")


def handle_client(conn: socket.socket, addr: tuple):
    logger.info(f"[MockRobot] Connection received from {addr[0]}:{addr[1]}")
    conn.settimeout(2.0)
    try:
        data = conn.recv(4096)
        if not data:
            return
        logger.info(f"[MockRobot] Raw data received: {data[:100]!r}")
        try:
            payload = json.loads(data.decode("utf-8").strip())
            cmd_type = payload.get("type", "unknown")
            if cmd_type == "wiggle_identify":
                print("\n" + "=" * 50)
                print(" 🤖 [ALOHAMINI MOCK] 收到【动一动】测试指令！")
                print(" 🦾 夹爪微动: [ 闭合 -> 轻柔展开 -> 复位 ]")
                print(" ✅ 肉眼辨识确认成功！")
                print("=" * 50 + "\n")
        except Exception:
            # If ZMQ framing or binary, still acknowledge wiggle
            print("\n" + "=" * 50)
            print(" 🤖 [ALOHAMINI MOCK] 收到指令脉冲！")
            print(" 🦾 机械臂动一动成功！")
            print("=" * 50 + "\n")
    except socket.timeout:
        pass
    except Exception as e:
        logger.debug(f"Client handler error: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_mock_server(host: str = "0.0.0.0", port: int = 5555):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"\n=======================================================")
    print(f" 🤖 AlohaMini 本地模拟器已就绪")
    print(f" 监听地址: {host}:{port}")
    print(f" 模拟出厂 MAC: d8:3a:dd:11:22:33 (Raspberry Pi Foundation)")
    print(f" 提示: 您现在可以运行测试脚本触发【动一动】指令！")
    print(f" 按 Ctrl+C 退出模拟器")
    print(f"=======================================================\n")

    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[MockRobot] 模拟器已安全关闭。")
    finally:
        server.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AlohaMini Mock Robot Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5555, help="Bind port (default: 5555)")
    args = parser.parse_args()
    run_mock_server(args.host, args.port)
