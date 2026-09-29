"""
AlohaMini Physical Identification and Verification.

Provides:
1. 'wiggle_robot': Send a minor motion pulse to visually verify 'this is the robot in front of me'.
2. 'capture_preview_jpeg': Fetch a single JPEG observation frame to show a camera preview in the web UI.
"""

import base64
import json
import logging
import time
from typing import Optional

logger = logging.getLogger("agent.identify")


def wiggle_robot(ip: str, port: int = 5555, timeout_s: float = 1.0) -> bool:
    """
    Send a momentary safe command pulse to alohamini_host to make the gripper
    open/close gently, giving the student an unambiguous physical confirmation.
    """
    try:
        import zmq
        has_zmq = True
    except ImportError:
        has_zmq = False

    if has_zmq:
        ctx = zmq.Context()
        sock = ctx.socket(zmq.PUSH)
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.CONFLATE, 1)

        try:
            sock.connect(f"tcp://{ip}:{port}")
            pulse_cmd = {
                "type": "wiggle_identify",
                "timestamp": time.time(),
            }
            sock.send(json.dumps(pulse_cmd).encode("utf-8"), flags=zmq.NOBLOCK)
            time.sleep(0.05)
            return True
        except Exception as e:
            logger.warning("Failed to send ZMQ wiggle command to %s:%d: %s", ip, port, e)
            return False
        finally:
            sock.close(linger=0)
            ctx.term()
    else:
        # Graceful pure-python socket fallback (useful for testing and environments without pyzmq)
        try:
            import socket
            with socket.create_connection((ip, port), timeout=timeout_s) as s:
                payload = json.dumps({"type": "wiggle_identify", "timestamp": time.time()}) + "\n"
                s.sendall(payload.encode("utf-8"))
                return True
        except Exception as e:
            logger.warning("Failed to send TCP wiggle command to %s:%d: %s", ip, port, e)
            return False


def capture_preview_jpeg(
    ip: str,
    port_obs: int = 5556,
    timeout_s: float = 1.5,
) -> Optional[str]:
    """
    Request a single observation frame from alohamini_host and extract
    the first available JPEG image, returning it as a data URL (base64)
    for instant rendering in the web browser.
    """
    try:
        import zmq
    except ImportError:
        return None

    ctx = zmq.Context()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))

    try:
        sock.connect(f"tcp://{ip}:{port_obs}")
        # Send observation request token
        token = b"identify_preview"
        sock.send(token)

        # Receive multipart: [json_state, img_wrist?, img_forward?]
        parts = sock.recv_multipart()
        if len(parts) >= 2:
            # First frame after state is usually an encoded JPEG
            for part in parts[1:]:
                # Check for JPEG SOI marker (0xFFD8)
                if len(part) > 4 and part[:2] == b"\xff\xd8":
                    b64_str = base64.b64encode(part).decode("utf-8")
                    return f"data:image/jpeg;base64,{b64_str}"

        # If no JPEG bytes, generate a mock visual confirmation data URL for simulation/testing
        return None
    except Exception as e:
        logger.debug("Could not capture live preview frame from %s:%d: %s", ip, port_obs, e)
        return None
    finally:
        sock.close(linger=0)
        ctx.term()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AlohaMini Identify / Wiggle Tool")
    parser.add_argument("--ip", default="127.0.0.1", help="Target robot IP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5555, help="Target command port (default: 5555)")
    args = parser.parse_args()

    print(f"\n[Identify] 正在向 {args.ip}:{args.port} 下发【动一动】辨识指令...")
    success = wiggle_robot(args.ip, args.port)
    if success:
        print(f"✅ [OK] 动一动指令已成功发送至 {args.ip}:{args.port}！请观察机械臂夹爪是否动作。\n")
    else:
        print(f"❌ [Failed] 无法连接到 {args.ip}:{args.port}，请检查目标 IP 与端口是否已开机并连上局域网。\n")

