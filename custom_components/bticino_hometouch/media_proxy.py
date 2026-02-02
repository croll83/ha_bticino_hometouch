"""SRTP to RTSP proxy for BTicino Hometouch using go2rtc integration."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import os
import json
import socket
from dataclasses import dataclass
from typing import Callable, Any
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


@dataclass
class AudioConfig:
    """Configuration for bidirectional audio."""
    recv_port: int
    send_port: int
    srtp_key: bytes
    codec: str = "pcma"  # G.711 A-law
    sample_rate: int = 8000


class Go2RTCProxy:
    """Connects to go2rtc for SRTP to RTSP conversion.

    Since Home Assistant 2024.11, go2rtc is built-in and runs in the same
    container as HA Core. This means:
    - API is on localhost:1984
    - RTP ports (10001-10010) are directly accessible
    - No Docker container isolation issues

    We prefer the built-in go2rtc (localhost) over the add-on.
    """

    # Prefer localhost (built-in go2rtc) first, then add-on
    ADDON_HOSTNAMES = [
        "127.0.0.1",         # Built-in go2rtc (HA 2024.11+) - PREFERRED
        "localhost",         # Same as above
        "a889bffc-go2rtc",   # Add-on hostname (fallback)
    ]

    def __init__(
        self,
        api_port: int = 1984,
        rtsp_port: int = 8554,
        webrtc_port: int = 8555,
        addon_hostname: str | None = None,
        config_dir: str = "/config/bticino_hometouch",
        **kwargs,  # Accept but ignore extra args for backward compatibility
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
        """Connect to go2rtc add-on."""
        import aiohttp

        # Try to find the go2rtc add-on
        for hostname in ([self._addon_hostname] if self._addon_hostname else self.ADDON_HOSTNAMES):
            api_url = f"http://{hostname}:{self._api_port}"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{api_url}/api", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            self._api_base_url = api_url
                            self._running = True
                            _LOGGER.info("Connected to go2rtc add-on at %s", api_url)
                            return True
            except Exception as e:
                _LOGGER.debug("go2rtc not found at %s: %s", hostname, e)
                continue

        _LOGGER.warning(
            "go2rtc add-on not found. Please install the go2rtc add-on from "
            "Settings -> Add-ons -> Add-on Store -> go2rtc"
        )
        return False

    async def stop(self):
        """Disconnect from go2rtc add-on."""
        self._running = False
        self._api_base_url = None
        self._streams.clear()

    def _create_sdp_file(self, stream: StreamConfig) -> str:
        """Create an SDP file for ffmpeg to receive RTP.

        Returns the path to the SDP file.
        """
        # Create SDP content that tells ffmpeg how to receive the RTP stream
        # This is a minimal SDP for H.264 video over RTP
        sdp_content = f"""v=0
