"""
Video bridge and WebRTC P2P signaling manager via go2rtc.
Enforces ADR-023: same-LAN host ICE candidates only, 0 bps cloud media egress.
Provides direct ZeroMQ JPEG subscription and local HTTP MJPEG streaming fallback.
"""

from __future__ import annotations

import json
import time
import logging
import threading
import ipaddress
import urllib.request
import urllib.parse
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any
from agent.config import AgentConfig

logger = logging.getLogger(__name__)


# Canonical physical cameras: forward / backward / chest / wrist_left / wrist_right.
# Single naming scheme end to end (Pi config -> ZMQ topics -> HTTP -> go2rtc).


def is_private_ip(ip_str: str) -> bool:
    """Check if an IP address belongs to RFC 1918 private subnets."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback
    except ValueError:
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
        self._lock = threading.Lock()

    def start(self) -> bool:
        try:
            import zmq
        except ImportError:
            logger.warning("pyzmq is not installed; ZMQCameraStreamSubscriber disabled.")
            return False

        if self._running:
            return True

        self._running = True
        self._thread = threading.Thread(target=self._subscriber_loop, daemon=True)
        self._thread.start()
        logger.info("ZMQ camera subscriber started for %s", self.endpoint)
        return True

    def stop(self) -> None:
        self._running = False

    def _subscriber_loop(self) -> None:
        import zmq
        ctx = zmq.Context()
        sock = ctx.socket(zmq.SUB)
        # NOTE: CONFLATE must NOT be used here: it only supports single-part
        # messages and aborts the process (fq.cpp:80 assert) on multipart
        # [topic, meta, jpeg] frames. RCVHWM=1 + drain-to-latest below gives
        # the same "latest frame only" behaviour safely.
        sock.setsockopt(zmq.RCVHWM, 1)
        sock.setsockopt_string(zmq.SUBSCRIBE, "")  # subscribe to all topics
        sock.connect(self.endpoint)

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)

        while self._running:
            try:
                socks = dict(poller.poll(500))
                if sock in socks and socks[sock] == zmq.POLLIN:
                    # Drain queue, keep only the newest multipart frame.
                    parts = None
                    while True:
                        try:
                            parts = sock.recv_multipart(flags=zmq.NOBLOCK)
                        except zmq.Again:
                            break
                    if not parts or len(parts) < 3:
                        continue
                    topic = parts[0].decode("utf-8")
                    meta_raw = parts[1].decode("utf-8")
                    jpeg_bytes = parts[2]
                    with self._lock:
                        # Store only the exact topic and its physical base name.
                        self.latest_frames[topic] = jpeg_bytes
                        self.latest_frames[topic.removeprefix("camera/")] = jpeg_bytes
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
            # Direct lookup, then topic lookup. Unknown names return None
            # so HTTP serves 404 instead of a misleading random camera.
            frame = self.latest_frames.get(camera_name)
            if frame is not None:
                return frame
            return self.latest_frames.get(f"camera/{camera_name}")

    def get_available_cameras(self) -> list[str]:
        # Only physical cameras (camera/<name> topics), never legacy aliases.
        with self._lock:
            names = {
                k.removeprefix("camera/")
                for k in self.latest_frames.keys()
                if k.startswith("camera/")
            }
            return sorted(names)


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class _MJPEGHTTPHandler(BaseHTTPRequestHandler):
    """Serves JPEG snapshots and multipart MJPEG video streams over HTTP."""

    subscriber: ZMQCameraStreamSubscriber | None = None

    def log_message(self, format, *args):
        # Silence routine HTTP request logging
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        # 1. API: List available cameras
        if path in ("/api/cameras", "/cameras"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            cams = self.subscriber.get_available_cameras() if self.subscriber else []
            self.wfile.write(json.dumps({"cameras": cams}).encode("utf-8"))
            return

        # Determine camera name
        cam_name = query.get("cam", ["forward"])[0]
        if path.startswith("/stream/"):
            cam_name = path.removeprefix("/stream/")
        elif path.startswith("/camera/") or path.startswith("/snapshot/"):
            cam_name = path.split("/")[-1]

        # 2. MJPEG Stream
        if path.startswith("/stream") or query.get("type", [""])[0] == "mjpeg":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            try:
                while True:
                    frame = self.subscriber.get_latest_frame(cam_name) if self.subscriber else None
                    if frame is not None:
                        header = (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            + f"Content-Length: {len(frame)}\r\n\r\n".encode()
                        )
                        self.wfile.write(header + frame + b"\r\n")
                    time.sleep(0.033)  # ~30 FPS
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # 3. Single JPEG Snapshot
        frame = self.subscriber.get_latest_frame(cam_name) if self.subscriber else None
        if frame is not None:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(frame)
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Camera frame not available yet")


class VideoBridge:
    """Manages go2rtc stream registration, local HTTP MJPEG server, and WebRTC SDP handshake."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.go2rtc_url = config.go2rtc_api_url.rstrip("/")
        self.subscriber = ZMQCameraStreamSubscriber(
            host=config.robot_host_ip,
            port=config.port_zmq_camera_stream,
        )
        self.http_server: HTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start both ZMQ subscriber and local HTTP streaming server."""
        self.subscriber.start()

        # Start HTTP server on camera_http_port
        handler_cls = _MJPEGHTTPHandler
        handler_cls.subscriber = self.subscriber
        try:
            self.http_server = _ThreadedHTTPServer(("0.0.0.0", self.config.camera_http_port), handler_cls)
            self._server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
            self._server_thread.start()
            logger.info("Local Camera HTTP/MJPEG streaming server listening on port %d", self.config.camera_http_port)
        except Exception as e:
            logger.warning("Could not bind Camera HTTP streaming server on port %d: %s", self.config.camera_http_port, e)

        # Register with go2rtc if running
        if self.is_healthy():
            self.register_streams()

    def stop(self) -> None:
        """Stop subscriber and HTTP server."""
        self.subscriber.stop()
        if self.http_server:
            try:
                self.http_server.shutdown()
            except Exception:
                pass
            self.http_server = None

    def is_healthy(self) -> bool:
        """Check if go2rtc is running locally."""
        for path in ("/api/version", "/api"):
            try:
                req = urllib.request.Request(f"{self.go2rtc_url}{path}", method="GET")
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                continue
        return False

    def register_streams(self) -> bool:
        """
        Register camera streams into local go2rtc (compatible with go2rtc 1.8 & 1.9.14).
        Feeds from local HTTP MJPEG server, avoiding raw TCP ZMQ wire decode errors.
        """
        http_base = f"http://127.0.0.1:{self.config.camera_http_port}"
        streams = {
            "alohamini_forward": f"{http_base}/stream/forward",
            "alohamini_backward": f"{http_base}/stream/backward",
            "alohamini_chest": f"{http_base}/stream/chest",
            "alohamini_wrist_left": f"{http_base}/stream/wrist_left",
            "alohamini_wrist_right": f"{http_base}/stream/wrist_right",
        }
        success = True
        for name, src in streams.items():
            # 1. Try go2rtc 1.9+ JSON POST
            post_url = f"{self.go2rtc_url}/api/streams"
            try:
                payload = json.dumps({"name": name, "channels": [src]}).encode("utf-8")
                req = urllib.request.Request(post_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status in (200, 201):
                        continue
            except Exception:
                pass

            # 2. Fallback to PUT query param
            put_url = f"{self.go2rtc_url}/api/streams?src={urllib.parse.quote(src)}&name={name}"
            try:
                req = urllib.request.Request(put_url, method="PUT")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status not in (200, 201):
                        success = False
            except Exception as e:
                logger.debug("Stream registration notice (%s): %s", name, e)
                success = False

        if success:
            logger.info("Successfully registered camera streams (%s) into go2rtc", list(streams.keys()))
        return success

    def handle_sdp_offer(self, stream_name: str, client_sdp_offer: str) -> str | None:
        """Post SDP offer to go2rtc and retrieve answer."""
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
        """Enforce ADR-023: Must only accept host candidates with RFC 1918 private IP."""
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
