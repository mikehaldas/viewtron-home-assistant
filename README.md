# Home Assistant Security Camera Integration for Viewtron AI Cameras

This Home Assistant integration for Viewtron AI security cameras includes license plate recognition, person/vehicle detection, and face detection — delivered as native HA sensors via MQTT auto-discovery.

The Viewtron camera does all AI inference processing on-device. No Frigate, no Coral TPU, no cloud API, no subscription.

## What It Does

Your Viewtron camera detects a license plate, person, vehicle, or face → this bridge receives the event → Home Assistant gets a sensor update. Automations take it from there.

```
Viewtron IP Camera → HTTP POST (XML) → viewtron_bridge.py → MQTT → Home Assistant
```

**Example automations:**
- Garage door opens when your license plate is recognized
- Phone notification when vehicle arrives with an unknown plate
- Driveway lights turn on when person detected at night
- Access control and alarms based on facial recognition

## Supported Detection Types

| Detection | HA Entity | Data |
|-----------|-----------|------|
| **License Plate Recognition (LPR)** | `sensor.viewtron_*_plate` | Plate number, authorized/not authorized, vehicle brand/color/type |
| **Person Detection/Vehicle Detection** | `binary_sensor.viewtron_*_intrusion` | Zone entry, exit, line crossing, loitering, intrusion detection |
| **Face Detection** | `binary_sensor.viewtron_*_face` | Coming soon — face recognition with NVR database |
| **Object Counting** | `sensor.viewtron_*_counting` | People/vehicle count by line or area |

All entities auto-discover via MQTT — no manual YAML configuration.

## Setup

### 1. Install Viewtron Home Assistant Camera Integration

```bash
git clone https://github.com/mikehaldas/viewtron-home-assistant.git
cd viewtron-home-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. MQTT Broker

The bridge communicates with Home Assistant through an MQTT broker. If you already have Mosquitto running (most HA users do), skip to step 3.

**HAOS users (Home Assistant OS):**

1. Go to **Settings → Add-ons → Add-on Store** (bottom right)
2. Search for **Mosquitto broker** and click **Install**
3. Click **Start**
4. Go to **Settings → Devices & Services** — HA will auto-discover the Mosquitto add-on and prompt you to configure MQTT

**Docker / Linux HA users:**

```bash
docker run -d --name mosquitto --restart unless-stopped \
  -p 1883:1883 eclipse-mosquitto:2 \
  sh -c 'echo -e "listener 1883\nallow_anonymous true" > /mosquitto/config/mosquitto.conf && exec mosquitto -c /mosquitto/config/mosquitto.conf'
```

Then add the MQTT integration in HA: **Settings → Devices & Services → Add Integration → MQTT** → broker: `localhost`, port: `1883`.

### 3. Configure the Bridge

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
### 4. Setup the IP Camera on Your Network

Once your Viewtron LPR camera or general purpose Viewtron AI camera is connected to your network, you may need to use the Viewtron IP installer tool to locate the camera on your network. You can [download IP installer tools for Mac and Windows on this page](https://www.cctvcamerapros.com/viewtron-software-apps-s/1482.htm).

![Locate IP camera on network](https://videos.cctvcamerapros.com/wp-content/files/IP-Camera-Login-1024x546.jpg)

### 5. Configure License Plate Detection

In the camera's web interface, go to **Config → License Plate Detection**. Enable detection, draw a detection zone covering the area where plates will be visible, and set the minimum/maximum plate size parameters. Leave adequate buffer margins around the expected plate area.

![Configure LPR camera detection](https://videos.cctvcamerapros.com/wp-content/files/configure-LPR-Camera-1024x546.jpg)

![Enable license plate detection](https://videos.cctvcamerapros.com/wp-content/files/enable-license-plate-detection-1024x546.jpg)

**LPR installation best practices:**
- Mount the camera at a 15-30° angle to the vehicle path — avoid head-on or extreme side angles
- Keep the plate within 20-90 ft of the camera (LPR-IP4 range)
- Use the camera's motorized zoom to frame the plate area — plates should fill roughly 10-15% of the frame width
- Night performance is built in (IR illumination + headlight compensation) — no additional lighting needed

### 6. License Plate Database (Optional)

To use the authorized/not authorized feature in Home Assistant, add plates to the camera's allow list. Go to **License Plate Detection → Add**, enter the plate number, and select **Allow list** from the dropdown. You can also import plates in bulk via CSV.

![Camera license plate database](https://videos.cctvcamerapros.com/wp-content/files/camera-license-plate-database-1024x546.jpg)

![Add license plate to database](https://videos.cctvcamerapros.com/wp-content/files/add-license-plate-database.jpg)

You can also manage plates programmatically via the [viewtron Python SDK](https://github.com/mikehaldas/viewtron-python-sdk):

```python
from viewtron import ViewtronCamera