o=- 0 0 IN IP4 127.0.0.1
s=BTicino Camera {stream.camera_id}
c=IN IP4 127.0.0.1
t=0 0
m=video {stream.rtp_port} RTP/AVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1
a=recvonly
"""
        # Write SDP file
        sdp_path = self._config_dir / f"camera_{stream.camera_id}.sdp"
        sdp_path.parent.mkdir(parents=True, exist_ok=True)
        sdp_path.write_text(sdp_content)
        _LOGGER.info("Created SDP file: %s", sdp_path)
        return str(sdp_path)

    def _build_stream_source(self, stream: StreamConfig, srtp_key: bytes | None = None) -> str:
        """Build stream source URL for go2rtc.

        Note: We use ffmpeg: source with an SDP file. The SDP tells ffmpeg
        how to receive the RTP stream (codec, port, etc.).

        The SRTP decryption is handled by a separate decryptor that
        forwards plain RTP to the port specified in the SDP.
        """
        # Create SDP file for this stream
        sdp_path = self._create_sdp_file(stream)

        # Use ffmpeg with SDP file as input
        # The SDP file tells ffmpeg to listen on the specified port for RTP
        # Note: In Home Assistant add-ons, /config is accessible
        return f"ffmpeg:{sdp_path}#video={stream.codec}"

    async def add_stream(
        self,
        name: str,
        stream: StreamConfig,
    ) -> str:
        """Pre-warm the go2rtc stream so ffmpeg starts listening.

        go2rtc with ffmpeg:rtp:// source is lazy - it doesn't start ffmpeg
        until someone requests the stream. We "warm" it by requesting the
        RTSP stream briefly.
        """
        if not self._running or not self._api_base_url:
            _LOGGER.error("go2rtc not connected")
            return ""

        self._streams[name] = stream

        # Check if stream exists
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._api_base_url}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        streams_data = await resp.json()
                        if name in streams_data:
                            _LOGGER.info("Stream %s exists in go2rtc, warming it up", name)

                            # Warm up by requesting the stream info via API
                            # GET /api/streams?src=name starts the producer
                            async with session.get(
                                f"{self._api_base_url}/api/streams",
                                params={"src": name},
                                timeout=aiohttp.ClientTimeout(total=5)
                            ) as warm_resp:
                                resp_data = await warm_resp.text()
                                _LOGGER.info("Warmed up stream %s (status=%d): %s",
                                            name, warm_resp.status, resp_data[:100] if resp_data else "empty")

                            # Give ffmpeg time to start
                            await asyncio.sleep(1)

                            return self.get_rtsp_url(name)
        except Exception as e:
            _LOGGER.debug("Could not warm up stream: %s", e)

        _LOGGER.warning("Stream %s not found in go2rtc - please configure it in go2rtc.yaml", name)
        return self.get_rtsp_url(name)

    async def remove_stream(self, name: str):
        """Remove a stream via go2rtc API."""
        if name in self._streams:
            del self._streams[name]

        if not self._running or not self._api_base_url:
            return

        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self._api_base_url}/api/streams",
                    params={"name": name},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.info("Removed stream %s from go2rtc", name)
        except Exception as e:
            _LOGGER.debug("Failed to remove stream %s: %s", name, e)

    def get_rtsp_url(self, camera_name: str) -> str:
        """Get RTSP URL for a camera."""
        # Use the detected hostname (prefers localhost for built-in go2rtc)
        if self._api_base_url:
            # Extract hostname from API URL
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


class SRTPDecryptor:
    """Handles SRTP decryption and HLS generation via FFmpeg.

    This class:
    1. Receives SRTP packets from the BTicino gateway
    2. Decrypts them to plain RTP (or passthrough if already decrypted)
    3. Launches ffmpeg to receive RTP and generate HLS stream
    4. Forwards decrypted packets to ffmpeg's input port

    The HLS stream is written to /config/www/bticino/ so HA can serve it.
    """

    # HLS output directory (must be web-accessible)
    HLS_OUTPUT_DIR = "/config/www/bticino"

    def __init__(
        self,
        srtp_port: int,
        rtp_port: int,
        srtp_key: bytes,
        srtp_salt: bytes | None = None,
        passthrough: bool = False,  # For debugging: skip decryption
        stream_name: str = "",  # For logging purposes
    ):
        """Initialize SRTP decryptor."""
        self._srtp_port = srtp_port
        self._rtp_port = rtp_port  # Local port for ffmpeg to receive RTP
        self._srtp_key = srtp_key
        self._srtp_salt = srtp_salt or bytes(14)
        self._passthrough = passthrough
        self._stream_name = stream_name
        self._socket: socket.socket | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._ffmpeg_process: subprocess.Popen | None = None
        self._ffmpeg_task: asyncio.Task | None = None
        # Resolve go2rtc hostname once at init
        self._go2rtc_ip: str | None = None

    async def start(self):
        """Start SRTP to HLS conversion via ffmpeg.

        FFmpeg handles SRTP decryption natively - it listens directly on the
        SRTP port specified in the SDP file.

        We don't need our own socket or decrypt loop anymore.
        """
        try:
            # Create HLS output directory
            import os
            os.makedirs(self.HLS_OUTPUT_DIR, exist_ok=True)
            _LOGGER.info("HLS output directory: %s", self.HLS_OUTPUT_DIR)

            # Start ffmpeg - it will listen on self._srtp_port for SRTP
            await self._start_ffmpeg_hls()

            self._running = True

            _LOGGER.info("SRTP->HLS converter started: ffmpeg listening on port %d, key=%d bytes, stream=%s",
                        self._srtp_port,
                        len(self._srtp_key) if self._srtp_key else 0, self._stream_name)

        except Exception as e:
            _LOGGER.error("Failed to start SRTP->HLS converter on port %d: %s", self._srtp_port, e)
            import traceback
            _LOGGER.debug(traceback.format_exc())

    async def _start_ffmpeg_hls(self):
        """Start ffmpeg to receive SRTP directly and generate HLS stream.

        FFmpeg receives SRTP on a local UDP port, decrypts it using the provided
        key, and generates HLS segments that HA can serve via /local/bticino/

        This uses ffmpeg's native SRTP support instead of manual decryption.
        """
        import base64

        hls_path = f"{self.HLS_OUTPUT_DIR}/{self._stream_name}.m3u8"

        # Encode SRTP key for ffmpeg (base64)
        # The key from SDP is already in raw binary format (30 bytes: 16 key + 14 salt)
        srtp_key_b64 = base64.b64encode(self._srtp_key).decode('ascii') if self._srtp_key else ""

        _LOGGER.info("SRTP key for ffmpeg: %d bytes -> base64 %s",
                    len(self._srtp_key) if self._srtp_key else 0,
                    srtp_key_b64[:20] + "..." if len(srtp_key_b64) > 20 else srtp_key_b64)

        # SDP content that tells ffmpeg how to receive SRTP stream
        # We use RTP/SAVP profile for SRTP and include crypto attribute
        #
        # SRTP key exchange (SDP offer/answer):
        # - We (OFFERER) send OUR key in INVITE - server uses it to ENCRYPT packets to us
        # - Server (ANSWERER) sends ITS key in 200 OK - we would use it to ENCRYPT to server
        # - Since we're recvonly, we only RECEIVE video encrypted with OUR key
        # - Therefore, we DECRYPT using OUR key (the same one we sent in INVITE)
        sdp_content = f"""v=0
