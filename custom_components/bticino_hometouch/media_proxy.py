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
    """Manages go2rtc for SRTP to RTSP conversion."""

    def __init__(
        self,
        go2rtc_path: str = "/config/go2rtc/go2rtc",
        config_dir: str = "/config/go2rtc",
        api_port: int = 1984,
        rtsp_port: int = 8554,
        webrtc_port: int = 8555,
    ):
        """Initialize go2rtc proxy."""
        self._go2rtc_path = go2rtc_path
        self._config_dir = Path(config_dir)
        self._api_port = api_port
        self._rtsp_port = rtsp_port
        self._webrtc_port = webrtc_port
        self._process: subprocess.Popen | None = None
        self._streams: dict[str, StreamConfig] = {}
        self._running = False

    async def start(self) -> bool:
        """Start go2rtc process."""
        try:
            # Create config directory
            self._config_dir.mkdir(parents=True, exist_ok=True)

            # Generate initial config
            await self._write_config()

            # Start go2rtc
            self._process = await asyncio.create_subprocess_exec(
                self._go2rtc_path,
                "-config", str(self._config_dir / "go2rtc.yaml"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            self._running = True
            _LOGGER.info("go2rtc started on RTSP port %d", self._rtsp_port)
            return True

        except Exception as e:
            _LOGGER.error("Failed to start go2rtc: %s", e)
            return False

    async def stop(self):
        """Stop go2rtc process."""
        self._running = False
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None

    async def _write_config(self):
        """Write go2rtc configuration file."""
        config = {
            "api": {
                "listen": f":{self._api_port}",
            },
            "rtsp": {
                "listen": f":{self._rtsp_port}",
            },
            "webrtc": {
                "listen": f":{self._webrtc_port}",
                "candidates": ["stun:stun.l.google.com:19302"],
            },
            "streams": {},
        }

        # Add configured streams
        for name, stream in self._streams.items():
            config["streams"][name] = self._build_stream_source(stream)

        config_path = self._config_dir / "go2rtc.yaml"
        with open(config_path, 'w') as f:
            import yaml
            yaml.dump(config, f, default_flow_style=False)

    def _build_stream_source(self, stream: StreamConfig) -> list[str]:
        """Build stream source URL for go2rtc."""
        # SRTP source with key
        key_hex = stream.srtp_key.hex()
        sources = [
            f"ffmpeg:srtp://127.0.0.1:{stream.rtp_port}?pkt_size=1316"
            f"#video={stream.codec}"
            f"#input=-protocol_whitelist file,udp,rtp,srtp -i srtp://127.0.0.1:{stream.rtp_port}"
        ]
        return sources

    async def add_stream(
        self,
        name: str,
        stream: StreamConfig,
    ) -> str:
        """Add a new stream and return RTSP URL."""
        self._streams[name] = stream

        # Update config and reload
        await self._write_config()
        await self._reload_config()

        return f"rtsp://localhost:{self._rtsp_port}/{name}"

    async def remove_stream(self, name: str):
        """Remove a stream."""
        if name in self._streams:
            del self._streams[name]
            await self._write_config()
            await self._reload_config()

    async def _reload_config(self):
        """Reload go2rtc configuration via API."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://localhost:{self._api_port}/api/config/reload"
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            _LOGGER.warning("Failed to reload go2rtc config: %s", e)
            return False

    def get_rtsp_url(self, camera_name: str) -> str:
        """Get RTSP URL for a camera."""
        return f"rtsp://localhost:{self._rtsp_port}/{camera_name}"

    def get_webrtc_url(self, camera_name: str) -> str:
        """Get WebRTC URL for a camera."""
        return f"http://localhost:{self._api_port}/api/ws?src={camera_name}"

    @property
    def is_running(self) -> bool:
        """Return True if go2rtc is running."""
        return self._running and self._process is not None


class SRTPDecryptor:
    """Handles SRTP decryption and forwarding to RTP."""

    def __init__(
        self,
        srtp_port: int,
        rtp_port: int,
        srtp_key: bytes,
        srtp_salt: bytes | None = None,
    ):
        """Initialize SRTP decryptor."""
        self._srtp_port = srtp_port
        self._rtp_port = rtp_port
        self._srtp_key = srtp_key
        self._srtp_salt = srtp_salt or bytes(14)
        self._socket: socket.socket | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start SRTP decryption."""
        try:
            # Create UDP socket for receiving SRTP
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.bind(('0.0.0.0', self._srtp_port))
            self._socket.setblocking(False)

            self._running = True
            self._task = asyncio.create_task(self._decrypt_loop())

            _LOGGER.info("SRTP decryptor started on port %d", self._srtp_port)

        except Exception as e:
            _LOGGER.error("Failed to start SRTP decryptor: %s", e)

    async def stop(self):
        """Stop SRTP decryption."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._socket:
            self._socket.close()

    async def _decrypt_loop(self):
        """Main decryption loop."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # Receive SRTP packet
                data, addr = await loop.sock_recvfrom(self._socket, 2048)

                # Decrypt SRTP to RTP
                rtp_packet = self._decrypt_srtp_packet(data)

                if rtp_packet:
                    # Forward to RTP port (for go2rtc)
                    forward_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    forward_socket.sendto(rtp_packet, ('127.0.0.1', self._rtp_port))
                    forward_socket.close()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    _LOGGER.debug("SRTP decrypt error: %s", e)

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
                return None

            # Extract components
            rtp_header = srtp_packet[:12]
            auth_tag_len = 10  # Default SRTP auth tag length
            encrypted_payload = srtp_packet[12:-auth_tag_len]
            auth_tag = srtp_packet[-auth_tag_len:]

            # Extract sequence number and SSRC from RTP header
            seq_num = int.from_bytes(rtp_header[2:4], 'big')
            ssrc = int.from_bytes(rtp_header[8:12], 'big')

            # Derive session keys (simplified - real implementation needs full KDF)
            # For BTicino, the key exchange happens during SIP/SDP negotiation
            session_key = self._srtp_key[:16]  # AES-128

            # Build IV (packet index || SSRC)
            # Simplified: use seq_num as packet index (ignoring ROC)
            packet_index = seq_num
            iv = self._srtp_salt + bytes(2)
            iv = bytes([iv[i] ^ ((ssrc >> (8 * (3 - i % 4))) & 0xFF) if i < 4 else iv[i]
                       for i in range(len(iv))])

            # For AES-CTR mode
            counter = int.from_bytes(iv[:14], 'big') << 16 | (packet_index << 16)
            counter_bytes = counter.to_bytes(16, 'big')

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
            _LOGGER.debug("SRTP decryption failed: %s", e)
            return None


class BidirectionalAudio:
    """Handles bidirectional audio for intercom."""

    def __init__(
        self,
        local_port: int = 5004,
        remote_host: str = "",
        remote_port: int = 0,
        srtp_key: bytes = b"",
    ):
        """Initialize bidirectional audio."""
        self._local_port = local_port
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._srtp_key = srtp_key
        self._recv_socket: socket.socket | None = None
        self._send_socket: socket.socket | None = None
        self._running = False
        self._audio_callback: Callable[[bytes], None] | None = None

    async def start(
        self,
        on_audio_received: Callable[[bytes], None] | None = None,
    ):
        """Start audio handling."""
        self._audio_callback = on_audio_received

        # Create receive socket
        self._recv_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_socket.bind(('0.0.0.0', self._local_port))
        self._recv_socket.setblocking(False)

        # Create send socket
        self._send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._running = True
        asyncio.create_task(self._receive_loop())

        _LOGGER.info("Bidirectional audio started on port %d", self._local_port)

    async def stop(self):
        """Stop audio handling."""
        self._running = False
        if self._recv_socket:
            self._recv_socket.close()
        if self._send_socket:
            self._send_socket.close()

    async def _receive_loop(self):
        """Receive audio from intercom."""
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                data, addr = await loop.sock_recvfrom(self._recv_socket, 2048)

                # Decrypt SRTP audio
                rtp_audio = self._decrypt_audio(data)

                if rtp_audio and self._audio_callback:
                    self._audio_callback(rtp_audio)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    _LOGGER.debug("Audio receive error: %s", e)

    def _decrypt_audio(self, srtp_packet: bytes) -> bytes | None:
        """Decrypt SRTP audio packet."""
        # Similar to video SRTP decryption
        # BTicino uses G.711 a-law (PCMA) codec
        try:
            if len(srtp_packet) < 22:
                return None

            rtp_header = srtp_packet[:12]
            auth_tag_len = 10
            encrypted_payload = srtp_packet[12:-auth_tag_len]

            # Simplified decryption (same as video)
            # Real implementation needs proper key derivation
            return rtp_header + encrypted_payload  # Placeholder

        except Exception:
            return None

    async def send_audio(self, audio_data: bytes):
        """Send audio to intercom."""
        if not self._send_socket or not self._remote_host:
            return

        try:
            # Encrypt audio to SRTP
            srtp_packet = self._encrypt_audio(audio_data)

            if srtp_packet:
                self._send_socket.sendto(
                    srtp_packet,
                    (self._remote_host, self._remote_port)
                )

        except Exception as e:
            _LOGGER.debug("Audio send error: %s", e)

    def _encrypt_audio(self, rtp_packet: bytes) -> bytes | None:
        """Encrypt RTP audio to SRTP."""
        # Reverse of decryption
        # Placeholder implementation
        return rtp_packet


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
            config_dir=str(self._config_dir / "go2rtc"),
            api_port=go2rtc_api_port,
            rtsp_port=go2rtc_rtsp_port,
        )
        self._srtp_decryptors: dict[str, SRTPDecryptor] = {}
        self._audio_handlers: dict[str, BidirectionalAudio] = {}
        self._active_streams: dict[str, dict] = {}

    async def start(self) -> bool:
        """Start media proxy."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

        # Start go2rtc
        if not await self._go2rtc.start():
            _LOGGER.error("Failed to start go2rtc")
            return False

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
        """Set up camera stream and return RTSP URL."""
        camera_name = f"bticino_camera_{camera_id}"

        # Allocate local RTP port for decrypted stream
        rtp_port = 10000 + camera_id * 2

        # Create SRTP decryptor
        decryptor = SRTPDecryptor(
            srtp_port=srtp_port,
            rtp_port=rtp_port,
            srtp_key=srtp_key,
            srtp_salt=srtp_salt,
        )
        await decryptor.start()
        self._srtp_decryptors[camera_name] = decryptor

        # Add stream to go2rtc
        stream_config = StreamConfig(
            camera_id=camera_id,
            rtp_port=rtp_port,
            rtcp_port=rtp_port + 1,
            srtp_key=srtp_key,
        )
        rtsp_url = await self._go2rtc.add_stream(camera_name, stream_config)

        self._active_streams[camera_name] = {
            "rtsp_url": rtsp_url,
            "webrtc_url": self._go2rtc.get_webrtc_url(camera_name),
            "camera_id": camera_id,
        }

        _LOGGER.info("Camera %d stream ready at %s", camera_id, rtsp_url)
        return rtsp_url

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
        """Tear down a camera stream."""
        if camera_name in self._srtp_decryptors:
            await self._srtp_decryptors[camera_name].stop()
            del self._srtp_decryptors[camera_name]

        await self._go2rtc.remove_stream(camera_name)

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

    def get_rtsp_url(self, camera_id: int) -> str:
        """Get RTSP URL for a camera."""
        return self._go2rtc.get_rtsp_url(f"bticino_camera_{camera_id}")

    def get_webrtc_url(self, camera_id: int) -> str:
        """Get WebRTC URL for a camera."""
        return self._go2rtc.get_webrtc_url(f"bticino_camera_{camera_id}")