camera = ViewtronCamera("192.168.0.20", "admin", "password")
camera.login()
camera.add_plate("ABC1234", owner="Mike", list_type="whiteList")
```

### 7. Configure the HTTP Post Webhook Server

Navigate to **Network → HTTP POST** and click **Edit**. Click **Add** and enter the bridge connection details:

- **Server IP:** the machine running this bridge
- **Port:** `5002` (or whatever you set in config.yaml)
- **Path:** `/API`

Select the alarm types you want to forward (License Plate, Intrusion, Face Detection, etc.).

![HTTP Post server add](https://videos.cctvcamerapros.com/wp-content/files/http-post-server-add-1024x432.jpg)

![HTTP Post settings](https://videos.cctvcamerapros.com/wp-content/files/http-post-settings-1024x545.jpg)

Detailed setup guide: [LPR Camera API Setup](https://videos.cctvcamerapros.com/v/lpr-camera-api.html)

### 8. Run the Bridge

```bash
source venv/bin/activate
python3 viewtron_bridge.py
```

The first time a camera sends an event, a **Viewtron** device appears in HA with sensors for each detection type. No restart needed.

### 9. Run on Boot (Production)

To keep the bridge running after reboots:

**Docker / Linux users — systemd service:**

```bash
sudo tee /etc/systemd/system/viewtron-bridge.service > /dev/null << 'EOF'
[Unit]
Description=Viewtron Home Assistant Bridge
After=network.target docker.service

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/path/to/viewtron-home-assistant
ExecStart=/path/to/viewtron-home-assistant/venv/bin/python3 viewtron_bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable viewtron-bridge
sudo systemctl start viewtron-bridge
```

Replace `YOUR_USERNAME` and `/path/to/viewtron-home-assistant` with your actual values.

Check status: `sudo systemctl status viewtron-bridge`
View logs: `sudo journalctl -u viewtron-bridge -f`

**HAOS users — Docker container:**

HAOS doesn't support systemd. Run the bridge as a Docker container instead:

```bash
docker run -d --name viewtron-bridge --restart unless-stopped \
  --network host \
  -v /path/to/config.yaml:/app/config.yaml \
  python:3.12-slim \
  sh -c 'pip install viewtron paho-mqtt pyyaml requests && \
    cd /app && python3 -c "
from urllib.request import urlretrieve
urlretrieve(\"https://raw.githubusercontent.com/mikehaldas/viewtron-home-assistant/main/viewtron_bridge.py\", \"viewtron_bridge.py\")
" && python3 viewtron_bridge.py'
```

A proper HA Add-on (click-to-install from the Add-on Store) is planned for a future release.

## How It Works

Viewtron AI cameras run detection on-device (ALPR, face detection, human/vehicle classification) and send HTTP POST events with XML payloads. This bridge:

1. Receives the XML event
2. Parses it using the [viewtron](https://github.com/mikehaldas/viewtron-python-sdk) Python SDK
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

## Compatible Cameras

Any Viewtron IP camera or NVR with HTTP Post support:

- **[LPR-IP4](https://www.cctvcamerapros.com/LPR-Camera-p/lpr-ip4.htm)** — 4MP LPR camera, 20-90 ft range, on-camera ALPR
- **[AI security cameras](https://www.cctvcamerapros.com/AI-security-cameras-s/1512.htm)** — Person/vehicle detection, face detection, intrusion zones
- **[NVRs](https://www.cctvcamerapros.com/IP-Camera-NVRs-s/1472.htm)** — Forward events from all connected cameras

Product page: [www.Viewtron.com](https://www.Viewtron.com)

## Related Projects

- **[viewtron](https://github.com/mikehaldas/viewtron-python-sdk)** — Python SDK for Viewtron camera API (`pip install viewtron`)
- **[IP-Camera-API](https://github.com/mikehaldas/IP-Camera-API)** — Alarm server, API documentation, XML examples

## Author

Mike Haldas — [CCTV Camera Pros](https://www.cctvcamerapros.com)
mike@cctvcamerapros.net
