# BTicino Hometouch - Home Assistant Integration

Custom Home Assistant integration for BTicino Door Entry Touch intercom systems.

## Features

- **Push Notifications**: Receive notifications on the HA Companion app when someone rings the doorbell
- **Video Streaming**: View camera feeds (configurable number of outdoor stations)
- **Bidirectional Audio**: Talk through the intercom via WebRTC
- **Door Unlock**: Configurable number of lock buttons
- **Call Control**: Answer and hangup buttons
- **Doorbell Sensor**: Binary sensor that activates when someone rings
- **Connection Status**: Binary sensor showing SIP registration status

## Prerequisites

### 1. Extract TLS Certificates

This integration requires the TLS client certificates extracted from the "Door Entry for HomeTouch" Bticino Android app (https://apkpure.com/door-entry-for-hometouch/com.bticino.doorentrytouch/download). Use the `decrypt_bticino_certs.py` tool to extract them:

```bash
python decrypt_bticino_certs.py -c bticino_dump/files/config2.plant -d bticino_dump/files/certs/
```

You'll need these files:
- `client.cert.pem` - Client certificate
- `client.chain.pem` - CA certificate chain
- `private.pem` - Private key

### 2. SIP Credentials

From the Door Entry app data, you need:
- **SIP Username**: e.g., `user@email.com-MACADDRESS@123456.bs.iotleg.com`
- **SIP Password**: The decrypted SIP password from the database
- **SIP Domain**: e.g., `123456.bs.iotleg.com`
- **Gateway Address**: e.g., `SERIALNUMBER.bs.iotleg.com`

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Search for "BTicino Hometouch" and install
3. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/bticino_hometouch` folder to your Home Assistant's `custom_components` directory
2. Copy `www/bticino-hometouch-card.js` to `/config/www/`
3. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "BTicino Hometouch"
3. Enter your SIP credentials and gateway information
4. Specify the number of outdoor stations and locks in your installation
5. Paste the contents of your certificate files

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| Number of Cameras | How many outdoor stations/cameras | 1 |
| Number of Locks | How many door locks | 1 |

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

## Custom Card

The integration includes a custom Lovelace card for interactive intercom control:

```yaml
type: custom:bticino-hometouch-card
camera_entity: camera.bticino_hometouch_outdoor_station_1
lock_entity: button.bticino_hometouch_unlock_door_1
```

## Example Dashboard

```yaml
type: vertical-stack
cards:
  - type: custom:bticino-hometouch-card
    camera_entity: camera.bticino_hometouch_outdoor_station_1
    lock_entity: button.bticino_hometouch_unlock_door_1
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
          title: "🔔 Doorbell"
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

## License

This integration is provided for personal use only. BTicino is a trademark of Legrand.
