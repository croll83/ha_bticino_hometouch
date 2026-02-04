# BTicino Hometouch - Home Assistant Integration

Custom Home Assistant integration for BTicino Door Entry Touch intercom systems.

## ⚠️ Known Limitations

**Please read carefully before installing:**

| Limitation | Description |
|------------|-------------|
| **No Audio** | Audio is NOT available. The BTicino cloud gateway does not accept audio streams from third-party SIP clients. |
| **Video Latency** | Video has approximately 1-3 seconds of latency (variable depending on network conditions). |
| **On-Demand Only** | Only on-demand video calls initiated from Home Assistant are supported. |
| **Incoming Calls** | Incoming calls are displayed as a banner notification in the custom card, but cannot be answered with video/audio. Use the official BTicino app for full incoming call handling. |
| **Door Unlock Works** | Door unlock commands work reliably, even during incoming calls. |

## Features

- **Easy Setup**: Just enter your BTicino email and password - no manual certificate extraction needed!
- **Automatic Provisioning**: The integration automatically creates a dedicated device and obtains TLS certificates
- **Auto Certificate Renewal**: Certificates are automatically renewed 30 days before expiry
- **WebRTC Video Streaming**: Low-latency video via go2rtc with WebRTC
- **Door Unlock**: Unlock doors directly from Home Assistant
- **Custom Lovelace Card**: Beautiful card with video popup and door controls
- **Incoming Call Notification**: Banner displayed when someone rings the doorbell
- **Doorbell Sensor**: Binary sensor that activates when someone rings
- **Connection Status**: Binary sensor showing SIP registration status

## Requirements

- Home Assistant 2024.11+ (with built-in go2rtc) **OR** go2rtc add-on installed
- FFmpeg with libx264 (included in Home Assistant)
- UDP port 9078 forwarded from your router to Home Assistant

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Search for "BTicino Hometouch" and install
3. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/bticino_hometouch` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

### go2rtc Setup

The integration automatically configures go2rtc streams on first startup. It adds streams `bticino_live_1` through `bticino_live_10` to your `go2rtc.yaml`.

**After first installation**, restart the go2rtc add-on (or Home Assistant) for the streams to be registered.

## Configuration

### Step 1: Add the Integration

1. Go to **Settings -> Devices & Services -> Add Integration**
2. Search for "BTicino Hometouch"
3. Click on it

### Step 2: Enter Your Credentials

| Field | Description | Example |
|-------|-------------|---------|
| Email | Your BTicino account email | yourname@gmail.com |
| Password | Your BTicino account password | yourpassword |
| Gateway MAC | MAC address of your gateway | 00:03:50:B2:0E:1F |
| Public IP | Your public IP for video streaming | 1.2.3.4 |
| Number of Cameras | How many outdoor stations you have | 1 |
| Number of Locks | How many door locks you have | 1 |

### Step 3: Port Forwarding (Router/Firewall)

For video streaming to work, configure your router/firewall:

| Protocol | External Port | Internal Port | Internal IP | Description |
|----------|---------------|---------------|-------------|-------------|
| **UDP** | 9078 | 9078 | Your HA IP (e.g., 192.168.1.18) | SRTP Video Stream |

**Important:**
- The BTicino cloud gateway sends SRTP video to your public IP on port 9078
- Without this port forwarding, video will not work (calls will connect but no video)
- SIP signaling (port 5061) does NOT need forwarding - it's outbound only
- Audio port (7076) is not used since audio is not supported

### Step 4: Add the Custom Card

Add the BTicino Intercom card to your dashboard:

```yaml
type: custom:bticino-intercom-card
title: Citofono
show_title: true
```

## Entities Created

| Entity Pattern | Type | Description |
|----------------|------|-------------|
| `button.citofono_view_video_station_N` | Button | Start video call to station N |
| `button.citofono_unlock_door_N` | Button | Unlock door N |
| `button.citofono_hangup_call` | Button | Hangup current call |
| `camera.bticino_camera_N` | Camera | Camera N entity |
| `binary_sensor.bticino_hometouch_doorbell` | Binary Sensor | Doorbell ring detection |
| `binary_sensor.bticino_hometouch_connection` | Binary Sensor | SIP connection status |

## Events

The integration fires these events:

- `bticino_hometouch_incoming_call` - When someone rings the doorbell
- `bticino_hometouch_door_unlocked` - When a door is successfully unlocked
- `bticino_hometouch_call_ended` - When a video call ends (stream terminated by gateway)

## Custom Card Features

The included custom Lovelace card provides:

- **Station Buttons**: View video and unlock door for each station
- **Incoming Call Banner**: Orange pulsing banner when someone rings
- **Video Popup**: Full-screen video with WebRTC streaming
- **Error Handling**: Specific messages for different error types:
  - "Citofono occupato" (gateway busy - 486)
  - "Timeout connessione" (connection timeout - 408)
  - "Stream terminato" (gateway closed the stream)
- **Visual Feedback**: Success/error animations for door unlock

## Example Automation

```yaml
automation:
  - alias: "Doorbell Notification"
    trigger:
      - platform: event
        event_type: bticino_hometouch_incoming_call
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Doorbell"
          message: "Someone is at the door"
          data:
            actions:
              - action: "UNLOCK_DOOR"
                title: "Unlock"