o=- 0 0 IN IP4 127.0.0.1
s=BTicino Camera
c=IN IP4 127.0.0.1
t=0 0
m=video {self._srtp_port} RTP/SAVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1
a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{srtp_key_b64}
a=recvonly
"""

        _LOGGER.info("SDP for ffmpeg:\n%s", sdp_content)

        # Write SDP to a temporary file
        sdp_path = f"/tmp/bticino_{self._stream_name}.sdp"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._write_sdp_file, sdp_path, sdp_content)
            _LOGGER.info("Created SRTP SDP file: %s (port=%d)", sdp_path, self._srtp_port)
        except Exception as e:
            _LOGGER.error("Failed to create SDP file: %s", e)
            return

        # Build ffmpeg command for HLS output
        # ffmpeg will handle SRTP decryption natively based on SDP crypto attribute
        #
        # Re-encode with x264 to fix corrupted frames from SRTP HMAC errors
        # This produces clean video even when some packets have decryption issues
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            # SRTP/RTP input configuration
            "-protocol_whitelist", "file,udp,rtp,srtp,crypto",
            "-fflags", "+genpts+discardcorrupt",
            "-analyzeduration", "2000000",  # 2 second analysis
            "-probesize", "2000000",
            "-reorder_queue_size", "500",
            # Error resilience for corrupted SRTP packets
            "-err_detect", "ignore_err",
            # Input: SDP file that points to SRTP port
            "-i", sdp_path,
            # Re-encode to fix corrupted frames
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-crf", "23",
            "-g", "30",  # Keyframe every 30 frames
            "-an",  # No audio
            "-f", "hls",
            "-hls_time", "4",  # 4 second segments (was working well)
            "-hls_list_size", "10",  # Keep 10 segments in playlist
            "-hls_flags", "delete_segments+append_list",  # Delete old segments normally
            "-hls_segment_filename", f"{self.HLS_OUTPUT_DIR}/{self._stream_name}_%03d.ts",
            hls_path,
        ]

        _LOGGER.info("Starting ffmpeg for HLS: RTP port %d -> %s", self._rtp_port, hls_path)
        _LOGGER.debug("FFmpeg command: %s", " ".join(ffmpeg_cmd))

        try:
            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._ffmpeg_task = asyncio.create_task(self._monitor_ffmpeg())
            _LOGGER.info("FFmpeg HLS started with PID %d", self._ffmpeg_process.pid)
        except FileNotFoundError:
            _LOGGER.error("ffmpeg not found. Please install ffmpeg.")
        except Exception as e:
            _LOGGER.error("Failed to start ffmpeg: %s", e)
            import traceback
            _LOGGER.debug(traceback.format_exc())

    async def _start_ffmpeg(self):
        """Start ffmpeg to receive RTP and push to go2rtc via RTSP.

        FFmpeg receives plain RTP on a local UDP port and re-publishes it
        to go2rtc's RTSP server. This way go2rtc has a persistent stream source.

        We write an SDP file first, then use it as ffmpeg input. This is more
        reliable than pipe input for RTP streams.
        """
        rtsp_url = f"rtsp://{self._go2rtc_ip}:{self.GO2RTC_RTSP_PORT}/{self._stream_name}"

        # SDP content that tells ffmpeg how to receive the RTP stream
        # This describes the H.264 video stream coming on the local port
        sdp_content = f"""v=0
