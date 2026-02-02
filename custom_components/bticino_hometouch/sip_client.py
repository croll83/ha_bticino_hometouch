"""SIP client for BTicino intercom using SRTP."""
from __future__ import annotations

import asyncio
import logging
import ssl
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Any
from enum import Enum
import hashlib
import random
import re

_LOGGER = logging.getLogger(__name__)


class CallState(Enum):
    """SIP call states."""
    IDLE = "idle"
    CALLING = "calling"
    INCOMING = "incoming"
    EARLY = "early"
    CONNECTED = "connected"
    HOLD = "hold"
    DISCONNECTED = "disconnected"
    NEEDS_AUTH = "needs_auth"  # Needs reconnection for authenticated INVITE


class RegistrationState(Enum):
    """SIP registration states."""
    UNREGISTERED = "unregistered"
    REGISTERING = "registering"
    REGISTERED = "registered"
    FAILED = "failed"


@dataclass
class SIPConfig:
    """SIP configuration."""
    server: str
    port: int
    username: str
    password: str
    domain: str
    gateway_address: str
    client_cert: str
    client_key: str
    ca_cert: str
    local_ip: str = ""
    local_port: int = 5060
    apartment_code: str = ""  # OpenWebNet apartment/unit address
    public_ip: str = ""  # Public IP for NAT traversal (used in SDP for media)


class MediaMode(Enum):
    """Media mode for SIP calls."""
    VIDEO_ONLY = "video_only"      # Only receive video (no audio)
    AUDIO_ONLY = "audio_only"      # Only bidirectional audio (no video)
    AUDIO_VIDEO = "audio_video"    # Both audio and video (default)


@dataclass
class SIPCall:
    """Represents a SIP call."""
    call_id: str
    state: CallState = CallState.IDLE
    remote_uri: str = ""
    local_tag: str = ""
    remote_tag: str = ""
    cseq: int = 1
    branch: str = ""
    from_header: str = ""
    to_header: str = ""
    contact: str = ""
    rtp_port: int = 0
    srtp_key: bytes = b""
    video_rtp_port: int = 0
    video_srtp_key: bytes = b""  # OUR key sent in INVITE - server encrypts video WITH this key, we DECRYPT with it
    video_srtp_key_remote: bytes = b""  # Server's key from 200 OK - unused for recvonly video
    audio_rtp_port: int = 0
    audio_srtp_key: bytes = b""  # OUR key sent in INVITE - server decrypts our audio with this
    audio_srtp_key_remote: bytes = b""  # Server's key from 200 OK - we decrypt server's audio with this
    remote_audio_port: int = 0  # Server's audio port from SDP
    remote_ip: str = ""  # Server's IP address from SDP
    devaddr: str = ""
    station_id: int = 0  # Station number (1-10) for fixed port allocation
    media_mode: MediaMode = MediaMode.VIDEO_ONLY  # Default to video-only for camera viewing


# Fixed port ranges for NAT traversal (user needs to forward these ports)
# Using same ports as Linphone official app for compatibility:
# Video: 9078 (Linphone default)
# Audio: 7076 (Linphone default)
# Note: These are SINGLE ports, not per-station, because BTicino only allows one call at a time
FIXED_VIDEO_PORT = 9078  # Same as Linphone linphonerc_factory
FIXED_AUDIO_PORT = 7076  # Same as Linphone linphonerc_factory


