# Viewtron IP Camera → Home Assistant

Home Assistant integration for Viewtron AI security cameras. License plate recognition, person/vehicle detection, and face detection — delivered as native HA sensors via MQTT auto-discovery.

The camera does all AI processing on-device. No Frigate, no Coral TPU, no cloud API, no subscription.

## What It Does

Your Viewtron camera detects a license plate, person, vehicle, or face → this bridge receives the event → Home Assistant gets a sensor update. Automations take it from there.

```
Viewtron IP Camera → HTTP POST (XML) → viewtron_bridge.py → MQTT → Home Assistant
```

**Example automations:**
- Garage door opens when your plate is recognized
- Phone notification when unknown vehicle arrives
- Driveway lights turn on when person detected at night
- Doorbell alert with face attributes

## Supported Detection Types

| Detection | HA Entity | Data |
|-----------|-----------|------|
| **License Plate (LPR)** | `sensor.viewtron_*_plate` | Plate number, authorized/not authorized, vehicle brand/color/type |
| **Person/Vehicle Intrusion** | `binary_sensor.viewtron_*_intrusion` | Zone entry, exit, line crossing, loitering |
| **Face Detection** | `binary_sensor.viewtron_*_face` | Age, sex, glasses, mask |
| **Object Counting** | `sensor.viewtron_*_counting` | People/vehicle count by line or area |

All entities auto-discover via MQTT — no manual YAML configuration.

## Setup

### 1. Install

```bash
git clone https://github.com/mikehaldas/viewtron-home-assistant.git
cd viewtron-home-assistant
pip install -r requirements.txt
```

### 2. MQTT Broker

You need an MQTT broker. Most HA users already have Mosquitto — it's the most common HA add-on.

**HAOS users:** Settings → Add-ons → Mosquitto broker → Install

**Docker/Linux users:**
```bash
docker run -d --name mosquitto --restart unless-stopped \
  -p 1883:1883 eclipse-mosquitto:2 \
  sh -c 'echo -e "listener 1883\nallow_anonymous true" > /mosquitto/config/mosquitto.conf && exec mosquitto -c /mosquitto/config/mosquitto.conf'
```

Then add the MQTT integration in HA: Settings → Devices & Services → Add Integration → MQTT → broker: `localhost`, port: `1883`.

### 3. Configure

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml`:

```yaml
bridge_port: 5002        # port this bridge listens on

mqtt:
  enabled: true
  broker: localhost       # your MQTT broker
  port: 1883

home_assistant:
  url: http://localhost:8123
  webhooks:               # optional — for event-triggered automations
    lpr: viewtron-lpr
    intrusion: viewtron-intrusion
```

### 4. Configure Your Camera

In the camera's web interface, set the HTTP Post / Alarm Server:
- **Server IP:** the machine running this bridge
- **Port:** `5002` (or whatever you set in config.yaml)
- **Path:** `/API`

Setup guide: [IP Camera HTTP Post Configuration](https://videos.cctvcamerapros.com/support/topic/ip-camera-api-webbooks)

### 5. Run

```bash
python3 viewtron_bridge.py
```

The first time a camera sends an event, a **Viewtron** device appears in HA with sensors for each detection type. No restart needed.

## How It Works

Viewtron AI cameras run detection on-device (ALPR, face detection, human/vehicle classification) and send HTTP POST events with XML payloads. This bridge:

1. Receives the XML event
2. Parses it using the [viewtron](https://github.com/mikehaldas/viewtron-python) Python SDK
3. Converts to JSON
4. Publishes to MQTT with HA auto-discovery config
5. Optionally forwards to HA webhook triggers

The bridge handles both **IP Camera direct (v1.x)** and **NVR forwarded (v2.0)** event formats automatically.

## LPR Plate Data

The LPR sensor shows the last detected plate number. The Plate Status sensor shows whether the plate is on the camera's whitelist:

| Sensor | Value | Persists |
|--------|-------|----------|
| **License Plate** | `ABC1234` | Yes — shows last plate until next detection |
| **Plate Status** | `Authorized` or `Not Authorized` | Yes |

The camera maintains the whitelist — add plates via the camera web interface, NVR, or the [viewtron Python SDK](https://github.com/mikehaldas/viewtron-python):

```python
from viewtron import ViewtronCamera

camera = ViewtronCamera("192.168.0.20", "admin", "password")
camera.login()
camera.add_plate("ABC1234", owner="Mike", list_type="whiteList")
```

## Example Automations

See [`example_automations.yaml`](example_automations.yaml) for ready-to-use HA automations.

**Garage door opener (webhook):**

```yaml
- alias: "Open garage for authorized plates"
  trigger:
    - platform: webhook
      webhook_id: viewtron-lpr
      local_only: true
  condition:
    - condition: template
      value_template: "{{ trigger.json.plate_authorized == true }}"
  action:
    - service: cover.open_cover
      target:
        entity_id: cover.garage_door
```

**Unknown vehicle alert (MQTT sensor):**

```yaml
- alias: "Alert on unknown vehicle"
  trigger:
    - platform: state
      entity_id: sensor.viewtron_ipc_plate_status
      to: "Not Authorized"
  action:
    - service: notify.mobile_app_phone
      data:
        title: "Unknown vehicle"
        message: "Plate {{ states('sensor.viewtron_ipc_plate') }} detected"
```

## Why Not Frigate?

| | Frigate Stack | This Integration |
|---|---|---|
| Where AI runs | Your server (needs Coral TPU) | On the camera |
| Software layers | 5-6 (Frigate, Docker, MQTT, PaddleOCR, YAML) | 1 (this bridge) |
| LPR accuracy | Depends on camera angle + software tuning | Purpose-built LPR camera with IR illumination |
| Night plates | Struggles (common complaint) | Dedicated plate IR illumination + headlight compensation |
| Setup time | Hours of YAML config | Minutes |
| Subscription | Free | Free |
| Hardware cost | Camera + server + Coral TPU ($25-100) | Just the camera |

Frigate is great for general object detection across many camera brands. This integration is purpose-built for Viewtron cameras that do the AI on-device and push structured event data.

## Compatible Cameras

Any Viewtron IP camera or NVR with HTTP Post support:

- **[LPR-IP4](https://www.cctvcamerapros.com/LPR-Camera-p/lpr-ip4.htm)** — 4MP LPR camera, 20-90 ft range, on-camera ALPR
- **[AI security cameras](https://www.cctvcamerapros.com/AI-security-cameras-s/1512.htm)** — Person/vehicle detection, face detection, intrusion zones
- **[NVRs](https://www.cctvcamerapros.com/IP-Camera-NVRs-s/1472.htm)** — Forward events from all connected cameras

Product page: [www.Viewtron.com](https://www.Viewtron.com)

## Related Projects

- **[viewtron](https://github.com/mikehaldas/viewtron-python)** — Python SDK for Viewtron camera API (`pip install viewtron`)
- **[IP-Camera-API](https://github.com/mikehaldas/IP-Camera-API)** — Alarm server, API documentation, XML examples

## Author

Mike Haldas — [CCTV Camera Pros](https://www.cctvcamerapros.com)
mike@cctvcamerapros.net
