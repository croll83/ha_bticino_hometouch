"""Constants for the BTicino Hometouch integration."""

DOMAIN = "bticino_hometouch"

# Configuration keys - User input (simple setup)
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_GATEWAY_MAC = "gateway_mac"
CONF_NUM_CAMERAS = "num_cameras"
CONF_NUM_LOCKS = "num_locks"
CONF_APARTMENT_CODE = "apartment_code"

# Configuration keys - Auto-discovered/generated
CONF_SIP_SERVER = "sip_server"
CONF_SIP_PORT = "sip_port"
CONF_SIP_USERNAME = "sip_username"
CONF_SIP_PASSWORD = "sip_password"
CONF_SIP_DOMAIN = "sip_domain"
CONF_GATEWAY_ADDRESS = "gateway_address"
CONF_CLIENT_CERT = "client_cert"
CONF_CLIENT_KEY = "client_key"
CONF_CA_CERT = "ca_cert"
CONF_LOCK_COMMANDS = "lock_commands"

# Auto-provisioning data
CONF_PLANT_ID = "plant_id"
CONF_GATEWAY_ID = "gateway_id"
CONF_SIP_ACCOUNT = "sip_account"
CONF_DEVICE_ID = "device_id"
CONF_CERT_EXPIRY = "cert_expiry"

# Default values
DEFAULT_SIP_SERVER = "sipserver.bs.iotleg.com"
DEFAULT_SIP_PORT = 5228
DEFAULT_NUM_CAMERAS = 1
DEFAULT_NUM_LOCKS = 1
DEFAULT_DEVICE_NAME = "HomeAssistant"
DEFAULT_APARTMENT_CODE = ""  # Empty means no apartment code in command

# BTicino API
BTICINO_API_BASE = "https://www.myhomeweb.com"
BTICINO_API_PORT = 443

# Lock command types based on CID (from decompiled app)
# CID 10060 or 3008 -> commands *8*19 and *8*20
# CID 2009 -> commands *8*21 and *8*22
LOCK_COMMAND_TYPE_A = {
    "open": "*8*19",
    "close": "*8*20",
}
LOCK_COMMAND_TYPE_B = {
    "open": "*8*21",
    "close": "*8*22",
}

# SIP message destination for door control
SIP_MHT_PREFIX = "sip:MHT@"

# SDP attributes for camera control
SDP_ATTR_DEVADDR = "DEVADDR"
SDP_ATTR_CAMERASLIDING = "CAMERASLIDING"

# Events
EVENT_INCOMING_CALL = f"{DOMAIN}_incoming_call"
EVENT_CALL_ENDED = f"{DOMAIN}_call_ended"

# Services
SERVICE_UNLOCK_DOOR = "unlock_door"
SERVICE_ANSWER_CALL = "answer_call"
SERVICE_HANGUP_CALL = "hangup_call"
SERVICE_SWITCH_CAMERA = "switch_camera"

# Attributes
ATTR_LOCK_ID = "lock_id"
ATTR_CAMERA_ID = "camera_id"
ATTR_CALLER_ID = "caller_id"

# Certificate renewal
CERT_RENEWAL_DAYS_BEFORE_EXPIRY = 30
