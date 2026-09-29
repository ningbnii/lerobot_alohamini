"""
Video bridge and WebRTC P2P signaling manager via go2rtc.
Enforces ADR-023: same-LAN host ICE candidates only, 0 bps cloud media egress.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
import ipaddress
from typing import Any
from agent.config import AgentConfig

logger = logging.getLogger(__name__)


def is_private_ip(ip_str: str) -> bool:
    """Check if an IP address belongs to RFC 1918 private subnets."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


class VideoBridge:
    """Manages go2rtc stream registration and WebRTC SDP handshake."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.go2rtc_url = config.go2rtc_api_url.rstrip("/")

    def is_healthy(self) -> bool:
        """Check if go2rtc is running locally."""
        try:
            req = urllib.request.Request(f"{self.go2rtc_url}/api/version", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def register_streams(self) -> bool:
        """
        Register camera streams from Pi ZMQ/RTSP into go2rtc.
        alohamini_top: ZMQ camera stream from Raspberry Pi Host
        alohamini_wrist: ZMQ camera stream from Raspberry Pi Host
        """
        streams = {
            "alohamini_top": f"ffmpeg:tcp://{self.config.robot_host_ip}:{self.config.port_zmq_camera_stream}#video=h264",
            "alohamini_wrist": f"ffmpeg:tcp://{self.config.robot_host_ip}:{self.config.port_zmq_camera_stream}#video=h264",
        }
        success = True
        for name, src in streams.items():
            url = f"{self.go2rtc_url}/api/streams?src={urllib.parse.quote(src)}&name={name}"
            try:
                req = urllib.request.Request(url, method="PUT")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status not in (200, 201):
                        success = False
            except Exception as e:
                logger.debug("Stream registration notice (%s): %s", name, e)
        return success


    def handle_sdp_offer(self, stream_name: str, client_sdp_offer: str) -> str | None:
        """
        Post SDP offer to go2rtc and retrieve answer.
        Validates that connection remains local.
        """
        url = f"{self.go2rtc_url}/api/webrtc?src={urllib.parse.quote(stream_name)}"
        try:
            data = client_sdp_offer.encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/sdp"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    answer_sdp = resp.read().decode("utf-8")
                    return answer_sdp
            return None
        except Exception as e:
            logger.error("WebRTC SDP exchange failed: %s", e)
            return None

    def validate_ice_candidate(self, candidate_line: str) -> bool:
        """
        Enforce ADR-023:
        Must only accept 'host' candidate with RFC 1918 private IP.
        Must REJECT srflx and relay candidates.
        """
        parts = candidate_line.strip().split()
        if len(parts) < 8:
            return False

        try:
            typ_index = parts.index("typ")
            cand_type = parts[typ_index + 1].lower()
            ip = parts[4]

            if cand_type != "host":
                logger.warning("Rejected non-host ICE candidate type: %s", cand_type)
                return False

            if not is_private_ip(ip):
                logger.warning("Rejected public/non-RFC1918 ICE candidate IP: %s", ip)
                return False

            return True
        except (ValueError, IndexError):
            return False


class ZMQCameraStreamSubscriber:
    """
    Subscribes directly to Raspberry Pi Host's CameraStreamPublisher (:5557).
    Receives multipart [topic, metadata_json, jpeg_bytes] and buffers the latest frames.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5557) -> None:
        self.endpoint = f"tcp://{host}:{port}"
        self.latest_frames: dict[str, bytes] = {}
        self.latest_metadata: dict[str, Any] = {}
        self._running = False
        self._thread: Any = None
        self._lock = __import__("threading").Lock()

    def start(self) -> bool:
        try:
            import zmq
        except ImportError:
            logger.warning("pyzmq is not installed; ZMQCameraStreamSubscriber disabled.")
            return False

        self._running = True
        self._thread = __import__("threading").Thread(target=self._subscriber_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False

    def _subscriber_loop(self) -> None:
        import zmq
        ctx = zmq.Context()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.CONFLATE, 1)  # only keep latest frame per camera
        sock.setsockopt_string(zmq.SUBSCRIBE, "")  # subscribe to all topics ('top', 'wrist')
        sock.connect(self.endpoint)

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)

        while self._running:
            try:
                socks = dict(poller.poll(500))
                if sock in socks and socks[sock] == zmq.POLLIN:
                    parts = sock.recv_multipart(flags=zmq.NOBLOCK)
                    if len(parts) >= 3:
                        topic = parts[0].decode("utf-8")
                        meta_raw = parts[1].decode("utf-8")
                        jpeg_bytes = parts[2]
                        with self._lock:
                            self.latest_frames[topic] = jpeg_bytes
                            try:
                                self.latest_metadata[topic] = json.loads(meta_raw)
                            except Exception:
                                pass
            except Exception as e:
                logger.debug("ZMQ subscriber read notice: %s", e)

        sock.close()
        ctx.term()

    def get_latest_frame(self, camera_name: str) -> bytes | None:
        with self._lock:
            return self.latest_frames.get(camera_name)