class SIPClient:
    """Pure Python SIP client with TLS support for BTicino intercom."""

    def __init__(
        self,
        config: SIPConfig,
        on_incoming_call: Callable[[SIPCall], None] | None = None,
        on_call_state_changed: Callable[[SIPCall, CallState], None] | None = None,
        on_video_frame: Callable[[bytes], None] | None = None,
    ):
        """Initialize SIP client."""
        self._config = config
        self._on_incoming_call = on_incoming_call
        self._on_call_state_changed = on_call_state_changed
        self._on_video_frame = on_video_frame

        self._registration_state = RegistrationState.UNREGISTERED
        self._calls: dict[str, SIPCall] = {}
        self._socket: ssl.SSLSocket | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._recv_task: asyncio.Task | None = None
        self._register_task: asyncio.Task | None = None
        self._intentional_disconnect = False  # Track if disconnect is intentional (for 407 auth)

        self._local_cseq = 1
        self._call_id_counter = 0
        self._registration_call_id = ""
        self._registration_cseq = 0
        self._auth_in_progress = False
        self._auth_attempts = 0

        # For MESSAGE auth handling
        self._message_auth_event: asyncio.Event | None = None
        self._message_response_code: int = 0
        self._proxy_realm: str = ""
        self._proxy_nonce: str = ""
        self._proxy_opaque: str = ""
        self._proxy_algorithm: str = "MD5"
        self._proxy_nc: int = 0  # nonce count

        # Determine local IP
        if not config.local_ip:
            self._config.local_ip = self._get_local_ip()

        # Fix username if it contains domain (legacy config)
        if "@" in self._config.username:
            _LOGGER.warning(
                "Username contains domain (%s), extracting user part only",
                self._config.username
            )
            self._config.username = self._config.username.split("@")[0]

    def _get_local_ip(self) -> str:
        """Get local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((self._config.server, self._config.port))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _generate_branch(self) -> str:
        """Generate a unique branch ID."""
        return f"z9hG4bK-{random.randint(100000, 999999)}-{int(time.time())}"

    def _generate_tag(self) -> str:
        """Generate a unique tag."""
        return f"{random.randint(100000, 999999)}"

    def _generate_call_id(self) -> str:
        """Generate a unique Call-ID."""
        self._call_id_counter += 1
        return f"{self._call_id_counter}-{int(time.time())}@{self._config.local_ip}"

    async def connect(self) -> bool:
        """Connect to SIP server with TLS."""
        import tempfile
        import os

        # Cancel any existing receive task first to avoid race conditions
        if self._recv_task and not self._recv_task.done():
            _LOGGER.debug("Cancelling existing receive task before reconnect")
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        # Close existing connection if any
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        cert_file = None
        key_file = None
        ca_file = None

        try:
            # Create SSL context in executor to avoid blocking calls
            loop = asyncio.get_event_loop()

            def _create_ssl_context():
                """Create SSL context with certificates (blocking operations)."""
                nonlocal cert_file, key_file, ca_file

                # Write temp cert files
                with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
                    f.write(self._config.client_cert)
                    cert_file = f.name

                with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
                    f.write(self._config.client_key)
                    key_file = f.name

                with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
                    f.write(self._config.ca_cert)
                    ca_file = f.name

                # Create SSL context
                ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_REQUIRED
                ctx.load_verify_locations(ca_file)
                ctx.load_cert_chain(cert_file, key_file)

                return ctx

            ssl_context = await loop.run_in_executor(None, _create_ssl_context)

            # Connect
            self._reader, self._writer = await asyncio.open_connection(
                self._config.server,
                self._config.port,
                ssl=ssl_context,
            )

            self._running = True
            self._intentional_disconnect = False  # Reset on new connection
            self._recv_task = asyncio.create_task(self._receive_loop())

            _LOGGER.info("Connected to SIP server %s:%d", self._config.server, self._config.port)
            return True

        except Exception as e:
            _LOGGER.error("Failed to connect to SIP server: %s", e)
            return False

        finally:
            # Clean up temp files
            for f in [cert_file, key_file, ca_file]:
                if f:
                    try:
                        os.unlink(f)
                    except OSError:
                        pass

    async def disconnect(self, intentional: bool = False):
        """Disconnect from SIP server.

        Args:
            intentional: If True, this is an intentional disconnect (e.g., for 407 auth)
                        and auto-reconnect should not be triggered.
        """
        self._intentional_disconnect = intentional
        self._running = False

        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass

        if self._register_task:
            self._register_task.cancel()
            try:
                await self._register_task
            except asyncio.CancelledError:
                pass

        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

        self._registration_state = RegistrationState.UNREGISTERED
        _LOGGER.info("Disconnected from SIP server")

    async def _auto_reconnect(self):
        """Automatically reconnect to SIP server after connection loss."""
        # Wait a bit before reconnecting
        await asyncio.sleep(3)

        if self._running:
            # Already running again, skip
            return

        _LOGGER.info("Attempting automatic reconnection to SIP server...")

        try:
            if await self.connect():
                if await self.register():
                    _LOGGER.info("Successfully reconnected and re-registered")
                else:
                    _LOGGER.error("Reconnected but failed to register")
            else:
                _LOGGER.error("Failed to reconnect to SIP server")
                # Try again after a longer delay
                await asyncio.sleep(10)
                asyncio.create_task(self._auto_reconnect())
        except Exception as e:
            _LOGGER.error("Error during auto-reconnect: %s", e)
            # Try again
            await asyncio.sleep(10)
            asyncio.create_task(self._auto_reconnect())

    async def register(self) -> bool:
        """Register with SIP server."""
        if self._registration_state == RegistrationState.REGISTERING:
            return False

        self._registration_state = RegistrationState.REGISTERING
        self._registration_call_id = self._generate_call_id()
        self._registration_cseq = 1

        # Build REGISTER request
        request = self._build_register_request()

        try:
            await self._send(request)
            return True
        except Exception as e:
            _LOGGER.error("Failed to send REGISTER: %s", e)
            self._registration_state = RegistrationState.FAILED
            return False

    def _build_register_request(self, auth: str = "") -> str:
        """Build a REGISTER request."""
        branch = self._generate_branch()
        tag = self._generate_tag()

        uri = f"sip:{self._config.domain}"
        from_uri = f"sip:{self._config.username}@{self._config.domain}"
        contact = f"<sip:{self._config.username}@{self._config.local_ip}:{self._config.local_port};transport=tls>"

        lines = [
            f"REGISTER {uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={branch}",
            f"From: <{from_uri}>;tag={tag}",
            f"To: <{from_uri}>",
            f"Call-ID: {self._registration_call_id}",
            f"CSeq: {self._registration_cseq} REGISTER",
            f"Contact: {contact}",
            "Max-Forwards: 70",
            "Expires: 3600",  # 1 hour registration
            f"User-Agent: VctLinphoneService/3.0.0",
        ]

        if auth:
            lines.append(auth)

        lines.append("Content-Length: 0")
        lines.append("")
        lines.append("")

        return "\r\n".join(lines)

    def _build_message_request(self, to_uri: str, body: str, call: SIPCall | None = None, auth_header: str = "") -> str:
        """Build a MESSAGE request for door unlock."""
        branch = self._generate_branch()
        tag = self._generate_tag()
        call_id = call.call_id if call else self._generate_call_id()
        cseq = call.cseq if call else 1

        if call:
            call.cseq += 1

        from_uri = f"sip:{self._config.username}@{self._config.domain}"

        lines = [
            f"MESSAGE {to_uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={branch}",
            f"From: <{from_uri}>;tag={tag}",
            f"To: <{to_uri}>",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} MESSAGE",
            "Max-Forwards: 70",
            "Content-Type: text/plain",
        ]

        if auth_header:
            lines.append(auth_header)

        lines.extend([
            f"Content-Length: {len(body)}",
            "",
            body,
        ])

        return "\r\n".join(lines)

    async def send_door_unlock(self, lock_id: int = 1, command_type: str = "A") -> bool:
        """Send door unlock command via SIP MESSAGE.

        Args:
            lock_id: Lock identifier (1, 2, or 3)
            command_type: "A" for *8*19/*8*20, "B" for *8*21/*8*22

        OpenWebNet command format:
            *8*19*<where>## - Open door
            *8*20*<where>## - Close door (release relay)

        Where <where> can be:
            - Empty: simple installations
            - Apartment code: for multi-apartment systems
            - Lock address: specific lock in the system
        """
        # Determine command based on type
        if command_type == "A":
            open_cmd = "*8*19"
            close_cmd = "*8*20"
        else:
            open_cmd = "*8*21"
            close_cmd = "*8*22"

        # Build the WHERE part of the command
        # If apartment_code is configured, use it; otherwise try simple format
        if self._config.apartment_code:
            where = self._config.apartment_code
        else:
            # Try lock_id as the where parameter
            where = str(lock_id)

        open_message = f"{open_cmd}*{where}##"
        close_message = f"{close_cmd}*{where}##"

        # Use domain for remote connection, not MAC address
        # The MHT (MyHomeTouch) is addressed via the SIP domain
        to_uri = f"{SIP_MHT_PREFIX}{self._config.domain}"

        _LOGGER.debug("Sending door unlock: %s to %s", open_message, to_uri)

        try:
            # Send open command and wait for response
            response = await self._send_message_with_auth(to_uri, open_message)
            if not response:
                _LOGGER.error("Failed to send open command")
                return False

            # Brief delay
            await asyncio.sleep(0.1)

            # Send close command
            response = await self._send_message_with_auth(to_uri, close_message)
            if not response:
                _LOGGER.error("Failed to send close command")
                return False

            _LOGGER.debug("Door unlock command sent for lock %d (where=%s)", lock_id, where)
            return True

        except Exception as e:
            _LOGGER.error("Failed to send door unlock: %s", e)
            return False

    async def _send_message_with_auth(self, to_uri: str, body: str) -> bool:
        """Send a MESSAGE request, handling 407 Proxy Authentication Required.

        Strategy: Send without auth first, wait for 407, then retry with the
        fresh nonce from the 407 response.
        """
        max_attempts = 3

        for attempt in range(max_attempts):
            # Create event to wait for response
            self._message_auth_event = asyncio.Event()
            self._message_response_code = 0

            # Build request - with or without auth based on whether we have nonce
            if self._proxy_nonce and attempt > 0:
                # After first 407, use the nonce we just received
                auth_header = self._build_proxy_auth_header(to_uri)
                request = self._build_message_request(to_uri, body, auth_header=auth_header)
                _LOGGER.debug("Sending MESSAGE with proxy auth (attempt %d, nonce=%s...)",
                             attempt + 1, self._proxy_nonce[:8] if self._proxy_nonce else "none")
            else:
                # First attempt without auth
                request = self._build_message_request(to_uri, body)
                _LOGGER.debug("Sending MESSAGE without auth (attempt %d)", attempt + 1)

            await self._send(request)

            # Wait for response (with timeout)
            try:
                await asyncio.wait_for(self._message_auth_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                _LOGGER.warning("MESSAGE response timeout on attempt %d", attempt + 1)
                continue

            # Check response
            if self._message_response_code == 200 or self._message_response_code == 202:
                _LOGGER.debug("MESSAGE delivered successfully (code %d)", self._message_response_code)
                return True
            elif self._message_response_code == 407:
                # We got 407 and cached the new nonce - retry
                _LOGGER.debug("Got 407, will retry with fresh nonce")
                continue
            elif self._message_response_code == 100:
                # Provisional response, wait more
                try:
                    self._message_auth_event.clear()
                    await asyncio.wait_for(self._message_auth_event.wait(), timeout=2.0)
                    if self._message_response_code == 200 or self._message_response_code == 202:
                        return True
                except asyncio.TimeoutError:
                    pass
            else:
                _LOGGER.warning("MESSAGE failed with code %d", self._message_response_code)

        _LOGGER.error("MESSAGE failed after %d attempts", max_attempts)
        return False

    def _build_proxy_auth_header(self, to_uri: str, method: str = "MESSAGE") -> str:
        """Build Proxy-Authorization header for SIP requests.

        Args:
            to_uri: The request URI
            method: SIP method (MESSAGE, INVITE, etc.)
        """
        realm = self._proxy_realm or self._config.domain
        nonce = self._proxy_nonce
        opaque = self._proxy_opaque
        algorithm = self._proxy_algorithm or "MD5"

        if not nonce:
            return ""

        # Increment nonce count
        self._proxy_nc += 1
        nc = f"{self._proxy_nc:08x}"  # 8-digit hex

        # Generate cnonce (client nonce)
        cnonce = hashlib.md5(f"{time.time()}{random.random()}".encode()).hexdigest()[:16]

        # For Digest auth, we need username:realm:password
        username = self._config.username
        password = self._config.password

        _LOGGER.debug("Building Proxy-Auth: user=%s, realm=%s, uri=%s, method=%s, nc=%s", username, realm, to_uri, method, nc)

        # Calculate digest with qop=auth
        ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{to_uri}".encode()).hexdigest()
        # With qop=auth: response = MD5(HA1:nonce:nc:cnonce:qop:HA2)
        response_hash = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()

        _LOGGER.debug("Digest: HA1=%s, HA2=%s, response=%s", ha1[:8], ha2[:8], response_hash[:8])

        # Build header with all required fields including nc and cnonce
        header = (
            f'Proxy-Authorization: Digest username="{username}", '
            f'realm="{realm}", nonce="{nonce}", uri="{to_uri}", '
            f'nc={nc}, cnonce="{cnonce}", qop=auth, '
            f'response="{response_hash}", algorithm={algorithm}'
        )

        # Add opaque if provided by server
        if opaque:
            header = header.replace(f', algorithm={algorithm}', f', opaque="{opaque}", algorithm={algorithm}')

        return header

    async def answer_call(self, call_id: str) -> bool:
        """Answer an incoming call."""
        call = self._calls.get(call_id)
        if not call or call.state != CallState.INCOMING:
            return False

        # Build 200 OK response with SDP for video
        # This requires proper SDP negotiation
        # For now, just accept
        response = self._build_ok_response(call)
        await self._send(response)

        call.state = CallState.CONNECTED
        if self._on_call_state_changed:
            self._on_call_state_changed(call, call.state)

        return True

    async def switch_camera(self, call_id: str, camera_addr: str) -> bool:
        """Switch to a different camera using SIP UPDATE with DEVADDR."""
        call = self._calls.get(call_id)
        if not call or call.state != CallState.CONNECTED:
            return False

        # Build UPDATE request with DEVADDR SDP attribute
        request = self._build_update_request(call, camera_addr)
        await self._send(request)

        call.devaddr = camera_addr
        return True

    def _build_update_request(self, call: SIPCall, devaddr: str) -> str:
        """Build an UPDATE request for camera switching."""
        branch = self._generate_branch()
        call.cseq += 1

        # Build SDP with DEVADDR attribute
        sdp = self._build_video_sdp(call.video_rtp_port, devaddr)

        lines = [
            f"UPDATE {call.remote_uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={branch}",
            f"From: {call.from_header}",
            f"To: {call.to_header}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} UPDATE",
            "Max-Forwards: 70",
            f"Contact: <sip:{self._config.username}@{self._config.local_ip}:{self._config.local_port};transport=tls>",
            "Content-Type: application/sdp",
            f"Content-Length: {len(sdp)}",
            "",
            sdp,
        ]

        return "\r\n".join(lines)

    def _build_video_sdp(self, rtp_port: int, devaddr: str = "") -> str:
        """Build SDP for video reception."""
        lines = [
            "v=0",
            f"o=- {int(time.time())} {int(time.time())} IN IP4 {self._config.local_ip}",
            "s=BTicino Hometouch",
            f"c=IN IP4 {self._config.local_ip}",
            "t=0 0",
            f"m=video {rtp_port} RTP/SAVP 96",
            "a=rtpmap:96 H264/90000",
            "a=recvonly",
        ]

        if devaddr:
            lines.append(f"a=DEVADDR:{devaddr}")
            # CAMERASLIDING is only used for call updates (cycling cameras), not initial INVITEs
            # Including it in initial INVITE causes 486 Busy Here from gateway

        return "\r\n".join(lines)

    def _build_ok_response(self, call: SIPCall) -> str:
        """Build 200 OK response."""
        # Simplified OK response
        lines = [
            "SIP/2.0 200 OK",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={call.branch}",
            f"From: {call.from_header}",
            f"To: {call.to_header};tag={self._generate_tag()}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} INVITE",
            f"Contact: <sip:{self._config.username}@{self._config.local_ip}:{self._config.local_port};transport=tls>",
            "Content-Length: 0",
            "",
            "",
        ]

        return "\r\n".join(lines)

    async def _send(self, message: str):
        """Send a SIP message."""
        if not self._writer:
            raise ConnectionError("Not connected")

        # Log SIP messages - truncate for normal operations, debug level
        _LOGGER.debug("Sending SIP message:\n%s", message[:500])
        self._writer.write(message.encode('utf-8'))
        await self._writer.drain()

    async def _receive_loop(self):
        """Receive loop for SIP messages."""
        buffer = b""  # Use bytes buffer for more reliable handling

        while self._running and self._reader:
            try:
                data = await self._reader.read(8192)  # Larger buffer
                if not data:
                    break

                buffer += data

                # Parse complete messages
                while b"\r\n\r\n" in buffer:
                    header_end = buffer.index(b"\r\n\r\n")
                    header = buffer[:header_end].decode('utf-8', errors='ignore')

                    # Check for Content-Length
                    content_length = 0
                    for line in header.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                content_length = int(line.split(":")[1].strip())
                            except ValueError:
                                pass
                            break

                    message_end = header_end + 4 + content_length
                    if len(buffer) < message_end:
                        break  # Wait for more data

                    message = buffer[:message_end].decode('utf-8', errors='ignore')
                    buffer = buffer[message_end:]

                    await self._handle_message(message)

            except asyncio.CancelledError:
                _LOGGER.debug("Receive loop cancelled")
                break
            except Exception as e:
                _LOGGER.error("Error in receive loop: %s", e)
                import traceback
                _LOGGER.debug(traceback.format_exc())
                # Don't break on error, try to continue receiving
                await asyncio.sleep(1)
                continue

        _LOGGER.warning("Receive loop exited, connection may have been closed by server")
        self._running = False
        self._registration_state = RegistrationState.UNREGISTERED

        # Only try to reconnect if this wasn't an intentional disconnect
        if not self._intentional_disconnect:
            _LOGGER.info("Connection lost unexpectedly, will attempt auto-reconnect")
            asyncio.create_task(self._auto_reconnect())
        else:
            _LOGGER.debug("Intentional disconnect, skipping auto-reconnect")

    async def _handle_message(self, message: str):
        """Handle a received SIP message."""
        # Log received SIP messages at debug level (truncated)
        _LOGGER.debug("Received SIP message:\n%s", message[:500])

        lines = message.split("\r\n")
        if not lines:
            return

        first_line = lines[0]

        if first_line.startswith("SIP/2.0"):
            # This is a response
            await self._handle_response(message)
        else:
            # This is a request
            await self._handle_request(message)

    async def _handle_response(self, message: str):
        """Handle a SIP response."""
        lines = message.split("\r\n")
        status_line = lines[0]

        # Parse status code
        parts = status_line.split(" ", 2)
        if len(parts) < 2:
            return

        status_code = int(parts[1])

        # Find CSeq to determine what this is a response to
        cseq_method = ""
        for line in lines:
            if line.lower().startswith("cseq:"):
                cseq_parts = line.split()
                if len(cseq_parts) >= 3:
                    cseq_method = cseq_parts[2]
                break

        if cseq_method == "REGISTER":
            await self._handle_register_response(status_code, message)
        elif cseq_method == "MESSAGE":
            await self._handle_message_response(status_code, message)
        elif cseq_method == "INVITE":
            await self._handle_invite_response(status_code, message)
        elif cseq_method in ("UPDATE", "BYE"):
            await self._handle_call_response(status_code, message, cseq_method)

    async def _handle_register_response(self, status_code: int, message: str):
        """Handle REGISTER response."""
        _LOGGER.debug("REGISTER response: %d (current state: %s)", status_code, self._registration_state)

        if status_code == 200:
            self._registration_state = RegistrationState.REGISTERED
            self._auth_in_progress = False
            self._auth_attempts = 0
            _LOGGER.info("Successfully registered with SIP server")

            # Schedule re-registration
            self._register_task = asyncio.create_task(self._reregister_loop())

        elif status_code == 401:
            # Track auth attempts to prevent infinite loops
            if not hasattr(self, '_auth_attempts'):
                self._auth_attempts = 0
            self._auth_attempts += 1

            if self._auth_attempts > 3:
                _LOGGER.error("Too many authentication attempts, giving up")
                self._registration_state = RegistrationState.FAILED
                return

            # Need authentication
            # Parse WWW-Authenticate header and retry with credentials
            await self._handle_auth_challenge(message)

        else:
            self._registration_state = RegistrationState.FAILED
            _LOGGER.error("Registration failed with status %d", status_code)

    async def _handle_auth_challenge(self, message: str):
        """Handle authentication challenge."""
        # Prevent handling if we already sent auth
        if hasattr(self, '_auth_in_progress') and self._auth_in_progress:
            _LOGGER.debug("Auth already in progress, ignoring duplicate 401")
            return

        self._auth_in_progress = True

        try:
            # Parse WWW-Authenticate header
            www_auth = ""
            for line in message.split("\r\n"):
                if line.lower().startswith("www-authenticate:"):
                    www_auth = line[17:].strip()
                    break

            if not www_auth:
                _LOGGER.error("No WWW-Authenticate header found in 401 response")
                self._registration_state = RegistrationState.FAILED
                return

            _LOGGER.debug("WWW-Authenticate: %s", www_auth)

            # Parse realm and nonce
            realm_match = re.search(r'realm="([^"]+)"', www_auth)
            nonce_match = re.search(r'nonce="([^"]+)"', www_auth)

            if not realm_match or not nonce_match:
                _LOGGER.error("Could not parse realm/nonce from WWW-Authenticate: %s", www_auth)
                self._registration_state = RegistrationState.FAILED
                return

            realm = realm_match.group(1)
            nonce = nonce_match.group(1)

            _LOGGER.debug("Parsed realm=%s, nonce=%s", realm, nonce)

            # Calculate digest response
            ha1 = hashlib.md5(
                f"{self._config.username}:{realm}:{self._config.password}".encode()
            ).hexdigest()

            uri = f"sip:{self._config.domain}"
            ha2 = hashlib.md5(f"REGISTER:{uri}".encode()).hexdigest()

            response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()

            # Build Authorization header
            auth_header = (
                f'Authorization: Digest username="{self._config.username}", '
                f'realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}"'
            )

            _LOGGER.info("Sending REGISTER with Digest authentication")

            # Send REGISTER with auth
            self._registration_cseq += 1
            request = self._build_register_request(auth_header)
            await self._send(request)

        finally:
            # Reset after a delay to allow retries if auth fails
            await asyncio.sleep(2)
            self._auth_in_progress = False

    async def _handle_message_response(self, status_code: int, message: str):
        """Handle MESSAGE response, especially 407 Proxy Authentication Required."""
        _LOGGER.debug("MESSAGE response: %d", status_code)

        # Store the response code for the waiting coroutine
        self._message_response_code = status_code

        if status_code == 407:
            # Parse Proxy-Authenticate header and cache nonce for retry
            proxy_auth = ""
            for line in message.split("\r\n"):
                if line.lower().startswith("proxy-authenticate:"):
                    proxy_auth = line[19:].strip()
                    break

            if proxy_auth:
                realm_match = re.search(r'realm="([^"]+)"', proxy_auth)
                nonce_match = re.search(r'nonce="([^"]+)"', proxy_auth)
                opaque_match = re.search(r'opaque="([^"]+)"', proxy_auth)
                algo_match = re.search(r'algorithm=(\w+)', proxy_auth)

                if realm_match and nonce_match:
                    self._proxy_realm = realm_match.group(1)
                    self._proxy_nonce = nonce_match.group(1)
                    self._proxy_nc = 0  # Reset nonce count for new nonce
                    if opaque_match:
                        self._proxy_opaque = opaque_match.group(1)
                    if algo_match:
                        self._proxy_algorithm = algo_match.group(1)
                    _LOGGER.debug("Cached proxy auth: realm=%s, nonce=%s, opaque=%s, algo=%s",
                                self._proxy_realm, self._proxy_nonce, self._proxy_opaque, self._proxy_algorithm)

        # Signal the waiting coroutine (for any non-provisional response)
        if self._message_auth_event and status_code != 100:
            self._message_auth_event.set()
        elif self._message_auth_event and status_code == 100:
            # For 100 Trying, still signal but the caller will wait for final response
            pass

    async def _handle_call_response(self, status_code: int, message: str, method: str):
        """Handle call-related response."""
        # Find Call-ID
        call_id = ""
        for line in message.split("\r\n"):
            if line.lower().startswith("call-id:"):
                call_id = line[8:].strip()
                break

        call = self._calls.get(call_id)
        if not call:
            return

        if method == "INVITE":
            if status_code == 200:
                call.state = CallState.CONNECTED
            elif status_code >= 400:
                call.state = CallState.DISCONNECTED

            if self._on_call_state_changed:
                self._on_call_state_changed(call, call.state)

    async def _handle_request(self, message: str):
        """Handle a SIP request."""
        lines = message.split("\r\n")
        request_line = lines[0]
        parts = request_line.split(" ")
        method = parts[0]

        if method == "INVITE":
            await self._handle_invite(message)
        elif method == "BYE":
            await self._handle_bye(message)
        elif method == "CANCEL":
            await self._handle_cancel(message)
        elif method == "OPTIONS":
            await self._handle_options(message)

    async def _handle_invite(self, message: str):
        """Handle incoming INVITE."""
        # Parse headers
        call_id = ""
        from_header = ""
        to_header = ""
        via_branch = ""
        cseq = 0
        remote_uri = ""

        lines = message.split("\r\n")
        request_line = lines[0]
        parts = request_line.split(" ")
        if len(parts) >= 2:
            remote_uri = parts[1]

        for line in lines:
            lower = line.lower()
            if lower.startswith("call-id:"):
                call_id = line[8:].strip()
            elif lower.startswith("from:"):
                from_header = line[5:].strip()
            elif lower.startswith("to:"):
                to_header = line[3:].strip()
            elif lower.startswith("via:"):
                via_match = re.search(r'branch=([^;,\s]+)', line)
                if via_match:
                    via_branch = via_match.group(1)
            elif lower.startswith("cseq:"):
                cseq_parts = line.split()
                if len(cseq_parts) >= 2:
                    cseq = int(cseq_parts[1])

        # Create call object
        call = SIPCall(
            call_id=call_id,
            state=CallState.INCOMING,
            remote_uri=remote_uri,
            from_header=from_header,
            to_header=to_header,
            branch=via_branch,
            cseq=cseq,
        )

        self._calls[call_id] = call

        # Send 180 Ringing
        ringing = self._build_ringing_response(call)
        await self._send(ringing)

        # Notify callback
        if self._on_incoming_call:
            self._on_incoming_call(call)

    def _build_ringing_response(self, call: SIPCall) -> str:
        """Build 180 Ringing response."""
        lines = [
            "SIP/2.0 180 Ringing",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={call.branch}",
            f"From: {call.from_header}",
            f"To: {call.to_header};tag={self._generate_tag()}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} INVITE",
            "Content-Length: 0",
            "",
            "",
        ]

        return "\r\n".join(lines)

    async def _handle_bye(self, message: str):
        """Handle BYE request."""
        # Find Call-ID
        call_id = ""
        for line in message.split("\r\n"):
            if line.lower().startswith("call-id:"):
                call_id = line[8:].strip()
                break

        call = self._calls.get(call_id)
        if call:
            call.state = CallState.DISCONNECTED
            if self._on_call_state_changed:
                self._on_call_state_changed(call, call.state)

        # Send 200 OK
        # Simplified response
        ok_response = f"SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n"
        await self._send(ok_response)

    async def _handle_cancel(self, message: str):
        """Handle CANCEL request."""
        # Similar to BYE
        pass

    async def _handle_options(self, message: str):
        """Handle OPTIONS request (keepalive)."""
        # Send 200 OK
        ok_response = f"SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n"
        await self._send(ok_response)

    async def _reregister_loop(self):
        """Periodically re-register."""
        while self._running:
            await asyncio.sleep(3600)  # Re-register every hour
            if self._running and self._registration_state == RegistrationState.REGISTERED:
                self._registration_cseq += 1
                request = self._build_register_request()
                try:
                    await self._send(request)
                except Exception as e:
                    _LOGGER.error("Re-registration failed: %s", e)

    async def initiate_call(
        self,
        station_id: int = 1,
        media_mode: MediaMode = MediaMode.VIDEO_ONLY
    ) -> SIPCall | None:
        """Initiate an outgoing call to view camera from an outdoor station.

        This sends a SIP INVITE to the MHT (MyHomeTouch) gateway to request
        video streaming from the specified outdoor station (posto esterno).

        Args:
            station_id: The outdoor station number (1, 2, 3...)
            media_mode: What media to request (VIDEO_ONLY, AUDIO_ONLY, or AUDIO_VIDEO)

        Returns:
            SIPCall object if successful, None if failed
        """
        if not self.is_registered:
            _LOGGER.error("Cannot initiate call: not registered")
            return None

        # Build the SIP URI for MHT (MyHomeTouch gateway)
        to_uri = f"{SIP_MHT_PREFIX}{self._config.domain}"

        # Build DEVADDR for the station
        # Format: deviceDev + deviceAddr (NO PADDING!)
        # Based on decompiled app code (Z0/s.java lines 611-617):
        #   StringBuilder.append(device.f())  // deviceDev (e.g. "2" for VDE)
        #   StringBuilder.append(device.e())  // deviceAddr (e.g. "0", "1", "6")
        #   Result: "20", "21", "26"
        #
        # From plantSQLite database - VDE devices have deviceDev=2:
        #   - Albani (station 1) = deviceDev "2" + deviceAddr "0" → DEVADDR "20"
        #   - Madruzzo (station 2) = deviceDev "2" + deviceAddr "1" → DEVADDR "21"
        #   - Scala B (station 3) = deviceDev "2" + deviceAddr "6" → DEVADDR "26"
        STATION_TO_DEVADDR = {
            1: "20",   # Albani - deviceDev "2" + deviceAddr "0"
            2: "21",   # Madruzzo - deviceDev "2" + deviceAddr "1"
            3: "26",   # Scala B - deviceDev "2" + deviceAddr "6"
        }

        devaddr = STATION_TO_DEVADDR.get(station_id, f"2{station_id - 1}")

        _LOGGER.info("Initiating call to station %d (DEVADDR=%s, mode=%s)", station_id, devaddr, media_mode.value)

        # Create call object
        call_id = self._generate_call_id()
        call = SIPCall(
            call_id=call_id,
            state=CallState.CALLING,
            remote_uri=to_uri,
            local_tag=self._generate_tag(),
            cseq=1,
            devaddr=devaddr,
            station_id=station_id,  # Store station_id for fixed port allocation
            media_mode=media_mode,
        )

        self._calls[call_id] = call

        try:
            # Build and send INVITE with custom SDP attributes
            invite = self._build_invite_request(call)
            await self._send(invite)

            # Wait for response (with timeout)
            # The response will be handled in _handle_invite_response
            for _ in range(100):  # 10 second timeout
                await asyncio.sleep(0.1)

                if call.state == CallState.CONNECTED:
                    _LOGGER.info("Call connected to station %d", station_id)
                    return call
                elif call.state == CallState.DISCONNECTED:
                    _LOGGER.warning("Call to station %d failed", station_id)
                    return None
                elif call.state == CallState.NEEDS_AUTH:
                    # Need to reconnect for authenticated INVITE
                    # This is done here to avoid race conditions with receive loop
                    _LOGGER.info("Reconnecting for authenticated INVITE...")

                    # Disconnect completely (intentional - don't trigger auto-reconnect)
                    await self.disconnect(intentional=True)
                    await asyncio.sleep(0.3)

                    # Reconnect
                    if not await self.connect():
                        _LOGGER.error("Failed to reconnect for authenticated INVITE")
                        call.state = CallState.DISCONNECTED
                        return None

                    # After reconnect for 407 auth, we're implicitly registered
                    # because the proxy accepted our previous registration
                    # Set state to REGISTERED so is_registered returns True
                    self._registration_state = RegistrationState.REGISTERED
                    _LOGGER.debug("Set registration state to REGISTERED after 407 reconnect")

                    # Send authenticated INVITE
                    call.state = CallState.CALLING
                    call.cseq += 1
                    auth_header = self._build_proxy_auth_header(call.remote_uri, method="INVITE")
                    invite = self._build_invite_request_with_auth(call, auth_header)
                    await self._send(invite)
                    _LOGGER.info("Sent authenticated INVITE")

            _LOGGER.warning("Call to station %d timed out", station_id)
            call.state = CallState.DISCONNECTED
            return None

        except Exception as e:
            _LOGGER.error("Failed to initiate call: %s", e)
            call.state = CallState.DISCONNECTED
            return None

    async def send_reinvite(self, call: SIPCall, new_media_mode: MediaMode) -> bool:
        """Send a re-INVITE to modify the media session (add/remove audio).

        This is used to add audio to an existing video-only call, or vice versa.
        According to BTicino/Olinphone behavior, re-INVITE is the standard way
        to modify an active session.

        Args:
            call: The active SIPCall to modify
            new_media_mode: The new media mode to request

        Returns:
            True if re-INVITE was sent and accepted, False otherwise
        """
        if call.state != CallState.CONNECTED:
            _LOGGER.error("Cannot send re-INVITE: call not connected (state=%s)", call.state)
            return False

        _LOGGER.info("Sending re-INVITE to change media from %s to %s",
                    call.media_mode.value, new_media_mode.value)

        # Update the media mode
        old_mode = call.media_mode
        call.media_mode = new_media_mode

        # Increment CSeq for new transaction
        call.cseq += 1

        try:
            # Build re-INVITE with authentication (we should already have auth from initial INVITE)
            if self._proxy_nonce:
                auth_header = self._build_proxy_auth_header(call.remote_uri, method="INVITE")
                invite = self._build_invite_request_with_auth(call, auth_header)
            else:
                invite = self._build_invite_request(call)

            await self._send(invite)
            _LOGGER.info("Sent re-INVITE with new SDP")

            # Wait for response (ACK will be sent automatically by _handle_invite_response)
            # The call stays in CONNECTED state during re-INVITE
            for _ in range(50):  # 5 second timeout
                await asyncio.sleep(0.1)

                # Check if we got audio port back from server
                if new_media_mode in (MediaMode.AUDIO_VIDEO, MediaMode.AUDIO_ONLY):
                    if call.remote_audio_port and call.audio_srtp_key_remote:
                        _LOGGER.info("re-INVITE successful: got audio port %d", call.remote_audio_port)
                        return True

            _LOGGER.warning("re-INVITE timeout - no audio parameters received")
            # Revert media mode on failure
            call.media_mode = old_mode
            return False

        except Exception as e:
            _LOGGER.error("re-INVITE failed: %s", e)
            call.media_mode = old_mode
            return False

    def _build_invite_request(self, call: SIPCall) -> str:
        """Build an INVITE request based on media mode."""
        branch = self._generate_branch()
        call.branch = branch

        from_uri = f"sip:{self._config.username}@{self._config.domain}"
        to_uri = call.remote_uri
        contact = f"<sip:{self._config.username}@{self._config.local_ip}:{self._config.local_port};transport=tls>"

        # Build SDP body based on media mode
        sdp_body = self._build_sdp_for_mode(call)

        _LOGGER.info("INVITE SDP [%s] (%d bytes): %s", call.media_mode.value, len(sdp_body), sdp_body.replace('\r\n', ' | '))

        lines = [
            f"INVITE {to_uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={branch}",
            f"Route: <sip:{self._config.server}:{self._config.port};transport=tls;lr>",
            f"From: <{from_uri}>;tag={call.local_tag}",
            f"To: <{to_uri}>",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} INVITE",
            f"Contact: {contact}",
            "Max-Forwards: 70",
            "User-Agent: VctLinphoneService/3.0.0",
            "Allow: INVITE, ACK, CANCEL, BYE, OPTIONS, MESSAGE, NOTIFY",
            "Supported: replaces, outbound, 100rel",  # Match gateway capabilities
            "Content-Type: application/sdp",
            "Content-Disposition: session",  # Required by BTicino (from thesis)
            f"Content-Length: {len(sdp_body)}",
            "",
            sdp_body,
        ]

        return "\r\n".join(lines)

    def _build_sdp_for_mode(self, call: SIPCall) -> str:
        """Build SDP body based on media mode.

        VIDEO_ONLY: Only video stream (recvonly) - for camera viewing
        AUDIO_ONLY: Only audio stream (sendrecv) - for intercom conversation
        AUDIO_VIDEO: Both audio (sendrecv) and video (recvonly)
        """
        import base64

        session_id = int(time.time())

        # Use public IP for the connection address (c= line) where gateway sends media
        # This is critical for NAT traversal - gateway needs to send to our public IP
        # The local_ip is still used for SIP signaling (Via headers, etc.)
        media_ip = self._config.public_ip if self._config.public_ip else self._config.local_ip

        _LOGGER.debug("SDP media IP: %s (public_ip=%s, local_ip=%s)",
                     media_ip, self._config.public_ip, self._config.local_ip)

        sdp_lines = [
            "v=0",
            f"o={self._config.username} {session_id} {session_id} IN IP4 {self._config.local_ip}",
            "s=BTicino Call",
            f"c=IN IP4 {media_ip}",  # Gateway will send media to this IP
            "t=0 0",
            # Custom BTicino session-level attributes (MUST be before media lines)
            # For VDE (Video Door Entry), don't set TVCC - only TVCC cameras use TVCC:1
            # The official app omits TVCC entirely for normal door stations
            f"a=DEVADDR:{call.devaddr}",
            # CAMERASLIDING is only used for call updates (cycling cameras), not initial INVITEs
            # Including it in initial INVITE causes 486 Busy Here from gateway
        ]

        # VIDEO ONLY - audio is not supported by the BTicino cloud gateway for third-party clients
        # The gateway systematically rejects audio SDP offers with m=audio 0
        if call.media_mode in (MediaMode.VIDEO_ONLY, MediaMode.AUDIO_VIDEO):
            # Use fixed port for NAT traversal
            local_video_port = FIXED_VIDEO_PORT
            call.video_rtp_port = local_video_port

            # Generate SRTP key ONLY if not already set (for re-INVITE after 407)
            if not call.video_srtp_key:
                video_key_raw = random.randbytes(30)  # 16 bytes key + 14 bytes salt
                call.video_srtp_key = video_key_raw
                video_key_b64 = base64.b64encode(video_key_raw).decode()
                _LOGGER.info("Generated NEW SRTP key for video: %d bytes", len(video_key_raw))
            else:
                video_key_b64 = base64.b64encode(call.video_srtp_key).decode()
                _LOGGER.info("Reusing EXISTING SRTP key for video")

            sdp_lines.extend([
                f"m=video {local_video_port} RTP/SAVP 96",
                f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{video_key_b64}",
                "a=recvonly",
                "a=rtpmap:96 H264/90000",
                "a=fmtp:96 profile-level-id=42e01f",
            ])

        return "\r\n".join(sdp_lines) + "\r\n"

    async def _handle_invite_response(self, status_code: int, message: str):
        """Handle response to our outgoing INVITE."""
        # Find Call-ID
        call_id = ""
        to_tag = ""

        for line in message.split("\r\n"):
            lower = line.lower()
            if lower.startswith("call-id:"):
                call_id = line[8:].strip()
            elif lower.startswith("to:"):
                # Extract tag from To header
                tag_match = re.search(r'tag=([^;\s>]+)', line)
                if tag_match:
                    to_tag = tag_match.group(1)

        call = self._calls.get(call_id)
        if not call:
            _LOGGER.debug("Received INVITE response for unknown call %s", call_id)
            return

        _LOGGER.debug("INVITE response %d for call %s", status_code, call_id)

        if status_code == 100:
            # Trying - just wait
            pass
        elif status_code == 180 or status_code == 183:
            # Ringing / Session Progress
            call.state = CallState.EARLY
            call.remote_tag = to_tag
        elif status_code == 200:
            # OK - call established
            call.state = CallState.CONNECTED
            call.remote_tag = to_tag

            # Parse SDP from response to get SRTP parameters
            self._parse_sdp_response(call, message)

            # Send ACK
            ack = self._build_ack(call)
            await self._send(ack)

            if self._on_call_state_changed:
                self._on_call_state_changed(call, call.state)

        elif status_code == 407:
            # Proxy Authentication Required for INVITE
            _LOGGER.info("INVITE requires authentication")
            # Similar to MESSAGE auth handling
            proxy_auth = ""
            for line in message.split("\r\n"):
                if line.lower().startswith("proxy-authenticate:"):
                    proxy_auth = line[19:].strip()
                    break

            if proxy_auth:
                realm_match = re.search(r'realm="([^"]+)"', proxy_auth)
                nonce_match = re.search(r'nonce="([^"]+)"', proxy_auth)
                opaque_match = re.search(r'opaque="([^"]+)"', proxy_auth)
                algorithm_match = re.search(r'algorithm=([^,\s]+)', proxy_auth)

                if realm_match and nonce_match:
                    # Store auth parameters for _build_proxy_auth_header
                    self._proxy_realm = realm_match.group(1)
                    self._proxy_nonce = nonce_match.group(1)
                    if opaque_match:
                        self._proxy_opaque = opaque_match.group(1)
                    if algorithm_match:
                        self._proxy_algorithm = algorithm_match.group(1)
                    # Reset nonce count for new nonce
                    self._nonce_count = 0

                    _LOGGER.debug("INVITE auth params: realm=%s, nonce=%s...",
                                  self._proxy_realm, self._proxy_nonce[:16] if self._proxy_nonce else "")

                    # Send ACK to complete the 407 transaction
                    ack = self._build_ack(call)
                    await self._send(ack)

                    # Signal that we need to reconnect for authenticated INVITE
                    # The reconnection will be handled by initiate_call() to avoid
                    # race conditions with the receive loop
                    _LOGGER.info("Setting NEEDS_AUTH state for reconnection...")
                    call.state = CallState.NEEDS_AUTH

        elif status_code >= 400:
            # Error
            call.state = CallState.DISCONNECTED
            _LOGGER.warning("INVITE failed with %d", status_code)

            if self._on_call_state_changed:
                self._on_call_state_changed(call, call.state)

    def _build_invite_request_with_auth(self, call: SIPCall, auth_header: str) -> str:
        """Build an INVITE request with authentication."""
        branch = self._generate_branch()
        call.branch = branch

        from_uri = f"sip:{self._config.username}@{self._config.domain}"
        to_uri = call.remote_uri
        contact = f"<sip:{self._config.username}@{self._config.local_ip}:{self._config.local_port};transport=tls>"

        # Rebuild SDP with same format as initial INVITE (reuse the mode-based builder)
        sdp_body = self._build_sdp_for_mode(call)

        lines = [
            f"INVITE {to_uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={branch}",
            f"Route: <sip:{self._config.server}:{self._config.port};transport=tls;lr>",
            f"From: <{from_uri}>;tag={call.local_tag}",
            f"To: <{to_uri}>",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} INVITE",
            f"Contact: {contact}",
            "Max-Forwards: 70",
            "User-Agent: VctLinphoneService/3.0.0",
            "Allow: INVITE, ACK, CANCEL, BYE, OPTIONS, MESSAGE, NOTIFY",
            "Supported: replaces, outbound, 100rel",
            auth_header,
            "Content-Type: application/sdp",
            "Content-Disposition: session",
            f"Content-Length: {len(sdp_body)}",
            "",
            sdp_body,
        ]

        return "\r\n".join(lines)

    def _build_ack(self, call: SIPCall) -> str:
        """Build an ACK request."""
        branch = self._generate_branch()

        from_uri = f"sip:{self._config.username}@{self._config.domain}"
        to_uri = call.remote_uri

        to_header = f"<{to_uri}>"
        if call.remote_tag:
            to_header = f"<{to_uri}>;tag={call.remote_tag}"

        lines = [
            f"ACK {to_uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={branch}",
            f"From: <{from_uri}>;tag={call.local_tag}",
            f"To: {to_header}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} ACK",
            "Max-Forwards: 70",
            "Content-Length: 0",
            "",
            "",
        ]

        return "\r\n".join(lines)

    def _parse_sdp_response(self, call: SIPCall, message: str):
        """Parse SDP from 200 OK response to extract remote SRTP parameters.

        SRTP key exchange in SDP offer/answer (RFC 4568):
        In practice, each party sends the key it will use to ENCRYPT data it sends.
        - We send OUR key in INVITE → we would encrypt with OUR key (but we're recvonly)
        - Server sends ITS key in 200 OK → server encrypts with ITS key

        Since server is sendonly for video:
        - Server SENDS video encrypted with ITS key (from 200 OK)
        - We must DECRYPT using SERVER's key

        For audio (sendrecv):
        - Server SENDS audio encrypted with ITS key → we decrypt with server's key
        - We SEND audio encrypted with OUR key → server decrypts with our key

        This is different from some RFC interpretations but matches real-world
        implementations like Olinphone/BTicino.
        """
        # Find the SDP body (after blank line)
        parts = message.split("\r\n\r\n", 1)
        if len(parts) < 2:
            _LOGGER.warning("No SDP body found in 200 OK response")
            return

        sdp = parts[1]
        _LOGGER.debug("Parsing SDP response:\n%s", sdp)

        remote_video_port = None
        remote_audio_port = None
        remote_ip = None
        current_media = None  # Track which media section we're in

        # Parse SDP line by line, tracking media sections
        for line in sdp.split("\r\n"):
            if line.startswith("m=video"):
                current_media = "video"
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        remote_video_port = int(parts[1])
                        _LOGGER.debug("Remote video port: %d", remote_video_port)
                    except ValueError:
                        pass
            elif line.startswith("m=audio"):
                current_media = "audio"
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        remote_audio_port = int(parts[1])
                        call.remote_audio_port = remote_audio_port
                        _LOGGER.debug("Remote audio port: %d", remote_audio_port)
                    except ValueError:
                        pass
            elif line.startswith("a=crypto:"):
                # SRTP key for current media section
                crypto_match = re.search(r'inline:([A-Za-z0-9+/=]+)', line)
                if crypto_match:
                    import base64
                    try:
                        server_key = base64.b64decode(crypto_match.group(1))
                        if current_media == "video":
                            _LOGGER.info("Server VIDEO SRTP key: %d bytes, b64=%s...",
                                        len(server_key), crypto_match.group(1)[:20])
                            call.video_srtp_key = server_key
                        elif current_media == "audio":
                            _LOGGER.info("Server AUDIO SRTP key: %d bytes, b64=%s...",
                                        len(server_key), crypto_match.group(1)[:20])
                            # Server's key for DECRYPTING incoming audio from server
                            call.audio_srtp_key_remote = server_key
                    except Exception as e:
                        _LOGGER.warning("Failed to decode server SRTP key: %s", e)
            elif line.startswith("c=IN IP4"):
                # Connection address (remote IP)
                parts = line.split()
                if len(parts) >= 3:
                    remote_ip = parts[2]
                    call.remote_ip = remote_ip
                    _LOGGER.debug("Remote address: %s", remote_ip)

        # Log parsed info
        video_key_b64 = ""
        if call.video_srtp_key:
            import base64
            video_key_b64 = base64.b64encode(call.video_srtp_key).decode()[:20]

        audio_key_b64 = ""
        if call.audio_srtp_key_remote:
            import base64
            audio_key_b64 = base64.b64encode(call.audio_srtp_key_remote).decode()[:20]

        _LOGGER.info("SDP parsed: remote=%s, video_port=%s (key=%s...), audio_port=%s (key=%s...)",
                    remote_ip, remote_video_port, video_key_b64,
                    remote_audio_port, audio_key_b64)

    async def hangup_call(self, call_id: str) -> bool:
        """Hangup a call by sending BYE."""
        call = self._calls.get(call_id)
        if not call:
            return False

        try:
            bye = self._build_bye(call)
            await self._send(bye)
            call.state = CallState.DISCONNECTED

            if self._on_call_state_changed:
                self._on_call_state_changed(call, call.state)

            return True
        except Exception as e:
            _LOGGER.error("Failed to hangup call: %s", e)
            return False

    def _build_bye(self, call: SIPCall) -> str:
        """Build a BYE request."""
        branch = self._generate_branch()
        call.cseq += 1

        from_uri = f"sip:{self._config.username}@{self._config.domain}"
        to_uri = call.remote_uri

        to_header = f"<{to_uri}>"
        if call.remote_tag:
            to_header = f"<{to_uri}>;tag={call.remote_tag}"

        lines = [
            f"BYE {to_uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={branch}",
            f"From: <{from_uri}>;tag={call.local_tag}",
            f"To: {to_header}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} BYE",
            "Max-Forwards: 70",
            "Content-Length: 0",
            "",
            "",
        ]

        return "\r\n".join(lines)

    @property
    def is_registered(self) -> bool:
        """Return True if registered."""
        return self._registration_state == RegistrationState.REGISTERED

    @property
    def active_calls(self) -> list[SIPCall]:
        """Return list of active calls."""
        return [c for c in self._calls.values() if c.state not in (CallState.IDLE, CallState.DISCONNECTED)]


# Import for const
SIP_MHT_PREFIX = "sip:MHT@"
