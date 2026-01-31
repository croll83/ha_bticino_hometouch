# BTicino Hometouch - Home Assistant Integration

Custom Home Assistant integration for BTicino Door Entry Touch intercom systems.

## Features

- **Easy Setup**: Just enter your BTicino email and password - no manual certificate extraction needed!
- **Automatic Provisioning**: The integration automatically creates a dedicated device and obtains TLS certificates
- **Auto Certificate Renewal**: Certificates are automatically renewed 30 days before expiry
- **Push Notifications**: Receive notifications on the HA Companion app when someone rings the doorbell
- **Video Streaming**: View camera feeds (configurable number of outdoor stations)
- **Bidirectional Audio**: Talk through the intercom via WebRTC
- **Door Unlock**: Configurable number of lock buttons
- **Call Control**: Answer and hangup buttons
- **Doorbell Sensor**: Binary sensor that activates when someone rings
- **Connection Status**: Binary sensor showing SIP registration status

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Search for "BTicino Hometouch" and install
3. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/bticino_hometouch` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

### Step 1: Add the Integration

1. Go to **Settings -> Devices & Services -> Add Integration**
2. Search for "BTicino Hometouch"
3. Click on it

### Step 2: Enter Your Credentials

You only need to provide:

| Field | Description | Example |
|-------|-------------|---------|
| Email | Your BTicino account email | yourname@gmail.com |
| Password | Your BTicino account password | yourpassword |
| Gateway MAC | MAC address of your gateway | 00:03:50:B2:0E:1F |
| Number of Cameras | How many outdoor stations you have | 1 |
| Number of Locks | How many door locks you have | 1 |

### Step 3: Wait for Provisioning

Click "Submit" and the integration will automatically:

1. Login to BTicino servers
2. Discover your plant and gateway
3. Create a new SIP device called "HomeAssistant"
4. Generate and obtain TLS certificates
5. Configure everything for you

That's it! Your intercom is ready to use.

## Entities Created

Entities are dynamically created based on your configuration:

| Entity Pattern | Type | Description |
|----------------|------|-------------|
| `button.bticino_hometouch_unlock_door_N` | Button | Unlock door N |
| `button.bticino_hometouch_answer_call` | Button | Answer incoming call |
| `button.bticino_hometouch_hangup_call` | Button | Hangup current call |
| `camera.bticino_hometouch_outdoor_station_N` | Camera | Camera N feed |
| `binary_sensor.bticino_hometouch_doorbell` | Binary Sensor | Doorbell ring detection |
| `binary_sensor.bticino_hometouch_connection` | Binary Sensor | SIP connection status |

## Events

The integration fires these events:

- `bticino_hometouch_incoming_call` - When someone rings the doorbell
- `bticino_hometouch_call_ended` - When a call ends

## Automatic Certificate Renewal

The integration automatically monitors certificate expiry and renews them 30 days before they expire. This happens in the background - no action required from you!

When certificates are renewed:
1. The integration logs in to BTicino servers
2. Requests new certificates for your device
3. Updates the stored configuration
4. Restarts the SIP connection with new certificates

## Example Dashboard

```yaml
type: vertical-stack
cards:
  # Title
  - type: markdown
    content: "# Intercom"

  # Camera view
  - type: picture-entity
    entity: camera.bticino_hometouch_outdoor_station_1
    camera_view: live

  # Control buttons
  - type: horizontal-stack
    cards:
      - type: button
        entity: button.bticino_hometouch_unlock_door_1
        name: Open Door
        icon: mdi:door-open
        show_state: false

      - type: button
        entity: button.bticino_hometouch_answer_call
        name: Answer
        icon: mdi:phone
        show_state: false

      - type: button
        entity: button.bticino_hometouch_hangup_call
        name: Hangup
        icon: mdi:phone-hangup
        show_state: false

  # Status sensors
  - type: entities
    entities:
      - entity: binary_sensor.bticino_hometouch_connection
        name: Connection
      - entity: binary_sensor.bticino_hometouch_doorbell
        name: Doorbell
```

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
              - action: "DISMISS"
                title: "Dismiss"
```

## Technical Notes

### Video Streaming

The BTicino system uses SRTP (encrypted RTP) for video streaming. This integration uses go2rtc to convert SRTP to WebRTC for low-latency streaming with bidirectional audio.

### Door Unlock Protocol

Door unlock commands are sent via SIP MESSAGE to `sip:MHT@gateway`:
- Lock type A: Commands `*8*19*4##` and `*8*20*4##`
- Lock type B: Commands `*8*21*4##` and `*8*22*4##`

## Troubleshooting

### "Invalid email or password"

Make sure you're using the same credentials you use in the Door Entry Touch mobile app.

### "Could not connect to BTicino servers"

- Check your internet connection
- Verify that BTicino servers are reachable
- Try again later - servers might be temporarily unavailable

### Video not working

Make sure go2rtc is installed and running. See the detailed installation guide for go2rtc setup.

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
