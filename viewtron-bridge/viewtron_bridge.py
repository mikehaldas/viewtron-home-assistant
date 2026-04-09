#!/usr/bin/env python3
"""
Viewtron → Home Assistant Bridge

Receives HTTP Post alarm events from Viewtron IP cameras and NVRs,
converts them to JSON, and forwards to Home Assistant via MQTT
(with auto-discovery) and/or webhook triggers.

Architecture:
    Viewtron Camera/NVR → HTTP POST (XML) → This Bridge → MQTT / Webhook → HA

MQTT mode (recommended):
    - Publishes HA auto-discovery configs so entities appear automatically
    - Each camera becomes an HA device with sensors per detection type
    - No YAML needed — entities show up in the HA UI ready to use

Webhook mode:
    - Forwards JSON to HA webhook triggers
    - Automations use trigger.json.plate_number, etc.

Both modes can run simultaneously.

Setup:
    1. pip install viewtron paho-mqtt pyyaml requests
    2. Copy config.yaml.example to config.yaml and configure
    3. Point your camera/NVR HTTP Post at this bridge's IP and port
    4. Run: python3 viewtron_bridge.py

Requires: pip install viewtron paho-mqtt pyyaml requests

Written by Mike Haldas
mike@cctvcamerapros.net
"""

from datetime import datetime as dt
from viewtron import ViewtronServer
import requests
import json
import os
import re
import sys
import yaml
import threading

# ====================== CONFIG ======================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")
IMG_DIR = os.path.join(BASE_DIR, "images")


def load_config():
    """Load bridge configuration from YAML file."""
    if not os.path.exists(CONFIG_FILE):
        print(f"ERROR: Config file not found: {CONFIG_FILE}")
        print(f"Copy config.yaml.example to config.yaml and configure it.")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)

    return config


# IPC v1.x alarm types → viewtron.py classes


# ====================== MQTT AUTO-DISCOVERY ======================