```

## Technical Notes

### Video Streaming Architecture

```
BTicino Gateway -> SRTP (encrypted) -> Home Assistant -> FFmpeg (re-encode) -> RTSP -> go2rtc -> WebRTC -> Browser
```

The video path:
1. Gateway sends SRTP-encrypted H.264 video to your public IP (port 9078)
2. FFmpeg decrypts SRTP and re-encodes with frequent keyframes to avoid artifacts
3. Video is pushed to go2rtc via RTSP
4. go2rtc serves WebRTC to the browser with low latency

### Why No Audio?

The BTicino cloud gateway (sipserver.bs.iotleg.com) systematically rejects audio SDP offers from non-Linphone clients. This appears to be a server-side restriction that cannot be bypassed without using the official Linphone SDK.

### Door Unlock Protocol

Door unlock commands are sent via SIP MESSAGE using OpenWebNet protocol:
- Lock type A: Commands `*8*19*{WHERE}##` (open) and `*8*20*{WHERE}##` (release)
- Lock type B: Commands `*8*21*{WHERE}##` (open) and `*8*22*{WHERE}##` (release)

**WHERE Address Format:**

The WHERE address is composed of `{Gateway_ID}{Lock_Address}`:
- **Gateway ID**: Found in gateway settings (usually "2")
- **Lock Address**: The address configured for each lock (e.g., 0, 1, 6)

Example: For a gateway with ID `2` and locks at addresses `0`, `1`, `6`:
- Lock 1 (address 0): WHERE = `20` → command `*8*19*20##`
- Lock 2 (address 1): WHERE = `21` → command `*8*19*21##`
- Lock 3 (address 6): WHERE = `26` → command `*8*19*26##`

### How to Find Lock Addresses

1. On the BTicino gateway, go to **Advanced Settings → Videocitofonia (Video Intercom)**
2. Note the Gateway ID (shown in device settings, typically "2")
3. For each lock/door opener, note its configured address
4. Combine them: full address = `{Gateway_ID}{Lock_Address}`

Configure these addresses in the integration options under "Lock Addresses" (comma-separated, e.g., `20, 21, 26`)

## Troubleshooting

### "Invalid email or password"

Make sure you're using the same credentials you use in the Door Entry Touch mobile app.

### Video not working

1. Check that UDP port 9078 is forwarded to your Home Assistant server
2. Verify your public IP is correctly configured
3. Check that go2rtc add-on is running
4. Check Home Assistant logs for errors

### Green artifacts or frozen video

This usually means the keyframe wasn't received. Wait a few seconds for the next keyframe, or restart the stream.

### "Citofono occupato" error

The gateway is busy with another call (possibly from the official app). Wait and try again.

### Enable Debug Logging

Add this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.bticino_hometouch: debug
```

## License

This integration is provided for personal use only. BTicino is a trademark of Legrand.
