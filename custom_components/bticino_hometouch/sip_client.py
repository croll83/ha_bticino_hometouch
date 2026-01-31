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
    video_srtp_key: bytes = b""
    devaddr: str = ""


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

        self._local_cseq = 1
        self._call_id_counter = 0
        self._registration_call_id = ""
        self._registration_cseq = 0

        # Determine local IP
        if not config.local_ip:
            self._config.local_ip = self._get_local_ip()

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
        try:
            # Create SSL context with client certificate
            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_REQUIRED

            # Load CA certificate
            ssl_context.load_verify_locations(cadata=self._config.ca_cert)

            # Load client certificate and key
            ssl_context.load_cert_chain(
                certfile=None,
                keyfile=None,
            )
            # We need to use the cert/key data directly
            # This requires writing temp files or using memory BIO

            import tempfile
            import os

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

            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            ssl_context.load_verify_locations(ca_file)
            ssl_context.load_cert_chain(cert_file, key_file)

            # Connect
            self._reader, self._writer = await asyncio.open_connection(
                self._config.server,
                self._config.port,
                ssl=ssl_context,
            )

            # Clean up temp files
            os.unlink(cert_file)
            os.unlink(key_file)
            os.unlink(ca_file)

            self._running = True
            self._recv_task = asyncio.create_task(self._receive_loop())

            _LOGGER.info("Connected to SIP server %s:%d", self._config.server, self._config.port)
            return True

        except Exception as e:
            _LOGGER.error("Failed to connect to SIP server: %s", e)
            return False

    async def disconnect(self):
        """Disconnect from SIP server."""
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
            "Expires: 5184000",
            f"User-Agent: HomeAssistant-BTicino/1.0",
        ]

        if auth:
            lines.append(auth)

        lines.append("Content-Length: 0")
        lines.append("")
        lines.append("")

        return "\r\n".join(lines)

    def _build_message_request(self, to_uri: str, body: str, call: SIPCall | None = None) -> str:
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
            f"Content-Length: {len(body)}",
            "",
            body,
        ]

        return "\r\n".join(lines)

    async def send_door_unlock(self, lock_id: int = 1, command_type: str = "A") -> bool:
        """Send door unlock command via SIP MESSAGE.

        Args:
            lock_id: Lock identifier (1, 2, or 3)
            command_type: "A" for *8*19/*8*20, "B" for *8*21/*8*22
        """
        # Determine command based on type
        if command_type == "A":
            open_cmd = "*8*19"
            close_cmd = "*8*20"
        else:
            open_cmd = "*8*21"
            close_cmd = "*8*22"

        # Format: *8*19*4## for apartment 4
        # The "4" seems to be the apartment/unit number
        apartment = "4"  # TODO: make configurable
        open_message = f"{open_cmd}*{apartment}##"
        close_message = f"{close_cmd}*{apartment}##"

        to_uri = f"{SIP_MHT_PREFIX}{self._config.gateway_address}"

        try:
            # Send open command
            request1 = self._build_message_request(to_uri, open_message)
            await self._send(request1)

            # Brief delay
            await asyncio.sleep(0.1)

            # Send close command
            request2 = self._build_message_request(to_uri, close_message)
            await self._send(request2)

            _LOGGER.info("Door unlock command sent for lock %d", lock_id)
            return True

        except Exception as e:
            _LOGGER.error("Failed to send door unlock: %s", e)
            return False

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

    async def hangup_call(self, call_id: str) -> bool:
        """Hangup a call."""
        call = self._calls.get(call_id)
        if not call:
            return False

        request = self._build_bye_request(call)
        await self._send(request)

        call.state = CallState.DISCONNECTED
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
            lines.append("a=CAMERASLIDING:1")

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

    def _build_bye_request(self, call: SIPCall) -> str:
        """Build BYE request."""
        branch = self._generate_branch()
        call.cseq += 1

        lines = [
            f"BYE {call.remote_uri} SIP/2.0",
            f"Via: SIP/2.0/TLS {self._config.local_ip}:{self._config.local_port};branch={branch}",
            f"From: {call.from_header}",
            f"To: {call.to_header}",
            f"Call-ID: {call.call_id}",
            f"CSeq: {call.cseq} BYE",
            "Max-Forwards: 70",
            "Content-Length: 0",
            "",
            "",
        ]

        return "\r\n".join(lines)

    async def _send(self, message: str):
        """Send a SIP message."""
        if not self._writer:
            raise ConnectionError("Not connected")

        _LOGGER.debug("Sending SIP message:\n%s", message[:500])
        self._writer.write(message.encode('utf-8'))
        await self._writer.drain()

    async def _receive_loop(self):
        """Receive loop for SIP messages."""
        buffer = ""

        while self._running and self._reader:
            try:
                data = await self._reader.read(4096)
                if not data:
                    break

                buffer += data.decode('utf-8', errors='ignore')

                # Parse complete messages
                while "\r\n\r\n" in buffer:
                    header_end = buffer.index("\r\n\r\n")
                    header = buffer[:header_end]

                    # Check for Content-Length
                    content_length = 0
                    for line in header.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            content_length = int(line.split(":")[1].strip())
                            break

                    message_end = header_end + 4 + content_length
                    if len(buffer) < message_end:
                        break  # Wait for more data

                    message = buffer[:message_end]
                    buffer = buffer[message_end:]

                    await self._handle_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error("Error in receive loop: %s", e)
                break

        self._running = False
        self._registration_state = RegistrationState.UNREGISTERED

    async def _handle_message(self, message: str):
        """Handle a received SIP message."""
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
            _LOGGER.debug("MESSAGE response: %d", status_code)
        elif cseq_method in ("INVITE", "UPDATE", "BYE"):
            await self._handle_call_response(status_code, message, cseq_method)

    async def _handle_register_response(self, status_code: int, message: str):
        """Handle REGISTER response."""
        if status_code == 200:
            self._registration_state = RegistrationState.REGISTERED
            _LOGGER.info("Successfully registered with SIP server")

            # Schedule re-registration
            self._register_task = asyncio.create_task(self._reregister_loop())

        elif status_code == 401:
            # Need authentication
            # Parse WWW-Authenticate header and retry with credentials
            await self._handle_auth_challenge(message)

        else:
            self._registration_state = RegistrationState.FAILED
            _LOGGER.error("Registration failed with status %d", status_code)

    async def _handle_auth_challenge(self, message: str):
        """Handle authentication challenge."""
        # Parse WWW-Authenticate header
        www_auth = ""
        for line in message.split("\r\n"):
            if line.lower().startswith("www-authenticate:"):
                www_auth = line[17:].strip()
                break

        if not www_auth:
            return

        # Parse realm and nonce
        realm_match = re.search(r'realm="([^"]+)"', www_auth)
        nonce_match = re.search(r'nonce="([^"]+)"', www_auth)

        if not realm_match or not nonce_match:
            return

        realm = realm_match.group(1)
        nonce = nonce_match.group(1)

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

        # Send REGISTER with auth
        self._registration_cseq += 1
        request = self._build_register_request(auth_header)
        await self._send(request)

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