def slugify(text):
    """Convert text to a slug suitable for MQTT topics and HA unique IDs."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_')


class MQTTBridge:
    """Manages MQTT connection and Home Assistant auto-discovery."""

    def __init__(self, config):
        import paho.mqtt.client as mqtt

        mqtt_config = config.get("mqtt", {})
        self.broker = mqtt_config.get("broker", "localhost")
        self.port = mqtt_config.get("port", 1883)
        self.username = mqtt_config.get("username")
        self.password = mqtt_config.get("password")
        self.discovery_prefix = mqtt_config.get("discovery_prefix", "homeassistant")
        self.topic_prefix = mqtt_config.get("topic_prefix", "viewtron")
        self.expire_after = mqtt_config.get("expire_after", 30)

        self.client = mqtt.Client(
            client_id="viewtron-bridge",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if self.username:
            self.client.username_pw_set(self.username, self.password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.connected = False
        self.discovered_cameras = {}  # camera_id → set of published categories
        self.lock = threading.Lock()

    def connect(self):
        """Connect to the MQTT broker."""
        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            return True
        except Exception as e:
            print(f"  MQTT connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from the MQTT broker."""
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.connected = True
            print(f"  MQTT connected to {self.broker}:{self.port}")
        else:
            print(f"  MQTT connection failed: rc={rc}")

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self.connected = False
        if rc != 0:
            print(f"  MQTT disconnected unexpectedly: rc={rc}")

    def _camera_id(self, camera_name, camera_ip):
        """Generate a stable camera ID from name and IP."""
        name = slugify(camera_name) if camera_name and camera_name != "Unknown Camera" else ""
        ip_slug = camera_ip.replace(".", "_")
        return f"{name}_{ip_slug}" if name else ip_slug

    def _publish_discovery(self, camera_id, camera_name, camera_ip, category):
        """Publish HA MQTT auto-discovery configs for a camera + category."""

        # Avoid "Viewtron Viewtron IPC" — don't double-prefix
        if camera_name and camera_name.lower().startswith("viewtron"):
            display_name = camera_name
        else:
            display_name = f"Viewtron {camera_name}" if camera_name else f"Viewtron {camera_ip}"

        device_info = {
            "identifiers": [f"viewtron_{camera_id}"],
            "name": display_name,
            "manufacturer": "Viewtron",
            "model": "IP Camera",
            "configuration_url": f"http://{camera_ip}",
        }

        base_topic = f"{self.topic_prefix}/{camera_id}"

        if category == "lpr":
            # Sensor: plate number
            self.client.publish(
                f"{self.discovery_prefix}/sensor/{camera_id}/plate/config",
                json.dumps({
                    "name": "License Plate",
                    "unique_id": f"viewtron_{camera_id}_plate",
                    "state_topic": f"{base_topic}/lpr",
                    "value_template": "{{ value_json.plate_number }}",
                    "json_attributes_topic": f"{base_topic}/lpr",
                    "json_attributes_template": "{{ value_json | tojson }}",
                    "icon": "mdi:car",
                    "device": device_info,
                }),
                retain=True,
            )
            # Sensor: plate authorization status
            self.client.publish(
                f"{self.discovery_prefix}/sensor/{camera_id}/plate_authorized/config",
                json.dumps({
                    "name": "Status",
                    "unique_id": f"viewtron_{camera_id}_plate_authorized",
                    "state_topic": f"{base_topic}/lpr",
                    "value_template": "{{ value_json.plate_status }}",
                    "json_attributes_topic": f"{base_topic}/lpr",
                    "json_attributes_template": "{{ value_json | tojson }}",
                    "icon": "mdi:shield-car",
                    "device": device_info,
                }),
                retain=True,
            )
            # Image: overview scene
            self.client.publish(
                f"{self.discovery_prefix}/image/{camera_id}/overview/config",
                json.dumps({
                    "name": "Overview",
                    "unique_id": f"viewtron_{camera_id}_overview",
                    "image_topic": f"{base_topic}/overview_image",
                    "content_type": "image/jpeg",
                    "device": device_info,
                }),
                retain=True,
            )
            # Image: plate crop
            self.client.publish(
                f"{self.discovery_prefix}/image/{camera_id}/target/config",
                json.dumps({
                    "name": "Plate",
                    "unique_id": f"viewtron_{camera_id}_target",
                    "image_topic": f"{base_topic}/target_image",
                    "content_type": "image/jpeg",
                    "device": device_info,
                }),
                retain=True,
            )

        elif category == "intrusion":
            # Binary sensor: person/vehicle detected
            self.client.publish(
                f"{self.discovery_prefix}/binary_sensor/{camera_id}/intrusion/config",
                json.dumps({
                    "name": "Intrusion",
                    "unique_id": f"viewtron_{camera_id}_intrusion",
                    "state_topic": f"{base_topic}/intrusion",
                    "value_template": "ON",
                    "json_attributes_topic": f"{base_topic}/intrusion",
                    "json_attributes_template": "{{ value_json | tojson }}",
                    "device_class": "motion",
                    "expire_after": self.expire_after,
                    "device": device_info,
                }),
                retain=True,
            )

        elif category == "face":
            # Binary sensor: face detected
            self.client.publish(
                f"{self.discovery_prefix}/binary_sensor/{camera_id}/face/config",
                json.dumps({
                    "name": "Face Detected",
                    "unique_id": f"viewtron_{camera_id}_face",
                    "state_topic": f"{base_topic}/face",
                    "value_template": "ON",
                    "json_attributes_topic": f"{base_topic}/face",
                    "json_attributes_template": "{{ value_json | tojson }}",
                    "device_class": "motion",
                    "icon": "mdi:face-recognition",
                    "expire_after": self.expire_after,
                    "device": device_info,
                }),
                retain=True,
            )

        elif category == "counting":
            # Sensor: object count
            self.client.publish(
                f"{self.discovery_prefix}/sensor/{camera_id}/counting/config",
                json.dumps({
                    "name": "Object Count",
                    "unique_id": f"viewtron_{camera_id}_counting",
                    "state_topic": f"{base_topic}/counting",
                    "value_template": "{{ value_json.event_description }}",
                    "json_attributes_topic": f"{base_topic}/counting",
                    "json_attributes_template": "{{ value_json | tojson }}",
                    "icon": "mdi:counter",
                    "device": device_info,
                }),
                retain=True,
            )


    def publish_event(self, payload, category, vt_event=None):
        """Publish an event to MQTT, creating discovery configs if needed.

        Args:
            payload: JSON-serializable dict with event data.
            category: Event category (lpr, intrusion, face, etc.).
            vt_event: Optional event object — if provided and images exist,
                publishes JPEG bytes to image topics for HA camera entities.
        """
        if not self.connected:
            return False

        camera_id = self._camera_id(payload["camera_name"], payload["camera_ip"])

        # Publish discovery config on first event from this camera + category
        with self.lock:
            if camera_id not in self.discovered_cameras:
                self.discovered_cameras[camera_id] = set()
            if category not in self.discovered_cameras[camera_id]:
                self._publish_discovery(
                    camera_id, payload["camera_name"],
                    payload["camera_ip"], category,
                )
                self.discovered_cameras[camera_id].add(category)

        # Publish state (retain LPR so last plate persists in HA)
        topic = f"{self.topic_prefix}/{camera_id}/{category}"
        retain = category == "lpr"
        self.client.publish(topic, json.dumps(payload), retain=retain)

        # Publish images if available (retain LPR images for HA restart)
        if vt_event and vt_event.images_exist():
            base_topic = f"{self.topic_prefix}/{camera_id}"
            retain_img = category == "lpr"
            overview = vt_event.get_source_image_bytes()
            if overview:
                self.client.publish(f"{base_topic}/overview_image", overview, retain=retain_img)
            target = vt_event.get_target_image_bytes()
            if target:
                self.client.publish(f"{base_topic}/target_image", target, retain=retain_img)

        return True


