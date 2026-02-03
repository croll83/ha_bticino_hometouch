"""SRTP to WebRTC proxy for BTicino Hometouch using go2rtc integration."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import os
import socket
from dataclasses import dataclass
from typing import Callable
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


@dataclass
class StreamConfig:
    """Configuration for a media stream."""
    camera_id: int
    rtp_port: int
    rtcp_port: int
    srtp_key: bytes
    codec: str = "h264"
    sample_rate: int = 90000


class Go2RTCProxy:
    """Connects to go2rtc for SRTP to WebRTC conversion.

    Since Home Assistant 2024.11, go2rtc is built-in and runs in the same
    container as HA Core. This means:
    - API is on localhost:1984
    - RTSP server on localhost:8554
    - No Docker container isolation issues

    WebRTC flow:
    1. FFmpeg receives SRTP, decrypts, pushes RTSP to go2rtc
    2. go2rtc serves the stream via WebRTC to browsers
    """

    ADDON_HOSTNAMES = [
        "127.0.0.1",         # Built-in go2rtc (HA 2024.11+) - PREFERRED
        "localhost",
        "a889bffc-go2rtc",   # Add-on hostname (fallback)
    ]

    def __init__(
        self,
        api_port: int = 1984,
        rtsp_port: int = 8554,
        webrtc_port: int = 8555,
        addon_hostname: str | None = None,
        config_dir: str = "/config/bticino_hometouch",
        **kwargs,
    ):
        """Initialize go2rtc proxy."""
        self._api_port = api_port
        self._rtsp_port = rtsp_port
        self._webrtc_port = webrtc_port
        self._addon_hostname = addon_hostname
        self._config_dir = Path(config_dir)
        self._streams: dict[str, StreamConfig] = {}
        self._running = False
        self._api_base_url: str | None = None

    async def start(self) -> bool:
        """Connect to go2rtc."""
        import aiohttp

        for hostname in ([self._addon_hostname] if self._addon_hostname else self.ADDON_HOSTNAMES):
            api_url = f"http://{hostname}:{self._api_port}"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{api_url}/api", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            self._api_base_url = api_url
                            self._running = True
                            _LOGGER.info("Connected to go2rtc at %s", api_url)
                            return True
            except Exception as e:
                _LOGGER.debug("go2rtc not found at %s: %s", hostname, e)
                continue

        _LOGGER.error(
            "go2rtc not found. Please ensure go2rtc is installed. "
            "It's built-in since HA 2024.11, or install the go2rtc add-on."
        )
        return False

    async def stop(self):
        """Disconnect from go2rtc and remove all streams."""
        for stream_name in list(self._streams.keys()):
            await self.remove_stream(stream_name)

        self._running = False
        self._api_base_url = None
        self._streams.clear()

    async def remove_stream(self, stream_name: str) -> bool:
        """Remove a stream from go2rtc."""
        if stream_name in self._streams:
            del self._streams[stream_name]

        # Clean up SDP file
        sdp_path = f"/tmp/bticino_{stream_name}.sdp"
        try:
            if os.path.exists(sdp_path):
                os.remove(sdp_path)
        except Exception:
            pass

        if not self._running or not self._api_base_url:
            return True

        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self._api_base_url}/api/streams",
                    params={"name": stream_name},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.info("Stream %s removed from go2rtc", stream_name)
                    return True
        except Exception as e:
            _LOGGER.debug("Error removing stream %s: %s", stream_name, e)
            return True

    async def webrtc_offer(self, stream_name: str, sdp_offer: str) -> str | None:
        """Send WebRTC offer to go2rtc and get answer."""
        if not self._running or not self._api_base_url:
            _LOGGER.error("go2rtc not connected, cannot process WebRTC offer")
            return None

        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._api_base_url}/api/webrtc",
                    params={"src": stream_name},
                    json={"type": "offer", "sdp": sdp_offer},
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        sdp_answer = data.get("sdp") or data.get("value")
                        _LOGGER.info("Got WebRTC answer for %s (%d bytes)",
                                    stream_name, len(sdp_answer) if sdp_answer else 0)
                        return sdp_answer
                    else:
                        resp_text = await resp.text()
                        _LOGGER.error("WebRTC offer failed for %s: %d %s",
                                     stream_name, resp.status, resp_text)
                        return None
        except Exception as e:
            _LOGGER.error("Error sending WebRTC offer for %s: %s", stream_name, e)
            return None

    async def check_stream_active(self, stream_name: str) -> bool:
        """Check if a stream is active and has producers."""
        if not self._running or not self._api_base_url:
            return False

        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                # Query all streams (without src param) to get dict with stream names as keys
                async with session.get(
                    f"{self._api_base_url}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Response is: {"stream_name": {"producers": [...], "consumers": [...]}}
                        if stream_name in data:
                            stream_info = data[stream_name]
                            producers = stream_info.get("producers", [])
                            # Check if we have at least one active producer with data
                            for p in producers:
                                # If producer has bytes_recv, stream is active
                                if p.get("bytes_recv", 0) > 0:
                                    return True
                            # Even without bytes_recv, having producers means stream exists
                            return len(producers) > 0
        except Exception as e:
            _LOGGER.debug("Error checking stream %s: %s", stream_name, e)
        return False

    def get_rtsp_url(self, camera_name: str) -> str:
        """Get RTSP URL for a camera."""
        if self._api_base_url:
            import re
            match = re.search(r'http://([^:]+):', self._api_base_url)
            hostname = match.group(1) if match else "127.0.0.1"
        else:
            hostname = self._addon_hostname or "127.0.0.1"
        return f"rtsp://{hostname}:{self._rtsp_port}/{camera_name}"

    def get_webrtc_url(self, camera_name: str) -> str:
        """Get WebRTC API URL for a camera."""
        if self._api_base_url:
            return f"{self._api_base_url}/api/ws?src={camera_name}"
        return ""

    @property
    def is_running(self) -> bool:
        """Return True if connected to go2rtc."""
        return self._running


class SRTPtoRTSP:
    """Converts SRTP stream to RTSP for go2rtc.

    Uses FFmpeg to:
    1. Receive SRTP on a UDP port
    2. Decrypt using the provided key
    3. Push RTSP to go2rtc's RTSP server
    """

    GO2RTC_RTSP_PORT = 8554

    def __init__(
        self,
        srtp_port: int,
        srtp_key: bytes,
        stream_name: str,
    ):
        """Initialize SRTP to RTSP converter."""
        self._srtp_port = srtp_port
        self._srtp_key = srtp_key
        self._stream_name = stream_name
        self._running = False
        self._ffmpeg_process: subprocess.Popen | None = None
        self._ffmpeg_task: asyncio.Task | None = None

    async def start(self):
        """Start FFmpeg to convert SRTP to RTSP."""
        import base64

        rtsp_url = f"rtsp://127.0.0.1:{self.GO2RTC_RTSP_PORT}/{self._stream_name}"
        srtp_key_b64 = base64.b64encode(self._srtp_key).decode('ascii') if self._srtp_key else ""

        _LOGGER.info("Starting SRTP->RTSP: port %d -> %s (key=%s...)",
                    self._srtp_port, rtsp_url, srtp_key_b64[:20] if srtp_key_b64 else "none")

        # Create SDP file for FFmpeg
        # Use 0.0.0.0 to receive packets from any source IP (the gateway sends from public IP)
        sdp_content = f"""v=0
