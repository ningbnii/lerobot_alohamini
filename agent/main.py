"""
AlohaMini Agent - Main Edge Daemon Process.
Orchestrates hardware teleop, WebRTC P2P stream, watchdog, and recording.
"""

from __future__ import annotations

import sys
import time
import signal
import logging
import argparse
import threading
from typing import Any

from agent.config import AgentConfig
from agent.teleop import TeleopInputReceiver
from agent.streamer import VideoBridge
from agent.recorder import RecordingSession
from agent.uploader import DatasetUploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("alohamini-agent")


class AlohaMiniAgent:
    """The central edge daemon orchestrating the AlohaMini robot."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        self.teleop = TeleopInputReceiver(watchdog_timeout_s=self.config.watchdog_timeout_s)
        self.streamer = VideoBridge(self.config)
        self.recorder = RecordingSession(self.config)
        self.uploader = DatasetUploader(self.config)
        self._running = False
        self._heartbeat_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the agent background services."""
        logger.info("Initializing AlohaMini Agent (Device ID: %s, Model: %s)", self.config.device_id, self.config.robot_model)
        self._running = True

        # 0. Check persistent station binding to auto-follow DHCP dynamic IP
        from agent.binding import load_station_binding, resolve_effective_robot_ip
        binding = load_station_binding()
        if binding:
            logger.info("Found station binding: Station=%s, TargetMAC=%s", binding.station_id, binding.target_mac)
            effective_ip = resolve_effective_robot_ip(binding, port=self.config.port_zmq_cmd)
            if effective_ip:
                self.config.robot_host_ip = effective_ip
                logger.info("Auto-resolved robot IP to %s (via MAC %s)", effective_ip, binding.target_mac)

        # 1. Register camera video streams with local go2rtc
        if self.streamer.is_healthy():
            logger.info("go2rtc detected at %s, registering camera streams...", self.config.go2rtc_api_url)
            self.streamer.register_streams()
        else:
            logger.warning("go2rtc not detected at %s. Video streaming will activate once go2rtc starts.", self.config.go2rtc_api_url)

        # 2. Start heartbeat thread
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

        # 3. Start ZMQ command bridge to Pi Host (:5555)
        self._cmd_thread = threading.Thread(target=self._command_bridge_loop, daemon=True)
        self._cmd_thread.start()

        logger.info("AlohaMini Agent successfully started and ready for commands.")

    def stop(self) -> None:
        """Stop the agent and all ongoing tasks."""
        logger.info("Stopping AlohaMini Agent...")
        self._running = False
        self.teleop.trigger_estop()
        self.recorder.stop_recording(force=True)
        logger.info("AlohaMini Agent stopped.")

    def _command_bridge_loop(self) -> None:
        """Forward teleoperation base & lift actions to Pi Host ZMQ PULL socket (:5555)."""
        try:
            import zmq
            import json
        except ImportError:
            logger.warning("pyzmq 未安装在当前环境中，请运行: pip install pyzmq")
            return

        ctx = zmq.Context()
        cmd_socket = ctx.socket(zmq.PUSH)
        cmd_socket.setsockopt(zmq.CONFLATE, 1)
        try:
            cmd_socket.connect(self.config.zmq_cmd_url)
            logger.info("Connected to Robot Host command socket: %s", self.config.zmq_cmd_url)
        except Exception as e:
            logger.warning("Could not connect to Host command socket %s: %s", self.config.zmq_cmd_url, e)
            return

        interval = 1.0 / self.config.control_fps
        last_zero_sent = False

        while self._running:
            start_t = time.perf_counter()
            payload = self.teleop.build_zmq_action_payload()
            is_moving = any(abs(v) > 1e-4 for v in payload.values())

            # Send if moving, or send one zero frame on stopping to ensure robot brakes
            if is_moving or not last_zero_sent:
                try:
                    cmd_socket.send_string(json.dumps(payload), flags=zmq.NOBLOCK)
                    last_zero_sent = not is_moving
                except Exception as e:
                    logger.debug("ZMQ send notice: %s", e)

            elapsed = time.perf_counter() - start_t
            sleep_t = interval - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

        cmd_socket.close()
        ctx.term()

    def _heartbeat_loop(self) -> None:
        """Periodic heartbeat and telemetry report to AlohaLab BFF."""
        while self._running:
            try:
                # In production, publish MQTT topic or HTTP POST to BFF
                logger.debug(
                    "Heartbeat: state=%s, watchdog_expired=%s, keys=%s",
                    self.recorder.state,
                    self.teleop.watchdog.is_expired(),
                    self.teleop.get_active_keys(),
                )
            except Exception as e:
                logger.debug("Heartbeat loop error: %s", e)
            time.sleep(2.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="AlohaMini Edge Agent Daemon")
    parser.add_argument("--robot-model", choices=["alohamini1", "alohamini2", "alohamini2pro"], default=None)
    parser.add_argument("--robot-host-ip", default=None, help="Raspberry Pi Host IP address (e.g. 192.168.1.100)")
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--tenant-id", default=None)
    args = parser.parse_args()

    cfg = AgentConfig()
    if args.robot_model:
        cfg.robot_model = args.robot_model
    if args.robot_host_ip:
        cfg.robot_host_ip = args.robot_host_ip
    if args.device_id:
        cfg.device_id = args.device_id
    if args.tenant_id:
        cfg.tenant_id = args.tenant_id


    agent = AlohaMiniAgent(cfg)

    def sig_handler(sig, frame):
        logger.info("Received signal %s, shutting down...", sig)
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    agent.start()

    # Keep alive
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        agent.stop()


if __name__ == "__main__":
    main()