# ====================== SHARED FUNCTIONS ======================

def build_json_payload(vt_event, alarm_type, client_ip):
    """Convert a parsed viewtron.py event object to a JSON-serializable dict."""
    payload = {
        "event_type": alarm_type,
        "event_description": vt_event.get_alarm_description(),
        "camera_name": vt_event.get_ip_cam(),
        "camera_ip": client_ip,
        "timestamp": vt_event.get_time_stamp_formatted(),
    }

    if hasattr(vt_event, "channel_id") and vt_event.channel_id:
        payload["channel_id"] = vt_event.channel_id

    # LPR fields
    if alarm_type in ("VEHICE", "VEHICLE", "vehicle"):
        payload["plate_number"] = vt_event.get_plate_number()

        # Plate status — IPC uses fixed list types, NVR uses user-defined groups
        if hasattr(vt_event, "get_group_name"):
            # NVR v2.0 — show the group name directly
            group = vt_event.get_group_name()
            payload["plate_status"] = group if group else "Unknown"
        elif hasattr(vt_event, "get_vehicle_list_type"):
            # IPC v1.x — map fixed list types to labels
            list_type = vt_event.get_vehicle_list_type()
            if list_type == "whiteList":
                payload["plate_status"] = "Authorized"
            elif list_type == "blackList":
                payload["plate_status"] = "Blacklisted"
            elif list_type == "temporaryList":
                payload["plate_status"] = "Temporary"
            else:
                payload["plate_status"] = "Unknown"
        else:
            payload["plate_status"] = "Unknown"

        if hasattr(vt_event, "get_car_brand"):
            car_brand = vt_event.get_car_brand()
            if car_brand:
                payload["vehicle"] = {
                    "type": vt_event.get_car_type(),
                    "color": vt_event.get_car_color(),
                    "brand": car_brand,
                    "model": vt_event.get_car_model(),
                }
            plate_color = vt_event.get_plate_color()
            if plate_color:
                payload["plate_color"] = plate_color

    # Face fields
    if alarm_type in ("VFD", "videoFaceDetect"):
        if hasattr(vt_event, "get_face_age") and vt_event.get_face_age():
            payload["face"] = {
                "age": vt_event.get_face_age(),
                "sex": vt_event.get_face_sex(),
                "glasses": vt_event.get_face_glasses(),
                "mask": vt_event.get_face_mask(),
            }

    # Intrusion sub-type
    if alarm_type == "AOIENTRY":
        payload["zone_action"] = "entry"
    elif alarm_type == "AOILEAVE":
        payload["zone_action"] = "exit"
    elif alarm_type == "LOITER":
        payload["zone_action"] = "loiter"

    return payload


