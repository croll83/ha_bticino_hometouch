"""BTicino MyHomeWeb API client for automatic provisioning."""

from __future__ import annotations

import io
import logging
import secrets
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .const import (
    BTICINO_API_BASE,
    BTICINO_API_PORT,
    DEFAULT_DEVICE_NAME,
)

_LOGGER = logging.getLogger(__name__)


class BticinoApiError(Exception):
    """Base exception for BTicino API errors."""


class BticinoAuthError(BticinoApiError):
    """Authentication failed."""


class BticinoProvisioningError(BticinoApiError):
    """Provisioning failed."""


@dataclass
class ProvisioningResult:
    """Result of the provisioning process."""

    # Account info
    plant_id: str
    gateway_id: str
    sip_account: str
    sip_password: str
    device_id: str

    # Certificates
    client_cert: str
    client_key: str
    ca_cert: str
    cert_expiry: datetime

    # Derived SIP config
    sip_domain: str
    sip_username: str


class BticinoApi:
    """BTicino MyHomeWeb API client."""

    def __init__(self, email: str, password: str) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        self._auth_token: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> BticinoApi:
        """Async context manager entry."""
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        if self._session:
            await self._session.close()
            self._session = None

    def _get_headers(self) -> dict[str, str]:
        """Get API request headers."""
        headers = {
            "Content-Type": "application/json",
            "prj_name": "MHT",
        }
        if self._auth_token:
            headers["auth_token"] = self._auth_token
        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | bytes, dict[str, str]]:
        """Make an API request."""
        if not self._session:
            self._session = aiohttp.ClientSession()

        url = f"{BTICINO_API_BASE}:{BTICINO_API_PORT}{endpoint}"
        headers = self._get_headers()

        _LOGGER.debug("API request: %s %s", method, url)

        async with self._session.request(
            method, url, json=json_data, headers=headers
        ) as response:
            status = response.status
            resp_headers = dict(response.headers)

            # Update auth_token if provided in response
            if "auth_token" in resp_headers:
                self._auth_token = resp_headers["auth_token"]

            # Try to parse as JSON, otherwise return raw bytes
            content_type = response.content_type
            if "json" in content_type or status != 200:
                try:
                    data = await response.json()
                except:
                    data = await response.read()
            else:
                data = await response.read()

            return status, data, resp_headers

    async def login(self) -> None:
        """Login to MyHomeWeb and get auth token."""
        payload = {
            "username": self._email,
            "pwd": self._password,
            "appVersion": "legacy-3.7.0",
        }

        status, data, headers = await self._request(
            "POST", "/eliot/users/sign_in", payload
        )

        if status != 200:
            _LOGGER.error("Login failed with status %d", status)
            raise BticinoAuthError(f"Login failed: {status}")

        # Auth token should be in headers or response body
        if not self._auth_token:
            if isinstance(data, dict):
                self._auth_token = data.get("auth_token")
                if not self._auth_token and "payload" in data:
                    payload_data = data.get("payload", {})
                    if isinstance(payload_data, str):
                        import json
                        payload_data = json.loads(payload_data)
                    self._auth_token = payload_data.get("auth_token")

        if not self._auth_token:
            raise BticinoAuthError("No auth_token in login response")

        _LOGGER.debug("Login successful")

    async def get_plants(self) -> list[dict[str, Any]]:
        """Get list of plants for the user."""
        status, data, _ = await self._request("GET", "/eliot/plants")

        if status == 401:
            raise BticinoAuthError("Unauthorized - token may be expired")

        if status != 200:
            _LOGGER.error("Failed to fetch plants: %d", status)
            return []

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("plants", [])
        return []

    async def get_gateways(self, plant_id: str) -> list[dict[str, Any]]:
        """Get list of gateways for a plant."""
        status, data, _ = await self._request(
            "GET", f"/eliot/plants/{plant_id}/gateway/"
        )

        if status != 200:
            _LOGGER.error("Failed to fetch gateways: %d", status)
            return []

        if isinstance(data, list):
            return data
        return []

    async def get_sip_users(
        self, plant_id: str, gateway_id: str
    ) -> list[dict[str, Any]]:
        """Get list of SIP users for a plant/gateway."""
        status, data, _ = await self._request(
            "GET", f"/eliot/sip/users/plants/{plant_id}/gateway/{gateway_id}"
        )

        if status == 404:
            return []

        if status != 200:
            _LOGGER.error("Failed to fetch SIP users: %d", status)
            return []

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("users", [])
        return []

    async def create_sip_user(
        self,
        sip_account: str,
        device_name: str,
        gateway_id: str,
        device_id: str,
    ) -> bool:
        """Create a new SIP user."""
        payload = {
            "SipAccount": sip_account,
            "DeviceName": device_name,
            "GatewayId": gateway_id,
            "IdDevice": device_id,
        }

        status, data, _ = await self._request("POST", "/eliot/sip/user", payload)

        # 200 OK or 201 Created are both success
        if status not in (200, 201):
            _LOGGER.error("Failed to create SIP user: %d", status)
            return False

        _LOGGER.debug("SIP user created successfully (status: %d)", status)
        return True

    async def request_certificate(
        self, sip_account: str, csr: str
    ) -> tuple[bytes, bytes, datetime]:
        """Request TLS certificate from the API.

        Args:
            sip_account: The SIP account (CommonName)
            csr: CSR in PEM format with spaces instead of newlines

        Returns:
            Tuple of (certificate_pem, ca_chain_pem, expiry_date)
        """
        payload = {
            "CommonName": sip_account,
            "CSR": csr,
        }

        status, data, _ = await self._request(
            "POST", "/eliot/users/cert/signcert", payload
        )

        # Accept 200 OK or 201 Created
        if status not in (200, 201):
            raise BticinoProvisioningError(f"Certificate request failed: {status}")

        if not isinstance(data, bytes):
            raise BticinoProvisioningError("Unexpected response format")

        # Extract from ZIP
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                cert_pem = None
                ca_chain_pem = None

                for name in zf.namelist():
                    content = zf.read(name)
                    if name == "ca-chain.cert.pem":
                        ca_chain_pem = content
                    elif name.endswith(".cert.pem"):
                        cert_pem = content

                if not cert_pem or not ca_chain_pem:
                    raise BticinoProvisioningError("Missing certificate files in ZIP")

                # Parse certificate to get expiry date
                cert = x509.load_pem_x509_certificate(cert_pem)
                expiry = cert.not_valid_after_utc

                return cert_pem, ca_chain_pem, expiry

        except zipfile.BadZipFile:
            raise BticinoProvisioningError("Invalid certificate response format")

    async def provision_device(
        self,
        gateway_mac: str | None = None,
        device_name: str = DEFAULT_DEVICE_NAME,
    ) -> ProvisioningResult:
        """Complete device provisioning process.

        This will:
        1. Login to MyHomeWeb
        2. Find the plant and gateway
        3. Create a new SIP user for HomeAssistant
        4. Generate key pair and CSR
        5. Request and obtain TLS certificates

        Args:
            gateway_mac: Optional MAC address to filter gateways (not used for filtering currently)
            device_name: Name for the new device

        Returns:
            ProvisioningResult with all necessary configuration
        """
        # Step 1: Login
        await self.login()

        # Step 2: Get plants
        plants = await self.get_plants()
        if not plants:
            raise BticinoProvisioningError("No plants found for this account")

        plant_id = str(plants[0].get("PlantId"))
        if not plant_id:
            raise BticinoProvisioningError("Could not get plant ID")

        _LOGGER.debug("Found plant: %s", plant_id)

        # Step 3: Get gateways
        gateways = await self.get_gateways(plant_id)
        if not gateways:
            raise BticinoProvisioningError("No gateways found for this plant")

        gateway_id = str(gateways[0].get("GatewayId"))
        if not gateway_id:
            raise BticinoProvisioningError("Could not get gateway ID")

        _LOGGER.debug("Found gateway: %s", gateway_id)

        # Step 4: Generate device ID and SIP account
        device_id = secrets.token_hex(6).upper()
        email_part = self._email.replace("@", "-").replace(".", "-")
        sip_account = f"{email_part}-{device_id}@{gateway_id}.bs.iotleg.com"

        _LOGGER.debug("Creating SIP account: %s", sip_account)

        # Step 5: Create SIP user
        if not await self.create_sip_user(
            sip_account, device_name, gateway_id, device_id
        ):
            raise BticinoProvisioningError("Failed to create SIP user")

        # Step 6: Get SIP password from the created user
        sip_users = await self.get_sip_users(plant_id, gateway_id)
        sip_password = None
        for user in sip_users:
            if user.get("SipAccount") == sip_account:
                sip_password = user.get("SipPassword")
                break

        if not sip_password:
            raise BticinoProvisioningError("Could not retrieve SIP password")

        # Step 7: Generate key pair and CSR
        private_key_pem, csr = _generate_key_and_csr(sip_account)

        # Step 8: Request certificate
        cert_pem, ca_chain_pem, cert_expiry = await self.request_certificate(
            sip_account, csr
        )

        # Build result
        sip_domain = f"{gateway_id}.bs.iotleg.com"

        return ProvisioningResult(
            plant_id=plant_id,
            gateway_id=gateway_id,
            sip_account=sip_account,
            sip_password=sip_password,
            device_id=device_id,
            client_cert=cert_pem.decode("utf-8"),
            client_key=private_key_pem.decode("utf-8"),
            ca_cert=ca_chain_pem.decode("utf-8"),
            cert_expiry=cert_expiry,
            sip_domain=sip_domain,
            sip_username=sip_account,
        )

    async def renew_certificate(
        self,
        sip_account: str,
    ) -> tuple[str, str, str, datetime]:
        """Renew TLS certificate for an existing SIP account.

        Args:
            sip_account: The existing SIP account

        Returns:
            Tuple of (client_cert, client_key, ca_cert, expiry_date) as strings
        """
        # Login first
        await self.login()

        # Generate new key pair and CSR
        private_key_pem, csr = _generate_key_and_csr(sip_account)

        # Request new certificate
        cert_pem, ca_chain_pem, cert_expiry = await self.request_certificate(
            sip_account, csr
        )

        return (
            cert_pem.decode("utf-8"),
            private_key_pem.decode("utf-8"),
            ca_chain_pem.decode("utf-8"),
            cert_expiry,
        )


def _generate_key_and_csr(common_name: str) -> tuple[bytes, str]:
    """Generate RSA private key and CSR.

    Returns:
        Tuple of (private_key_pem, csr_with_spaces)
    """
    # Generate RSA 2048-bit key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Build CSR with BTicino's expected subject
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Italy"),
                    x509.NameAttribute(NameOID.LOCALITY_NAME, "Varese"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Bticino spa"),
                    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "DSI-PQS"),
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                ]
            )
        )
        .sign(private_key, hashes.SHA256())
    )

    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")

    # Replace newlines with spaces as BTicino API expects
    csr_with_spaces = csr_pem.replace("\n", " ")

    return private_key_pem, csr_with_spaces
