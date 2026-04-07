# Home Assistant Security Camera Integration for Viewtron AI Cameras

This Home Assistant integration for Viewtron AI security cameras includes license plate recognition, person/vehicle detection, and face detection — delivered as native HA sensors via MQTT auto-discovery.

**Please Note: all of the integration and testing so far has been done with our license plate recognition camera (LPR). The other types of AI cameras and events will be added asap.**

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

### What Home Assistant Receives

![Viewtron LPR camera in Home Assistant](https://videos.cctvcamerapros.com/wp-content/files/home-assistant-LPR-camera.jpg)

When a license plate is detected, two sensors appear on the Viewtron IP camera device in Home Assistant:

| Sensor | What It Shows | Example |
|--------|---------------|---------|
| **License Plate** | The plate number that was read | `ABC1234` |
| **Plate Status** | Whether the plate is in the camera's database | `Authorized` |

The **Plate Status** sensor has four possible values:

| Status | Meaning |
|--------|---------|
| **Authorized** | Plate is on the camera's allow list |
| **Blacklisted** | Plate is on the camera's block list |
| **Temporary** | Plate is on the temporary list and within its valid date range |
| **Unknown** | Plate is not in the camera's database |

These are the two inputs your Home Assistant automations use. For example, when Plate Status changes to `Authorized`, open the garage door. When it changes to `Unknown`, send a notification.

The plate database is managed directly on the LPR camera — add, remove, and organize plates through the camera's web interface. See [License Plate Database Setup](#3-license-plate-database-setup-optional) in the camera setup section below for instructions.

## Supported Detection Types

| Detection | HA Entity | Status |
|-----------|-----------|--------|
| **License Plate Recognition (LPR)** | `sensor.viewtron_*_plate` | **Tested and supported** — plate number, authorized/not authorized, vehicle brand/color/type |
| **Person Detection/Vehicle Detection** | `binary_sensor.viewtron_*_intrusion` | Coming soon — zone entry, exit, line crossing, loitering, intrusion detection |
| **Face Detection** | `binary_sensor.viewtron_*_face` | Coming soon — face recognition with NVR database |
| **Object Counting** | `sensor.viewtron_*_counting` | Coming soon — people/vehicle count by line or area |

LPR is fully tested end-to-end with the Viewtron LPR-IP4 camera. The other detection types use the same bridge architecture and will be documented as testing is completed. All entities auto-discover via MQTT — no manual YAML configuration.

## Setup

### Option A: Docker Install (Recommended)

The Docker install handles everything — MQTT config, boot persistence, and the bridge itself — in one command.

**Prerequisites:** Docker installed, MQTT broker running (see [MQTT Broker](#mqtt-broker) below if you don't have one).

```bash
docker run -d --name viewtron-bridge --restart unless-stopped \
  --network host \
  -e BRIDGE_PORT=5002 \
  -e MQTT_BROKER=localhost \
  ghcr.io/mikehaldas/viewtron-bridge
```

The bridge is running. Skip to [Camera Setup](#camera-setup).

**Additional env vars (optional):**

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_PORT` | `5002` | Port the bridge listens on |
| `MQTT_BROKER` | `localhost` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | *(empty)* | MQTT username (if broker requires auth) |
| `MQTT_PASSWORD` | *(empty)* | MQTT password |
| `SAVE_IMAGES` | `false` | Save event images to disk |

### Option B: Manual Install

```bash
git clone https://github.com/mikehaldas/viewtron-home-assistant.git
cd viewtron-home-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml
```

Edit `config.yaml` with your MQTT broker address and desired port, then continue to [Camera Setup](#camera-setup).

To run the bridge after setup:

```bash
source venv/bin/activate
python3 viewtron_bridge.py
```

To run on boot, create a systemd service:

```bash
sudo tee /etc/systemd/system/viewtron-bridge.service > /dev/null << 'EOF'
[Unit]
Description=Viewtron Home Assistant Bridge
After=network.target

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

### MQTT Broker

The bridge requires an MQTT broker. If you already have Mosquitto running (most HA users do), you're all set.

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

---

## Camera Setup

### 1. Locate Your IP Camera on Your Network

If you need to locate the camera on your network, you can [download the Viewtron network IP installer tool here](https://www.cctvcamerapros.com/viewtron-software-apps-s/1482.htm). We have a Windows and Mac IP camera network finder / IP installer tool available for Viewtron IP cameras.

![Locate IP camera on network](https://videos.cctvcamerapros.com/wp-content/files/IP-Camera-Login-1024x546.jpg)

Log into the camera using its IP address in a web browser. Enter the camera's user ID and password, then login.

### 2. License Plate Detection Configuration

Please note that these instructions are specifically for [Viewtron license plate recognition cameras](https://www.cctvcamerapros.com/License-Plate-Recognition-Systems-s/1518.htm). The Viewtron LPR-IP4 (4mp LPR) is the model that we have tested with and highly recommend. This repo will be updated with setup instructions for human detection, car detection, and face recognition AI cameras and events as soon as possible.

![Configure LPR camera detection](https://videos.cctvcamerapros.com/wp-content/files/configure-LPR-Camera-1024x546.jpg)

Navigate to the **Config** tab, then select **License Plate Detection**.

![Enable license plate detection](https://videos.cctvcamerapros.com/wp-content/files/enable-license-plate-detection-1024x546.jpg)

On the License Plate Detection screen:

1. Check **Enable**
2. Click on the **Draw Area** button
3. Draw the license plate detection zone
4. Click on the **Draw Target Size** button, then enter the min and max sizes for plates

The min sizes should be slightly smaller than the realistic size of a license plate. The max sizes should be slightly larger. These do not need to be exact — provide an adequate buffer rather than exact measurements. Click **Save** when done.

**LPR installation best practices:**
Here are some best practices when installing your LPR camera.
- Mount the camera at a 15-30° angle to the vehicle path — avoid head-on or extreme side angles
- Keep the plate within 20-90 ft of the camera (LPR-IP4 range)
- Use the camera's motorized zoom to frame the plate area — plates should fill roughly 10-15% of the frame width
- Night performance is built in (IR illumination + headlight compensation) — no additional lighting needed

For more details, please refer to our complete [LPR Camera Installation Guide](https://videos.cctvcamerapros.com/v/anpr-lpr-camera-installation.html).

### 3. License Plate Database Setup (Optional)

This step is optional. If you want to use the LPR camera's built-in database to manage a list of authorized plates, this is how you set that up. If you do not set up a list of authorized license plates in the database, the camera still sends all of the other data in the XML post except the authorization info.

To add plates to the license plate database, click on the **License Plate Detection** link on the left. Then, click on the **Add** button. You can also click on the **Bulk Entry** button if you want to upload a large list of license plates using a CSV file.

![Camera license plate database](https://videos.cctvcamerapros.com/wp-content/files/camera-license-plate-database-1024x546.jpg)

On the Vehicle Information screen, enter the license plate number and select **Allow list** from the List Type dropdown if you want this to be an authorized plate. The rest of the information is optional. Click **Save** when done. Repeat this process for each license plate that you want to add to the database, or use the Bulk Entry to upload a CSV list of plates.

![Add license plate to database](https://videos.cctvcamerapros.com/wp-content/files/add-license-plate-database.jpg)

Plates on the allow list will show as `Authorized` in Home Assistant. You can also manage plates programmatically via the [viewtron Python SDK](https://github.com/mikehaldas/viewtron-python-sdk):

```python
from viewtron import ViewtronCamera

camera = ViewtronCamera("192.168.0.20", "admin", "password")
camera.login()
camera.add_plate("ABC1234", owner="Mike", list_type="whiteList")
```

### 4. Configure the HTTP Post Webhook Server

In the camera's web interface, go to the **Network** section, then select **HTTP POST**. On the HTTP Post screen, click on the **Edit** button. Then click **Add** and enter the API server's IP address, port, and path:

- **Server IP:** the machine running this bridge
- **Port:** `5002` (or whatever you set as BRIDGE_PORT)
- **Path:** `/API`

You can configure which alarm types and data to send. Select the detection types you want forwarded to Home Assistant (License Plate, Intrusion, Face Detection, etc.). When done, click the **Save** button.

![HTTP Post server add](https://videos.cctvcamerapros.com/wp-content/files/http-post-server-add-1024x432.jpg)

![HTTP Post settings](https://videos.cctvcamerapros.com/wp-content/files/http-post-settings-1024x545.jpg)

Detailed setup guide: [LPR Camera API Setup](https://videos.cctvcamerapros.com/v/lpr-camera-api.html)

### 5. Verify

The first time a camera sends an event, a **Viewtron** device appears in HA under **Settings → Devices & Services → MQTT** with sensors for each detection type. No restart needed.

---

## How It Works

Viewtron AI cameras run detection on-device (ALPR, face detection, human/vehicle classification) and send HTTP POST events with XML payloads. This bridge:

1. Receives the XML event
2. Parses it using the [viewtron](https://github.com/mikehaldas/viewtron-python-sdk) Python SDK
3. Converts to JSON
4. Publishes to MQTT with HA auto-discovery config
5. Optionally forwards to HA webhook triggers

The bridge handles both **IP Camera direct (v1.x)** and **NVR forwarded (v2.0)** event formats automatically.

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
      value_template: "{{ trigger.json.plate_status == 'Authorized' }}"
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
      to: "Unknown"
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