o=- 0 0 IN IP4 0.0.0.0
s=BTicino Camera
c=IN IP4 0.0.0.0
t=0 0
m=video {self._srtp_port} RTP/SAVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1
a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{srtp_key_b64}
a=recvonly
"""

        sdp_path = f"/tmp/bticino_{self._stream_name}.sdp"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._write_file, sdp_path, sdp_content)
            _LOGGER.debug("Created SDP file: %s", sdp_path)
        except Exception as e:
            _LOGGER.error("Failed to create SDP file: %s", e)
            return

        # FFmpeg command: SRTP input -> RTSP output (push to go2rtc)
        # go2rtc receives RTSP streams on port 8554
        #
        # Strategy: Re-encode with frequent keyframes to avoid green artifacts
        # - libx264 with higher bitrate to avoid VBV underflow
        # - Longer probing to handle corrupted input streams
        # - CRF mode for consistent quality (no VBV issues)
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-protocol_whitelist", "file,udp,rtp,srtp,crypto",
            # Input: more generous probing to handle bad streams
            "-fflags", "+genpts+igndts+discardcorrupt",
            "-flags", "low_delay",
            "-analyzeduration", "1000000",  # 1s - enough to find valid frames
            "-probesize", "500000",  # 500KB
            "-reorder_queue_size", "0",
            "-err_detect", "ignore_err",  # Continue on errors
            "-i", sdp_path,
            # Output: re-encode with quality focus
            "-c:v", "libx264",
            "-preset", "veryfast",  # Slightly slower but better quality
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-crf", "23",  # CRF mode - consistent quality, no VBV underflow
            "-g", "15",  # Keyframe every 15 frames (~0.5s)
            "-keyint_min", "8",
            "-sc_threshold", "0",
            "-pix_fmt", "yuv420p",
            "-an",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            rtsp_url,
        ]

        _LOGGER.debug("FFmpeg command: %s", " ".join(ffmpeg_cmd))

        try:
            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._running = True
            self._ffmpeg_task = asyncio.create_task(self._monitor_ffmpeg())
            _LOGGER.info("FFmpeg started with PID %d", self._ffmpeg_process.pid)
        except FileNotFoundError:
            _LOGGER.error("ffmpeg not found. Please install ffmpeg.")
        except Exception as e:
            _LOGGER.error("Failed to start ffmpeg: %s", e)

    def _write_file(self, path: str, content: str) -> None:
        """Write file synchronously."""
        with open(path, 'w') as f:
            f.write(content)

    async def _monitor_ffmpeg(self):
        """Monitor ffmpeg process output."""
        if not self._ffmpeg_process:
            return

        loop = asyncio.get_event_loop()

        try:
            while self._running and self._ffmpeg_process.poll() is None:
                try:
                    stderr_data = await asyncio.wait_for(
                        loop.run_in_executor(None, self._ffmpeg_process.stderr.readline),
                        timeout=1.0
                    )
                    if stderr_data:
                        line = stderr_data.decode().strip()
                        if line:
                            _LOGGER.debug("FFmpeg: %s", line)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

        if self._ffmpeg_process:
            return_code = self._ffmpeg_process.poll()
            if return_code is not None:
                _LOGGER.info("FFmpeg exited with code %d", return_code)

    async def stop(self):
        """Stop FFmpeg."""
        self._running = False

        if self._ffmpeg_task:
            self._ffmpeg_task.cancel()
            try:
                await self._ffmpeg_task
            except asyncio.CancelledError:
                pass
            self._ffmpeg_task = None

        if self._ffmpeg_process:
            _LOGGER.info("Stopping FFmpeg (PID %d)", self._ffmpeg_process.pid)
            try:
                self._ffmpeg_process.terminate()
                await asyncio.sleep(0.5)
                if self._ffmpeg_process.poll() is None:
                    self._ffmpeg_process.kill()
            except Exception as e:
                _LOGGER.debug("Error stopping FFmpeg: %s", e)
            self._ffmpeg_process = None

        # Clean up SDP file
        sdp_path = f"/tmp/bticino_{self._stream_name}.sdp"
        try:
            if os.path.exists(sdp_path):
                os.remove(sdp_path)
        except Exception:
            pass

        _LOGGER.info("SRTP->RTSP stopped for %s", self._stream_name)

    @property
    def is_running(self) -> bool:
        """Check if FFmpeg is running."""
        return self._running and self._ffmpeg_process is not None and self._ffmpeg_process.poll() is None


class BidirectionalAudio:
    """Handles bidirectional SRTP audio for intercom.

    Audio flow:
    - Incoming: Gateway -> SRTP (decrypt) -> PCM -> WebSocket -> Browser
    - Outgoing: Browser -> WebSocket -> PCM -> SRTP (encrypt) -> Gateway

    BTicino uses G.711 A-law (PCMA) codec at 8kHz.
    """

    def __init__(
        self,
        local_port: int,
        remote_host: str,
        remote_port: int,
        decrypt_key: bytes,
        encrypt_key: bytes,
    ):
        """Initialize bidirectional audio."""
        self._local_port = local_port
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._decrypt_key = decrypt_key
        self._encrypt_key = encrypt_key

        self._recv_socket: socket.socket | None = None
        self._send_socket: socket.socket | None = None
        self._running = False
        self._recv_task: asyncio.Task | None = None
        self._on_audio_received: Callable[[bytes], None] | None = None

        self._send_seq = 0
        self._send_ssrc = int.from_bytes(os.urandom(4), 'big')
        self._send_timestamp = 0
        self._packets_received = 0
        self._packets_sent = 0

    async def start(self, on_audio_received: Callable[[bytes], None] | None = None):
        """Start audio handling."""
        self._on_audio_received = on_audio_received

        self._recv_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_socket.bind(('0.0.0.0', self._local_port))
        self._recv_socket.setblocking(False)

        self._send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._running = True
        self._recv_task = asyncio.create_task(self._receive_loop())

        _LOGGER.info("Bidirectional audio started: local=%d, remote=%s:%d",
                    self._local_port, self._remote_host, self._remote_port)

    async def stop(self):
        """Stop audio handling."""
        self._running = False

        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        if self._recv_socket:
            self._recv_socket.close()
            self._recv_socket = None
        if self._send_socket:
            self._send_socket.close()
            self._send_socket = None

        _LOGGER.info("Bidirectional audio stopped. Received: %d, Sent: %d packets",
                    self._packets_received, self._packets_sent)

    async def _receive_loop(self):
        """Receive and decrypt audio from intercom."""
        loop = asyncio.get_event_loop()
        last_log_time = 0

        while self._running:
            try:
                data, addr = await loop.sock_recvfrom(self._recv_socket, 2048)
                self._packets_received += 1

                import time
                now = time.time()
                if self._packets_received <= 5 or (now - last_log_time) > 10:
                    _LOGGER.debug("Audio packet #%d received: %d bytes from %s",
                                self._packets_received, len(data), addr)
                    last_log_time = now

                rtp_packet = self._decrypt_srtp(data)

                if rtp_packet and self._on_audio_received:
                    audio_payload = rtp_packet[12:]
                    self._on_audio_received(audio_payload)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    _LOGGER.debug("Audio receive error: %s", e)

    def _decrypt_srtp(self, srtp_packet: bytes) -> bytes | None:
        """Decrypt SRTP audio packet."""
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend

            if len(srtp_packet) < 22:
                return None

            if not self._decrypt_key or len(self._decrypt_key) < 30:
                return srtp_packet[:-10] if len(srtp_packet) > 10 else srtp_packet

            rtp_header = srtp_packet[:12]
            auth_tag_len = 10
            encrypted_payload = srtp_packet[12:-auth_tag_len]

            seq_num = int.from_bytes(rtp_header[2:4], 'big')
            ssrc = int.from_bytes(rtp_header[8:12], 'big')

            session_key = self._decrypt_key[:16]
            master_salt = self._decrypt_key[16:30]

            packet_index = seq_num
            ssrc_bytes = ssrc.to_bytes(4, 'big')
            packet_index_bytes = packet_index.to_bytes(8, 'big')
            iv_input = bytes(2) + ssrc_bytes + packet_index_bytes
            iv = bytes(a ^ b for a, b in zip(master_salt, iv_input))
            counter_bytes = iv + bytes(2)

            cipher = Cipher(
                algorithms.AES(session_key),
                modes.CTR(counter_bytes),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            decrypted_payload = decryptor.update(encrypted_payload) + decryptor.finalize()

            return rtp_header + decrypted_payload

        except Exception as e:
            _LOGGER.debug("Audio SRTP decryption error: %s", e)
            return None

    async def send_audio(self, pcm_data: bytes):
        """Send audio to intercom."""
        if not self._send_socket or not self._remote_host:
            return

        try:
            rtp_packet = self._build_rtp_packet(pcm_data)
            srtp_packet = self._encrypt_srtp(rtp_packet)

            if srtp_packet:
                self._send_socket.sendto(srtp_packet, (self._remote_host, self._remote_port))
                self._packets_sent += 1

        except Exception as e:
            _LOGGER.debug("Audio send error: %s", e)

    def _build_rtp_packet(self, payload: bytes) -> bytes:
        """Build RTP packet for audio."""
        self._send_seq = (self._send_seq + 1) & 0xFFFF
        self._send_timestamp = (self._send_timestamp + len(payload)) & 0xFFFFFFFF

        header = bytes([0x80, 0x08])
        header += self._send_seq.to_bytes(2, 'big')
        header += self._send_timestamp.to_bytes(4, 'big')
        header += self._send_ssrc.to_bytes(4, 'big')

        return header + payload

    def _encrypt_srtp(self, rtp_packet: bytes) -> bytes | None:
        """Encrypt RTP to SRTP."""
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            import hmac
            import hashlib

            if not self._encrypt_key or len(self._encrypt_key) < 30:
                return rtp_packet + bytes(10)

            rtp_header = rtp_packet[:12]
            payload = rtp_packet[12:]

            seq_num = int.from_bytes(rtp_header[2:4], 'big')
            ssrc = int.from_bytes(rtp_header[8:12], 'big')

            session_key = self._encrypt_key[:16]
            master_salt = self._encrypt_key[16:30]

            packet_index = seq_num
            ssrc_bytes = ssrc.to_bytes(4, 'big')
            packet_index_bytes = packet_index.to_bytes(8, 'big')
            iv_input = bytes(2) + ssrc_bytes + packet_index_bytes
            iv = bytes(a ^ b for a, b in zip(master_salt, iv_input))
            counter_bytes = iv + bytes(2)

            cipher = Cipher(
                algorithms.AES(session_key),
                modes.CTR(counter_bytes),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()
            encrypted_payload = encryptor.update(payload) + encryptor.finalize()

            srtp_packet = rtp_header + encrypted_payload

            auth_key = session_key
            h = hmac.new(auth_key, srtp_packet, hashlib.sha1)
            auth_tag = h.digest()[:10]

            return srtp_packet + auth_tag

        except Exception as e:
            _LOGGER.debug("Audio SRTP encryption error: %s", e)
            return None

    @property
    def is_running(self) -> bool:
        """Check if audio is running."""
        return self._running


class MediaProxyManager:
    """Manages media proxy for BTicino intercom via go2rtc.

    Provides WebRTC streaming with ~1-2s latency through go2rtc.
    """

    def __init__(
        self,
        config_dir: str = "/config/bticino_hometouch",
        go2rtc_api_port: int = 1984,
        go2rtc_rtsp_port: int = 8554,
    ):
        """Initialize media proxy manager."""
        self._config_dir = Path(config_dir)
        self._go2rtc = Go2RTCProxy(
            api_port=go2rtc_api_port,
            rtsp_port=go2rtc_rtsp_port,
            config_dir=config_dir,
        )
        self._converters: dict[str, SRTPtoRTSP] = {}
        self._audio_handlers: dict[str, BidirectionalAudio] = {}
        self._active_streams: dict[str, dict] = {}

    async def start(self) -> bool:
        """Start media proxy. Returns False if go2rtc is not available."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._config_dir.mkdir(parents=True, exist_ok=True)
        )

        if not await self._go2rtc.start():
            _LOGGER.error("go2rtc is required for video streaming but was not found")
            return False

        _LOGGER.info("Media proxy started (WebRTC mode via go2rtc)")
        return True

    async def stop(self):
        """Stop media proxy."""
        for converter in self._converters.values():
            await converter.stop()

        for audio in self._audio_handlers.values():
            await audio.stop()

        await self._go2rtc.stop()

        self._converters.clear()
        self._audio_handlers.clear()
        self._active_streams.clear()

    def _create_sdp_content(self, srtp_port: int, srtp_key: bytes, stream_name: str) -> str:
        """Create SDP content for SRTP input."""
        import base64
        key_b64 = base64.b64encode(srtp_key).decode()
        return f"""v=0
o=- 0 0 IN IP4 127.0.0.1
s={stream_name}
c=IN IP4 127.0.0.1
t=0 0
m=video {srtp_port} RTP/SAVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1
a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{key_b64}
"""

    def _write_sdp_file(self, path: str, content: str) -> None:
        """Write SDP file synchronously."""
        with open(path, 'w') as f:
            f.write(content)

    async def setup_camera_stream(
        self,
        camera_id: int,
        srtp_port: int,
        srtp_key: bytes,
        srtp_salt: bytes | None = None,
    ) -> str:
        """Set up camera stream via go2rtc.

        Architecture:
        1. Streams are pre-configured in go2rtc.yaml (bticino_live_1, bticino_live_2, etc.)
        2. FFmpeg runs as subprocess, decrypts SRTP and pushes RTSP to go2rtc
        3. go2rtc receives the RTSP stream on its RTSP server port via ANNOUNCE
        4. go2rtc serves WebRTC to browsers

        IMPORTANT: go2rtc.yaml must have empty streams pre-configured:
          streams:
            bticino_live_1: []
            bticino_live_2: []
            etc.

        Args:
            camera_id: Camera identifier
            srtp_port: Local port to receive SRTP packets on
            srtp_key: SRTP decryption key from SDP
            srtp_salt: Optional SRTP salt (usually embedded in key)

        Returns:
            Stream name for WebRTC connection
        """
        camera_name = f"bticino_live_{camera_id}"

        _LOGGER.info("Setting up camera %d stream: srtp_port=%d, key=%d bytes",
                    camera_id, srtp_port, len(srtp_key) if srtp_key else 0)

        # Stop any existing converter
        if camera_name in self._converters:
            await self._converters[camera_name].stop()
            del self._converters[camera_name]

        # RTSP URL where FFmpeg will push and go2rtc will receive
        rtsp_url = f"rtsp://127.0.0.1:{self._go2rtc._rtsp_port}/{camera_name}"

        # Verify stream exists in go2rtc (should be pre-configured in YAML)
        stream_exists = False
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._go2rtc._api_base_url}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        streams = await resp.json()
                        stream_exists = camera_name in streams
                        if stream_exists:
                            _LOGGER.info("Stream %s found in go2rtc (pre-configured)", camera_name)
                        else:
                            _LOGGER.warning("Stream %s NOT found in go2rtc! Add to go2rtc.yaml: %s: []",
                                          camera_name, camera_name)
        except Exception as e:
            _LOGGER.warning("Could not check streams in go2rtc: %s", e)

        # Start FFmpeg - go2rtc stream should be pre-configured to accept RTSP push
        converter = SRTPtoRTSP(
            srtp_port=srtp_port,
            srtp_key=srtp_key,
            stream_name=camera_name,
        )
        await converter.start()
        self._converters[camera_name] = converter

        # Give FFmpeg time to connect to go2rtc
        await asyncio.sleep(0.5)

        self._active_streams[camera_name] = {
            "stream_name": camera_name,
            "camera_id": camera_id,
            "srtp_port": srtp_port,
            "rtsp_url": rtsp_url,
            "go2rtc_preconfigured": stream_exists,
        }

        _LOGGER.info("Camera %d WebRTC stream ready: SRTP:%d -> FFmpeg -> RTSP -> go2rtc -> WebRTC (preconfigured=%s)",
                    camera_id, srtp_port, stream_exists)
        return camera_name

    async def setup_audio(
        self,
        call_id: str,
        local_port: int,
        remote_host: str,
        remote_port: int,
        decrypt_key: bytes,
        encrypt_key: bytes,
    ) -> BidirectionalAudio:
        """Set up bidirectional audio for a call."""
        audio = BidirectionalAudio(
            local_port=local_port,
            remote_host=remote_host,
            remote_port=remote_port,
            decrypt_key=decrypt_key,
            encrypt_key=encrypt_key,
        )
        await audio.start()
        self._audio_handlers[call_id] = audio
        return audio

    async def teardown_stream(self, camera_name: str):
        """Tear down a camera stream.

        Note: We don't remove the stream from go2rtc because it's pre-configured
        in go2rtc.yaml. We only stop FFmpeg - the stream stays ready for next call.
        """
        if camera_name in self._converters:
            await self._converters[camera_name].stop()
            del self._converters[camera_name]
            _LOGGER.info("Stopped FFmpeg for %s", camera_name)

        if camera_name in self._active_streams:
            del self._active_streams[camera_name]

    async def teardown_audio(self, call_id: str):
        """Tear down audio for a call."""
        if call_id in self._audio_handlers:
            await self._audio_handlers[call_id].stop()
            del self._audio_handlers[call_id]

    def get_stream_info(self, camera_name: str) -> dict | None:
        """Get stream info for a camera."""
        return self._active_streams.get(camera_name)

    def is_webrtc_mode(self) -> bool:
        """Check if WebRTC mode is active (always True if go2rtc connected)."""
        return self._go2rtc.is_running

    def get_stream_name(self, camera_id: int) -> str:
        """Get stream name for a camera."""
        return f"bticino_live_{camera_id}"

    def get_rtsp_url(self, camera_id: int) -> str:
        """Get RTSP URL for a camera."""
        return self._go2rtc.get_rtsp_url(f"bticino_live_{camera_id}")

    def get_webrtc_url(self, camera_id: int) -> str:
        """Get WebRTC signaling URL for a camera."""
        if self._go2rtc._api_base_url:
            return f"{self._go2rtc._api_base_url}/api/webrtc?src=bticino_live_{camera_id}"
        return ""

    def get_go2rtc_api_url(self) -> str:
        """Get go2rtc API base URL."""
        return self._go2rtc._api_base_url or ""

    async def webrtc_offer(self, camera_id: int, sdp_offer: str) -> str | None:
        """Process WebRTC offer and return answer."""
        stream_name = f"bticino_live_{camera_id}"
        return await self._go2rtc.webrtc_offer(stream_name, sdp_offer)

    async def is_stream_active(self, camera_id: int) -> bool:
        """Check if a stream is active."""
        camera_name = f"bticino_live_{camera_id}"

        converter = self._converters.get(camera_name)
        if not converter or not converter.is_running:
            return False

        return await self._go2rtc.check_stream_active(camera_name)