o=- 0 0 IN IP4 127.0.0.1
s=BTicino Camera
c=IN IP4 127.0.0.1
t=0 0
m=video {self._rtp_port} RTP/AVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1
a=recvonly
"""

        # Write SDP to a temporary file (async to avoid blocking event loop)
        sdp_path = f"/tmp/bticino_{self._stream_name}.sdp"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._write_sdp_file, sdp_path, sdp_content)
            _LOGGER.info("Created SDP file: %s", sdp_path)
        except Exception as e:
            _LOGGER.error("Failed to create SDP file: %s", e)
            return

        # Build ffmpeg command
        # Input: RTP stream via SDP file
        # Output: RTSP push to go2rtc
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "info",  # Use info level to see what's happening
            # RTP input configuration
            "-protocol_whitelist", "file,udp,rtp",
            "-fflags", "+genpts+discardcorrupt",
            "-analyzeduration", "1000000",  # Analyze for 1 second max
            "-probesize", "1000000",  # 1MB probe size
            "-reorder_queue_size", "500",
            "-max_delay", "500000",  # 500ms max delay
            # Input: SDP file that points to local UDP port
            "-i", sdp_path,
            # Output configuration
            "-c:v", "copy",  # No re-encoding
            "-an",  # No audio for now
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            rtsp_url,
        ]

        _LOGGER.info("Starting ffmpeg to receive RTP on port %d and push to %s",
                    self._rtp_port, rtsp_url)
        _LOGGER.info("FFmpeg command: %s", " ".join(ffmpeg_cmd))

        try:
            # Start ffmpeg process
            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Start task to monitor ffmpeg output
            self._ffmpeg_task = asyncio.create_task(self._monitor_ffmpeg())

            _LOGGER.info("FFmpeg started with PID %d", self._ffmpeg_process.pid)

        except FileNotFoundError:
            _LOGGER.error("ffmpeg not found. Please install ffmpeg.")
        except Exception as e:
            _LOGGER.error("Failed to start ffmpeg: %s", e)
            import traceback
            _LOGGER.debug(traceback.format_exc())

    def _write_sdp_file(self, path: str, content: str) -> None:
        """Write SDP file synchronously (called via executor)."""
        with open(path, 'w') as f:
            f.write(content)

    async def _monitor_ffmpeg(self):
        """Monitor ffmpeg process output for errors."""
        if not self._ffmpeg_process:
            return

        loop = asyncio.get_event_loop()

        try:
            while self._running and self._ffmpeg_process.poll() is None:
                # Read stderr in a non-blocking way
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
        except Exception as e:
            _LOGGER.debug("FFmpeg monitor error: %s", e)

        if self._ffmpeg_process:
            return_code = self._ffmpeg_process.poll()
            if return_code is not None:
                _LOGGER.info("FFmpeg exited with code %d", return_code)

    async def _stop_ffmpeg(self):
        """Stop the ffmpeg process."""
        if self._ffmpeg_task:
            self._ffmpeg_task.cancel()
            try:
                await self._ffmpeg_task
            except asyncio.CancelledError:
                pass
            self._ffmpeg_task = None

        if self._ffmpeg_process:
            _LOGGER.info("Stopping ffmpeg (PID %d)", self._ffmpeg_process.pid)
            try:
                self._ffmpeg_process.terminate()
                # Give it time to gracefully shutdown
                await asyncio.sleep(0.5)
                if self._ffmpeg_process.poll() is None:
                    self._ffmpeg_process.kill()
            except Exception as e:
                _LOGGER.debug("Error stopping ffmpeg: %s", e)
            self._ffmpeg_process = None

    async def stop(self):
        """Stop ffmpeg. Files are NOT deleted here - they're deleted when next call starts."""
        self._running = False

        # Stop ffmpeg
        await self._stop_ffmpeg()

        # DO NOT delete HLS files here!
        # The browser might still be buffering/playing.
        # Files will be cleaned up when the NEXT call starts via cleanup_hls_files()
        _LOGGER.info("SRTP->HLS stopped for %s. HLS files preserved for playback.", self._stream_name)

    async def cleanup_hls_files_async(self):
        """Clean up HLS files asynchronously. Call this BEFORE starting a new stream."""
        try:
            await asyncio.to_thread(self._cleanup_hls_files)
            _LOGGER.info("Cleaned up old HLS files for %s", self._stream_name)
        except Exception as e:
            _LOGGER.debug("Error cleaning up HLS files: %s", e)

    def _cleanup_hls_files(self):
        """Clean up HLS files synchronously (called via asyncio.to_thread)."""
        import os
        import glob
        for f in glob.glob(f"{self.HLS_OUTPUT_DIR}/{self._stream_name}*"):
            try:
                os.remove(f)
                _LOGGER.debug("Removed HLS file: %s", f)
            except Exception as e:
                _LOGGER.debug("Could not remove %s: %s", f, e)

    async def _decrypt_loop(self):
        """Main decryption loop.

        Receives SRTP packets, decrypts them, and forwards to local ffmpeg.
        ffmpeg is listening on localhost:rtp_port for RTP packets.
        """
        loop = asyncio.get_event_loop()
        packet_count = 0
        last_log_time = 0

        # Create a persistent socket for forwarding to ffmpeg (localhost)
        forward_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        _LOGGER.info("SRTP decrypt loop started, listening on port %d, forwarding to localhost:%d (ffmpeg HLS)",
                    self._srtp_port, self._rtp_port)

        try:
            while self._running:
                try:
                    # Receive SRTP packet
                    data, addr = await loop.sock_recvfrom(self._socket, 2048)
                    packet_count += 1

                    # Log first few packets and then periodically
                    import time
                    now = time.time()
                    if packet_count <= 5 or (now - last_log_time) > 10:
                        _LOGGER.info("SRTP packet #%d received from %s: %d bytes",
                                    packet_count, addr, len(data))
                        last_log_time = now

                    # Decrypt SRTP to RTP (or passthrough for debugging)
                    if self._passthrough:
                        rtp_packet = data  # Forward as-is
                    else:
                        rtp_packet = self._decrypt_srtp_packet(data)

                    if rtp_packet:
                        # Forward to local ffmpeg (listening on localhost:rtp_port)
                        forward_socket.sendto(rtp_packet, ("127.0.0.1", self._rtp_port))

                        if packet_count <= 5:
                            mode = "passthrough" if self._passthrough else "decrypted"
                            _LOGGER.info("Forwarded %s RTP packet #%d (%d bytes) to localhost:%d (ffmpeg)",
                                        mode, packet_count, len(rtp_packet), self._rtp_port)
                    else:
                        if packet_count <= 10:
                            _LOGGER.warning("SRTP decryption returned None for packet #%d", packet_count)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if self._running:
                        _LOGGER.warning("SRTP decrypt error: %s", e)
        finally:
            forward_socket.close()

        _LOGGER.info("SRTP decrypt loop ended, processed %d packets", packet_count)

    def _decrypt_srtp_packet(self, srtp_packet: bytes) -> bytes | None:
        """Decrypt a single SRTP packet to RTP.

        SRTP packet format:
        - RTP header (12+ bytes)
        - Encrypted payload
        - Authentication tag (10 bytes for default profile)

        Note: This is a simplified implementation. Full SRTP requires:
        - Session key derivation
        - Replay protection
        - ROC (Roll-Over Counter) handling
        """
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend

            if len(srtp_packet) < 22:  # Minimum: 12 header + 10 auth tag
                _LOGGER.debug("SRTP packet too small: %d bytes", len(srtp_packet))
                return None

            # Check if we have a valid SRTP key
            if not self._srtp_key or len(self._srtp_key) < 16:
                _LOGGER.warning("Invalid or missing SRTP key (len=%d), passing through as-is",
                              len(self._srtp_key) if self._srtp_key else 0)
                # Try forwarding the packet as-is (maybe it's already decrypted or no encryption)
                return srtp_packet

            # Extract components
            rtp_header = srtp_packet[:12]
            auth_tag_len = 10  # Default SRTP auth tag length
            encrypted_payload = srtp_packet[12:-auth_tag_len]
            auth_tag = srtp_packet[-auth_tag_len:]

            # Extract sequence number and SSRC from RTP header
            seq_num = int.from_bytes(rtp_header[2:4], 'big')
            ssrc = int.from_bytes(rtp_header[8:12], 'big')

            # The SRTP key from SDP is: 16 bytes key + 14 bytes salt
            # For AES_CM_128_HMAC_SHA1_80: 30 bytes total (16 key + 14 salt)
            if len(self._srtp_key) >= 30:
                session_key = self._srtp_key[:16]  # AES-128 key
                master_salt = self._srtp_key[16:30]  # 14-byte salt
            else:
                session_key = self._srtp_key[:16]  # AES-128
                master_salt = self._srtp_salt if self._srtp_salt else bytes(14)

            # Build IV according to RFC 3711 Section 4.1.1
            # IV = (salt XOR (SSRC || packet_index)) << 16
            # packet_index = ROC * 2^16 + SEQ (we simplify by ignoring ROC)
            packet_index = seq_num

            # Create the IV/counter for AES-CTR
            # Format: salt[0..13] XOR (ssrc || packet_index) || counter
            ssrc_bytes = ssrc.to_bytes(4, 'big')
            packet_index_bytes = packet_index.to_bytes(8, 'big')  # 48-bit index padded to 8 bytes

            # XOR salt with (label || ssrc || packet_index)
            # For RTP, label = 0x00
            iv_input = bytes(2) + ssrc_bytes + packet_index_bytes  # 14 bytes
            iv = bytes(a ^ b for a, b in zip(master_salt, iv_input))

            # Counter block is IV || 0x0000 (16 bytes total)
            counter_bytes = iv + bytes(2)

            # Decrypt using AES-CTR
            cipher = Cipher(
                algorithms.AES(session_key),
                modes.CTR(counter_bytes),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            decrypted_payload = decryptor.update(encrypted_payload) + decryptor.finalize()

            # Return RTP packet (header + decrypted payload)
            return rtp_header + decrypted_payload

        except Exception as e:
            _LOGGER.warning("SRTP decryption failed: %s", e)
            import traceback
            _LOGGER.debug(traceback.format_exc())
            # On error, try forwarding as-is
            return srtp_packet


class BidirectionalAudio:
    """Handles bidirectional SRTP audio for intercom.

    Audio flow:
    - Incoming: Gateway -> SRTP (decrypt with server's key) -> PCM -> WebSocket -> Browser
    - Outgoing: Browser -> WebSocket -> PCM -> SRTP (encrypt with our key) -> Gateway

    BTicino uses G.711 A-law (PCMA) codec at 8kHz.
    """

    def __init__(
        self,
        local_port: int,
        remote_host: str,
        remote_port: int,
        decrypt_key: bytes,  # Server's key - to decrypt incoming audio
        encrypt_key: bytes,  # Our key - to encrypt outgoing audio
    ):
        """Initialize bidirectional audio.

        Args:
            local_port: Local UDP port to receive audio from gateway
            remote_host: Gateway IP address
            remote_port: Gateway audio port (from SDP)
            decrypt_key: Server's SRTP key (from 200 OK) for decrypting incoming audio
            encrypt_key: Our SRTP key (from INVITE) for encrypting outgoing audio
        """
        self._local_port = local_port
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._decrypt_key = decrypt_key  # 30 bytes: 16 key + 14 salt
        self._encrypt_key = encrypt_key  # 30 bytes: 16 key + 14 salt

        self._recv_socket: socket.socket | None = None
        self._send_socket: socket.socket | None = None
        self._running = False
        self._recv_task: asyncio.Task | None = None

        # Callbacks
        self._on_audio_received: Callable[[bytes], None] | None = None

        # RTP sequence number and SSRC for outgoing packets
        self._send_seq = 0
        self._send_ssrc = int.from_bytes(os.urandom(4), 'big')
        self._send_timestamp = 0

        # Statistics
        self._packets_received = 0
        self._packets_sent = 0

    async def start(
        self,
        on_audio_received: Callable[[bytes], None] | None = None,
    ):
        """Start audio handling.

        Args:
            on_audio_received: Callback for decoded audio (PCM a-law bytes)
        """
        self._on_audio_received = on_audio_received

        # Create receive socket
        self._recv_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_socket.bind(('0.0.0.0', self._local_port))
        self._recv_socket.setblocking(False)

        # Create send socket
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

                # Log periodically
                import time
                now = time.time()
                if self._packets_received <= 5 or (now - last_log_time) > 10:
                    _LOGGER.debug("Audio packet #%d received: %d bytes from %s",
                                self._packets_received, len(data), addr)
                    last_log_time = now

                # Decrypt SRTP to RTP
                rtp_packet = self._decrypt_srtp(data)

                if rtp_packet and self._on_audio_received:
                    # Extract audio payload (skip 12-byte RTP header)
                    audio_payload = rtp_packet[12:]
                    self._on_audio_received(audio_payload)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    _LOGGER.debug("Audio receive error: %s", e)

    def _decrypt_srtp(self, srtp_packet: bytes) -> bytes | None:
        """Decrypt SRTP audio packet using AES-CM.

        Uses the SERVER's key (from 200 OK) for decryption.
        """
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend

            if len(srtp_packet) < 22:  # Min: 12 header + 10 auth tag
                return None

            if not self._decrypt_key or len(self._decrypt_key) < 30:
                # No key, return payload as-is (for debugging)
                return srtp_packet[:-10] if len(srtp_packet) > 10 else srtp_packet

            # Extract components
            rtp_header = srtp_packet[:12]
            auth_tag_len = 10
            encrypted_payload = srtp_packet[12:-auth_tag_len]

            # Extract sequence number and SSRC
            seq_num = int.from_bytes(rtp_header[2:4], 'big')
            ssrc = int.from_bytes(rtp_header[8:12], 'big')

            # Key derivation
            session_key = self._decrypt_key[:16]
            master_salt = self._decrypt_key[16:30]

            # Build IV for AES-CTR
            packet_index = seq_num
            ssrc_bytes = ssrc.to_bytes(4, 'big')
            packet_index_bytes = packet_index.to_bytes(8, 'big')
            iv_input = bytes(2) + ssrc_bytes + packet_index_bytes
            iv = bytes(a ^ b for a, b in zip(master_salt, iv_input))
            counter_bytes = iv + bytes(2)

            # Decrypt
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
        """Send audio to intercom.

        Args:
            pcm_data: G.711 A-law encoded audio (8kHz, mono)
        """
        if not self._send_socket or not self._remote_host:
            return

        try:
            # Build RTP packet
            rtp_packet = self._build_rtp_packet(pcm_data)

            # Encrypt to SRTP
            srtp_packet = self._encrypt_srtp(rtp_packet)

            if srtp_packet:
                self._send_socket.sendto(
                    srtp_packet,
                    (self._remote_host, self._remote_port)
                )
                self._packets_sent += 1

                if self._packets_sent <= 5:
                    _LOGGER.debug("Audio packet #%d sent: %d bytes to %s:%d",
                                self._packets_sent, len(srtp_packet),
                                self._remote_host, self._remote_port)

        except Exception as e:
            _LOGGER.debug("Audio send error: %s", e)

    def _build_rtp_packet(self, payload: bytes) -> bytes:
        """Build RTP packet for audio.

        RTP header (12 bytes):
        - Version (2), Padding (0), Extension (0), CSRC count (0): 1 byte = 0x80
        - Marker (0), Payload type (8 = PCMA): 1 byte = 0x08
        - Sequence number: 2 bytes
        - Timestamp: 4 bytes
        - SSRC: 4 bytes
        """
        # Increment sequence and timestamp
        self._send_seq = (self._send_seq + 1) & 0xFFFF
        self._send_timestamp = (self._send_timestamp + len(payload)) & 0xFFFFFFFF

        header = bytes([
            0x80,  # V=2, P=0, X=0, CC=0
            0x08,  # M=0, PT=8 (PCMA)
        ])
        header += self._send_seq.to_bytes(2, 'big')
        header += self._send_timestamp.to_bytes(4, 'big')
        header += self._send_ssrc.to_bytes(4, 'big')

        return header + payload

    def _encrypt_srtp(self, rtp_packet: bytes) -> bytes | None:
        """Encrypt RTP to SRTP using AES-CM.

        Uses OUR key (from INVITE) for encryption.
        """
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            import hmac
            import hashlib

            if not self._encrypt_key or len(self._encrypt_key) < 30:
                # No key, send as-is with dummy auth tag
                return rtp_packet + bytes(10)

            rtp_header = rtp_packet[:12]
            payload = rtp_packet[12:]

            # Extract sequence number and SSRC
            seq_num = int.from_bytes(rtp_header[2:4], 'big')
            ssrc = int.from_bytes(rtp_header[8:12], 'big')

            # Key derivation
            session_key = self._encrypt_key[:16]
            master_salt = self._encrypt_key[16:30]

            # Build IV for AES-CTR
            packet_index = seq_num
            ssrc_bytes = ssrc.to_bytes(4, 'big')
            packet_index_bytes = packet_index.to_bytes(8, 'big')
            iv_input = bytes(2) + ssrc_bytes + packet_index_bytes
            iv = bytes(a ^ b for a, b in zip(master_salt, iv_input))
            counter_bytes = iv + bytes(2)

            # Encrypt payload
            cipher = Cipher(
                algorithms.AES(session_key),
                modes.CTR(counter_bytes),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()
            encrypted_payload = encryptor.update(payload) + encryptor.finalize()

            # Build SRTP packet (header + encrypted payload)
            srtp_packet = rtp_header + encrypted_payload

            # Calculate HMAC-SHA1 authentication tag (10 bytes)
            # For simplicity, we use the master key directly (should use derived auth key)
            auth_key = session_key  # Simplified - should derive proper auth key
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
    """Manages all media proxy components for BTicino intercom."""

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
        self._srtp_decryptors: dict[str, SRTPDecryptor] = {}
        self._audio_handlers: dict[str, BidirectionalAudio] = {}
        self._active_streams: dict[str, dict] = {}

    async def start(self) -> bool:
        """Start media proxy.

        HLS mode: No go2rtc needed. We create HLS files directly via ffmpeg.
        The files are stored in /config/www/bticino/ and served by HA.
        """
        # Create config directory if needed
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._config_dir.mkdir(parents=True, exist_ok=True)
        )

        # Create HLS output directory
        import os
        hls_dir = "/config/www/bticino"
        await loop.run_in_executor(
            None,
            lambda: os.makedirs(hls_dir, exist_ok=True)
        )

        _LOGGER.info("Media proxy started (HLS mode). Output directory: %s", hls_dir)
        return True

    async def stop(self):
        """Stop media proxy."""
        # Stop all decryptors
        for decryptor in self._srtp_decryptors.values():
            await decryptor.stop()

        # Stop all audio handlers
        for audio in self._audio_handlers.values():
            await audio.stop()

        # Stop go2rtc
        await self._go2rtc.stop()

    async def setup_camera_stream(
        self,
        camera_id: int,
        srtp_port: int,
        srtp_key: bytes,
        srtp_salt: bytes | None = None,
    ) -> str:
        """Set up camera stream and return HLS URL.

        Args:
            camera_id: Camera identifier
            srtp_port: Local port to receive SRTP packets on
            srtp_key: SRTP decryption key from SDP
            srtp_salt: Optional SRTP salt (usually embedded in key)

        The setup creates:
        1. An SRTP decryptor listening on srtp_port
        2. The decryptor forwards plain RTP to a local ffmpeg
        3. FFmpeg generates HLS stream to /config/www/bticino/

        HLS approach: No go2rtc needed, ffmpeg generates HLS directly.
        HA serves HLS via /local/bticino/
        """
        camera_name = f"bticino_camera_{camera_id}"

        _LOGGER.info("Setting up camera %d HLS stream: srtp_port=%d, key=%d bytes",
                    camera_id, srtp_port, len(srtp_key) if srtp_key else 0)

        # Stop any existing stream for this camera first
        if camera_name in self._srtp_decryptors:
            _LOGGER.info("Stopping existing stream for %s before starting new one", camera_name)
            await self._srtp_decryptors[camera_name].stop()
            del self._srtp_decryptors[camera_name]

        # Clean up old HLS files AFTER stopping old stream, BEFORE starting new one
        await self._cleanup_old_hls_files(camera_name)

        # Local RTP port for decrypted stream (internal, no NAT needed)
        # ffmpeg listens on this port for RTP packets
        rtp_port = 10000 + camera_id

        # Create SRTP decryptor that listens on srtp_port and forwards to rtp_port
        # The decryptor starts ffmpeg to receive RTP and generate HLS
        # NOTE: passthrough=False means we decrypt SRTP to RTP before forwarding
        decryptor = SRTPDecryptor(
            srtp_port=srtp_port,
            rtp_port=rtp_port,
            srtp_key=srtp_key,
            srtp_salt=srtp_salt,
            stream_name=camera_name,
            passthrough=False,  # Decrypt SRTP to plain RTP for ffmpeg
        )
        await decryptor.start()
        self._srtp_decryptors[camera_name] = decryptor

        # HLS URL served by HA's built-in web server
        hls_url = f"/local/bticino/{camera_name}.m3u8"

        self._active_streams[camera_name] = {
            "hls_url": hls_url,
            "camera_id": camera_id,
            "srtp_port": srtp_port,
            "rtp_port": rtp_port,
        }

        _LOGGER.info("Camera %d HLS stream: SRTP on port %d -> RTP on port %d -> %s",
                    camera_id, srtp_port, rtp_port, hls_url)
        return hls_url

    async def setup_audio(
        self,
        call_id: str,
        local_port: int,
        remote_host: str,
        remote_port: int,
        srtp_key: bytes,
    ) -> BidirectionalAudio:
        """Set up bidirectional audio for a call."""
        audio = BidirectionalAudio(
            local_port=local_port,
            remote_host=remote_host,
            remote_port=remote_port,
            srtp_key=srtp_key,
        )
        await audio.start()
        self._audio_handlers[call_id] = audio
        return audio

    async def teardown_stream(self, camera_name: str):
        """Tear down a camera stream and clean up HLS files."""
        if camera_name in self._srtp_decryptors:
            await self._srtp_decryptors[camera_name].stop()
            del self._srtp_decryptors[camera_name]

        await self._go2rtc.remove_stream(camera_name)

        if camera_name in self._active_streams:
            del self._active_streams[camera_name]

        # Clean up HLS files after stream teardown
        # This triggers "file not found" error in browser, signaling stream end
        await self._cleanup_old_hls_files(camera_name)

    async def teardown_audio(self, call_id: str):
        """Tear down audio for a call."""
        if call_id in self._audio_handlers:
            await self._audio_handlers[call_id].stop()
            del self._audio_handlers[call_id]

    def get_stream_info(self, camera_name: str) -> dict | None:
        """Get stream info for a camera."""
        return self._active_streams.get(camera_name)

    def get_hls_url(self, camera_id: int) -> str:
        """Get HLS URL for a camera.

        Returns the URL path served by HA's built-in web server.
        The HLS files are in /config/www/bticino/ and served via /local/bticino/
        """
        return f"/local/bticino/bticino_camera_{camera_id}.m3u8"

    def get_rtsp_url(self, camera_id: int) -> str:
        """Get RTSP URL for a camera (legacy, returns HLS URL now)."""
        # For backward compatibility, return HLS URL
        return self.get_hls_url(camera_id)

    def get_webrtc_url(self, camera_id: int) -> str:
        """Get WebRTC URL for a camera (not supported in HLS mode)."""
        # WebRTC requires go2rtc, which we're not using in HLS mode
        return ""

    async def _cleanup_old_hls_files(self, camera_name: str):
        """Clean up old HLS files for a camera before starting new stream.

        This is called at the START of a new call, not at the end.
        This ensures files remain available during playback/buffering.
        """
        import os
        import glob

        hls_dir = "/config/www/bticino"
        pattern = f"{hls_dir}/{camera_name}*"

        try:
            def cleanup():
                files = glob.glob(pattern)
                count = 0
                for f in files:
                    try:
                        os.remove(f)
                        count += 1
                    except Exception as e:
                        _LOGGER.debug("Could not remove %s: %s", f, e)
                return count

            count = await asyncio.to_thread(cleanup)
            if count > 0:
                _LOGGER.info("Cleaned up %d old HLS files for %s", count, camera_name)
        except Exception as e:
            _LOGGER.debug("Error cleaning up old HLS files: %s", e)