def save_event_images(vt_event, alarm_type, timestamp_str):
    """Save event images to disk. Returns dict of saved file paths."""
    saved = {}
    os.makedirs(IMG_DIR, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")

    for img_type, get_bytes in [
        ("overview", vt_event.get_source_image_bytes),
        ("target", vt_event.get_target_image_bytes),
    ]:
        img_data = get_bytes()
        if img_data:
            try:
                filename = f"{ts}_{alarm_type}_{img_type}.jpg"
                filepath = os.path.join(IMG_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(img_data)
                saved[f"{img_type}_image"] = filepath
            except Exception as e:
                print(f"  Image save failed ({img_type}): {e}")

    return saved


def forward_to_webhook(ha_url, webhook_id, payload, timeout=5):
    """Send JSON payload to Home Assistant webhook."""
    url = f"{ha_url}/api/webhook/{webhook_id}"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        return resp.status_code
    except requests.RequestException as e:
        print(f"  HA webhook failed: {e}")
        return None


# ====================== EVENT HANDLER ======================

def make_event_handler(config, mqtt_bridge):
    """Create the on_event callback with access to config and MQTT."""

    def on_event(vt_event, client_ip):
        # Skip traject — too high volume for MQTT/webhooks
        if vt_event.category == "traject":
            return

        alarm_type = vt_event.get_alarm_type()
        category = vt_event.category

        # === Build JSON payload ===
        payload = build_json_payload(vt_event, alarm_type, client_ip)

        # === Save images if configured ===
        if config.get("save_images", True) and vt_event.images_exist():
            image_paths = save_event_images(
                vt_event, alarm_type, payload["timestamp"]
            )
            payload.update(image_paths)

        # === Output: MQTT ===
        mqtt_status = ""
        if mqtt_bridge and mqtt_bridge.connected:
            ok = mqtt_bridge.publish_event(payload, category, vt_event)
            mqtt_status = "→ MQTT" if ok else "→ MQTT FAIL"

        # === Output: Webhook ===
        webhook_status = ""
        ha_config = config.get("home_assistant", {})
        webhooks = ha_config.get("webhooks", {})
        if webhooks:
            webhook_id = webhooks.get(category) or webhooks.get("all")
            if webhook_id:
                ha_url = ha_config["url"].rstrip("/")
                code = forward_to_webhook(ha_url, webhook_id, payload)
                webhook_status = f"→ WH {code}" if code else "→ WH FAIL"

        # === Console output ===
        ts = dt.now().strftime("%H:%M:%S")
        desc = payload["event_description"]
        extra = ""
        if "plate_number" in payload:
            plate = payload["plate_number"]
            status = payload.get("plate_status", "Unknown").lower()
            extra = f" | {plate} ({status})"
        elif "face" in payload:
            face = payload["face"]
            extra = f" | {face['age']} {face['sex']}"

        outputs = " ".join(filter(None, [mqtt_status, webhook_status]))
        print(f"[{ts}] {desc}{extra} from {client_ip} {outputs}")

    return on_event


def main():
    config = load_config()
    port = config.get("bridge_port", 5002)

    # === MQTT setup ===
    mqtt_bridge = None
    mqtt_config = config.get("mqtt", {})
    if mqtt_config.get("enabled", False):
        mqtt_bridge = MQTTBridge(config)
        mqtt_bridge.connect()

    # === Print startup info ===
    print(f"\nViewtron → Home Assistant Bridge")
    print(f"{'=' * 50}")

    if mqtt_bridge:
        print(f"MQTT broker:       {mqtt_config['broker']}:{mqtt_config.get('port', 1883)}")
        print(f"MQTT discovery:    {mqtt_config.get('discovery_prefix', 'homeassistant')}/")
        print(f"MQTT topics:       {mqtt_config.get('topic_prefix', 'viewtron')}/")
        print(f"Sensor expiry:     {mqtt_config.get('expire_after', 30)}s")
    else:
        print(f"MQTT:              disabled")

    ha_config = config.get("home_assistant", {})
    webhooks = ha_config.get("webhooks", {})
    if webhooks:
        ha_url = ha_config.get("url", "").rstrip("/")
        print(f"Webhooks:")
        for category, webhook_id in webhooks.items():
            print(f"  {category:12s} → {ha_url}/api/webhook/{webhook_id}")
    else:
        print(f"Webhooks:          disabled")

    print(f"Save images:       {config.get('save_images', True)}")
    print(f"{'=' * 50}")

    # === Start server ===
    def on_connect(client_ip):
        ts = dt.now().strftime("%H:%M:%S")
        print(f"[{ts}] Camera connected: {client_ip}")

    server = ViewtronServer(
        port=port,
        on_event=make_event_handler(config, mqtt_bridge),
        on_connect=on_connect,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if mqtt_bridge:
            mqtt_bridge.disconnect()


if __name__ == "__main__":
    main()
