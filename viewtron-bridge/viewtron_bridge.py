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
    4. Run: python3 viewtron-bridge/viewtron_bridge.py
       (from the folder that holds your config.yaml), or
       python3 viewtron_bridge.py --config /path/to/config.yaml

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
import time
import uuid
import yaml
import argparse
import threading
from contextlib import nullcontext

# ====================== CONFIG ======================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_ENV = "VIEWTRON_BRIDGE_CONFIG"
IMG_DIR = os.path.join(BASE_DIR, "images")  # replaced in main() with <config dir>/images

# Seconds to wait after publishing a discovery config before the first state
# message on that topic. Home Assistant only subscribes to a new entity's
# state topic after it has processed the discovery config, so a state
# message published right away is dropped.
DISCOVERY_SETTLE_SECONDS = 2.0

# IPC v1.x numeric target types. NVR v2.0 events already use names.
TARGET_TYPES_V1 = {"1": "person", "2": "car", "4": "motor"}


def is_addon_manifest(data):
    """True if a parsed YAML file is the Home Assistant add-on manifest
    (viewtron-bridge/config.yaml in the repo), not a bridge config."""
    return isinstance(data, dict) and "slug" in data and "schema" in data


def config_candidates(cli_path=None):
    """Config file locations, in the order they are tried."""
    if cli_path:
        return [cli_path]
    if os.environ.get(CONFIG_ENV):
        return [os.environ[CONFIG_ENV]]
    candidates = [
        os.path.join(os.getcwd(), "config.yaml"),             # current folder
        os.path.join(os.path.dirname(BASE_DIR), "config.yaml"),  # repo root
        os.path.join(BASE_DIR, "config.yaml"),                # next to this script
    ]
    seen = []
    for c in candidates:
        c = os.path.abspath(c)
        if c not in seen:
            seen.append(c)
    return seen


def load_config(cli_path=None):
    """Find and load the bridge configuration.

    Order: --config argument, then the VIEWTRON_BRIDGE_CONFIG environment
    variable, then config.yaml in the current folder, the repo root, and next
    to this script. The add-on manifest (viewtron-bridge/config.yaml) is
    skipped, because it is not a bridge config.

    Returns:
        (config dict, path of the file that was loaded)
    """
    explicit = bool(cli_path or os.environ.get(CONFIG_ENV))
    skipped_manifest = None

    for path in config_candidates(cli_path):
        if not os.path.isfile(path):
            if explicit:
                print(f"ERROR: Config file not found: {path}")
                sys.exit(1)
            continue
        with open(path) as f:
            config = yaml.safe_load(f) or {}
        if is_addon_manifest(config):
            if explicit:
                print(f"ERROR: {path} is the Home Assistant add-on manifest, "
                      f"not a bridge config.")
                print("Copy config.yaml.example to config.yaml and point --config at it.")
                sys.exit(1)
            skipped_manifest = path
            continue
        if not isinstance(config, dict):
            print(f"ERROR: {path} is not a valid bridge config (expected a YAML mapping).")
            sys.exit(1)
        return config, path

    print("ERROR: No bridge config file found. Looked for:")
    for path in config_candidates(cli_path):
        note = " (add-on manifest, skipped)" if path == skipped_manifest else ""
        print(f"  {path}{note}")
    print("Copy config.yaml.example to config.yaml in the repo folder and edit it,")
    print("or run with --config /path/to/config.yaml.")
    sys.exit(1)


def get_target_type(vt_event):
    """Return what the camera detected ("person", "car", "motor", ...) or None.

    Reads the first target in the event: listInfo (IPC v1.x),
    vsd/targetImageData (IPC v1.x video metadata) or targetListInfo
    (NVR v2.0).
    """
    data = getattr(vt_event, "json", None)
    if not isinstance(data, dict):
        return None
    config = data.get("config", {})
    if not isinstance(config, dict):
        return None

    def first_item(block):
        if not isinstance(block, dict):
            return {}
        item = block.get("item", {})
        if isinstance(item, list):
            item = item[0] if item else {}
        return item if isinstance(item, dict) else {}

    def text(value):
        if isinstance(value, dict):
            value = value.get("#text", "")
        return str(value).strip() if value is not None else ""

    raw = ""
    item = first_item(config.get("targetListInfo"))       # NVR v2.0
    if item:
        raw = text(item.get("targetType"))
    if not raw:
        item = first_item(config.get("listInfo"))         # IPC v1.x
        target = item.get("targetImageData", {}) if item else {}
        if isinstance(target, dict):
            raw = text(target.get("targetType"))
    if not raw:
        vsd = config.get("vsd", {})                       # IPC v1.x VSD
        target = vsd.get("targetImageData", {}) if isinstance(vsd, dict) else {}
        if isinstance(target, dict):
            raw = text(target.get("targetType"))
    if not raw:
        return None
    return TARGET_TYPES_V1.get(raw, raw)


# ====================== MQTT AUTO-DISCOVERY ======================

def slugify(text):
    """Convert text to a slug suitable for MQTT topics and HA unique IDs."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_')


class MQTTBridge:
    """Manages MQTT connection and Home Assistant auto-discovery."""

    # Image entities for event types other than LPR. LPR keeps its original
    # "Overview" and "Plate" entities and topics.
    IMAGE_LABELS = {
        "intrusion": "Intrusion",
        "face": "Face",
        "counting": "Counting",
        "metadata": "Object",
    }

    def __init__(self, config):
        import paho.mqtt.client as mqtt

        mqtt_config = config.get("mqtt") or {}
        self.broker = mqtt_config.get("broker", "localhost")
        self.port = mqtt_config.get("port", 1883)
        self.username = mqtt_config.get("username")
        self.password = mqtt_config.get("password")
        self.discovery_prefix = mqtt_config.get("discovery_prefix", "homeassistant")
        self.topic_prefix = mqtt_config.get("topic_prefix", "viewtron")
        # Seconds after the last event before binary sensors turn back off.
        # Older configs called this expire_after.
        self.off_delay = mqtt_config.get(
            "off_delay", mqtt_config.get("expire_after", 30)
        )

        # Unique client ID, so a second bridge on the same broker doesn't
        # keep disconnecting this one
        self.client = mqtt.Client(
            client_id=f"viewtron-bridge-{uuid.uuid4().hex[:8]}",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if self.username:
            self.client.username_pw_set(self.username, self.password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.connected = False
        self.discovered_cameras = {}  # camera_id → set of published discovery keys
        self.ready_at = {}            # (camera_id, key) → monotonic time state may be sent
        self.lock = threading.Lock()

    def connect(self):
        """Connect to the MQTT broker in the background.

        The network loop keeps retrying until the broker answers, so the
        bridge still connects if it starts before the broker (for example
        at boot), and reconnects if the broker restarts.
        """
        try:
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)
            self.client.connect_async(self.broker, self.port, keepalive=60)
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
            # Publish discovery again after a (re)connect, in case the broker
            # lost its retained messages.
            with self.lock:
                self.discovered_cameras.clear()
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

    def _device_info(self, camera_id, camera_name, camera_ip):
        # Avoid "Viewtron Viewtron IPC" — don't double-prefix
        if camera_name and camera_name.lower().startswith("viewtron"):
            display_name = camera_name
        else:
            display_name = f"Viewtron {camera_name}" if camera_name else f"Viewtron {camera_ip}"

        return {
            "identifiers": [f"viewtron_{camera_id}"],
            "name": display_name,
            "manufacturer": "Viewtron",
            "model": "IP Camera",
            "configuration_url": f"http://{camera_ip}",
        }

    def _image_topics(self, camera_id, category):
        """Overview and target image topics for an event category.

        LPR keeps the original overview_image/target_image topics. Other
        categories get their own topics, so an intrusion or counting crop
        never replaces the plate crop.
        """
        base_topic = f"{self.topic_prefix}/{camera_id}"
        if category == "lpr":
            return f"{base_topic}/overview_image", f"{base_topic}/target_image"
        return (f"{base_topic}/{category}_overview_image",
                f"{base_topic}/{category}_target_image")

    def _binary_sensor_config(self, name, unique_id, state_topic, device_info, icon=None):
        config = {
            "name": name,
            "unique_id": unique_id,
            "state_topic": state_topic,
            "value_template": "ON",
            "json_attributes_topic": state_topic,
            "json_attributes_template": "{{ value_json | tojson }}",
            "device_class": "motion",
            # Turn off after the delay. Without this the sensor stays on,
            # and expire_after would make it unavailable instead of off.
            "off_delay": self.off_delay,
            "device": device_info,
        }
        if icon:
            config["icon"] = icon
        return config

    def _publish_discovery(self, camera_id, camera_name, camera_ip, category):
        """Publish HA MQTT auto-discovery configs for a camera + category."""
        device_info = self._device_info(camera_id, camera_name, camera_ip)
        base_topic = f"{self.topic_prefix}/{camera_id}"

        if category == "lpr":
            overview_topic, target_topic = self._image_topics(camera_id, "lpr")
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
            # Sensor: plate group (the discovery ID keeps its original name so
            # existing entities are not duplicated)
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
                    "image_topic": overview_topic,
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
                    "image_topic": target_topic,
                    "content_type": "image/jpeg",
                    "device": device_info,
                }),
                retain=True,
            )

        elif category == "intrusion":
            # Binary sensor: person/vehicle detected in a zone or crossing a line
            self.client.publish(
                f"{self.discovery_prefix}/binary_sensor/{camera_id}/intrusion/config",
                json.dumps(self._binary_sensor_config(
                    "Intrusion", f"viewtron_{camera_id}_intrusion",
                    f"{base_topic}/intrusion", device_info,
                )),
                retain=True,
            )

        elif category == "face":
            # Binary sensor: face detected
            self.client.publish(
                f"{self.discovery_prefix}/binary_sensor/{camera_id}/face/config",
                json.dumps(self._binary_sensor_config(
                    "Face Detected", f"viewtron_{camera_id}_face",
                    f"{base_topic}/face", device_info, icon="mdi:face-recognition",
                )),
                retain=True,
            )

        elif category == "counting":
            # Sensor: number of objects counted since the bridge started.
            # total_increasing lets HA statistics and utility meters handle
            # the reset when the bridge restarts.
            self.client.publish(
                f"{self.discovery_prefix}/sensor/{camera_id}/counting/config",
                json.dumps({
                    "name": "Object Count",
                    "unique_id": f"viewtron_{camera_id}_counting",
                    "state_topic": f"{base_topic}/counting",
                    "value_template": "{{ value_json.count }}",
                    "json_attributes_topic": f"{base_topic}/counting",
                    "json_attributes_template": "{{ value_json | tojson }}",
                    "state_class": "total_increasing",
                    "unit_of_measurement": "objects",
                    "icon": "mdi:counter",
                    "device": device_info,
                }),
                retain=True,
            )

        elif category == "metadata":
            # Binary sensor: object (person, car, ...) seen anywhere in the frame
            self.client.publish(
                f"{self.discovery_prefix}/binary_sensor/{camera_id}/metadata/config",
                json.dumps(self._binary_sensor_config(
                    "Object Detected", f"viewtron_{camera_id}_metadata",
                    f"{base_topic}/metadata", device_info, icon="mdi:motion-sensor",
                )),
                retain=True,
            )

    def _publish_image_discovery(self, camera_id, camera_name, camera_ip, category):
        """Publish overview/target image entities for a non-LPR category."""
        label = self.IMAGE_LABELS.get(category)
        if not label:
            return False
        device_info = self._device_info(camera_id, camera_name, camera_ip)
        overview_topic, target_topic = self._image_topics(camera_id, category)
        for kind, name, topic in (
            ("overview", f"{label} Overview", overview_topic),
            ("target", f"{label} Target", target_topic),
        ):
            self.client.publish(
                f"{self.discovery_prefix}/image/{camera_id}/{category}_{kind}/config",
                json.dumps({
                    "name": name,
                    "unique_id": f"viewtron_{camera_id}_{category}_{kind}",
                    "image_topic": topic,
                    "content_type": "image/jpeg",
                    "device": device_info,
                }),
                retain=True,
            )
        return True

    def _ensure_discovery(self, camera_id, payload, key, publish):
        """Publish a discovery config once per camera + key.

        Returns the monotonic time after which state for this key may be
        published (so Home Assistant has subscribed by then).
        """
        with self.lock:
            published = self.discovered_cameras.setdefault(camera_id, set())
            if key not in published:
                publish(camera_id, payload["camera_name"], payload["camera_ip"])
                published.add(key)
                self.ready_at[(camera_id, key)] = time.monotonic() + DISCOVERY_SETTLE_SECONDS
            return self.ready_at.get((camera_id, key), 0)

    @staticmethod
    def _wait_until(ready_at):
        delay = ready_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def publish_event(self, payload, category, vt_event=None):
        """Publish an event to MQTT, creating discovery configs if needed.

        Args:
            payload: JSON-serializable dict with event data.
            category: Event category (lpr, intrusion, face, counting, metadata).
            vt_event: Optional event object — if provided and images exist,
                publishes JPEG bytes to image topics for HA image entities.
        """
        if not self.connected:
            return False

        camera_id = self._camera_id(payload["camera_name"], payload["camera_ip"])

        # Discovery on first event from this camera + category
        ready_at = self._ensure_discovery(
            camera_id, payload, category,
            lambda cid, name, ip: self._publish_discovery(cid, name, ip, category),
        )

        overview = target = None
        if vt_event and vt_event.images_exist():
            overview = vt_event.get_source_image_bytes()
            target = vt_event.get_target_image_bytes()
            if (overview or target) and category != "lpr" and category in self.IMAGE_LABELS:
                ready_at = max(ready_at, self._ensure_discovery(
                    camera_id, payload, f"{category}_images",
                    lambda cid, name, ip: self._publish_image_discovery(cid, name, ip, category),
                ))

        # Give Home Assistant time to subscribe to newly discovered entities
        self._wait_until(ready_at)

        # Publish state (retain LPR so last plate persists in HA)
        topic = f"{self.topic_prefix}/{camera_id}/{category}"
        retain = category == "lpr"
        self.client.publish(topic, json.dumps(payload), retain=retain)

        # Publish images (retain LPR images for HA restart)
        if overview or target:
            overview_topic, target_topic = self._image_topics(camera_id, category)
            if overview:
                self.client.publish(overview_topic, overview, retain=retain)
            if target:
                self.client.publish(target_topic, target, retain=retain)

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

    target_type = get_target_type(vt_event)
    if target_type:
        payload["target_type"] = target_type

    # LPR fields
    if alarm_type in ("VEHICE", "VEHICLE", "vehicle"):
        payload["plate_number"] = vt_event.get_plate_number()

        # Plate group — raw value from camera/NVR, application decides meaning
        plate_group = vt_event.get_plate_group()
        payload["plate_status"] = plate_group if plate_group else "Unknown"

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

class EventCounter:
    """Counts object counting events per camera since the bridge started.

    Counting events from the camera carry one counted object each, not a
    running total, so the bridge keeps the total.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.counts = {}       # (camera name, camera IP) → {"total": n, "by_type": {...}}
        self.camera_locks = {}  # (camera name, camera IP) → Lock

    @staticmethod
    def _key(payload):
        return (payload.get("camera_name"), payload.get("camera_ip"))

    def camera_lock(self, payload):
        """Lock held while a counting event is counted and published, so
        counts reach MQTT in order (a lower count after a higher one would
        look like a reset to Home Assistant)."""
        with self.lock:
            return self.camera_locks.setdefault(self._key(payload), threading.Lock())

    def add(self, payload):
        key = self._key(payload)
        with self.lock:
            entry = self.counts.setdefault(key, {"total": 0, "by_type": {}})
            entry["total"] += 1
            target = payload.get("target_type")
            if target:
                entry["by_type"][target] = entry["by_type"].get(target, 0) + 1
            return entry["total"], dict(entry["by_type"])


def make_event_handler(config, mqtt_bridge):
    """Create the on_event callback with access to config and MQTT."""
    counter = EventCounter()

    def on_event(vt_event, client_ip):
        # Skip traject — too high volume for MQTT/webhooks
        if vt_event.category == "traject":
            return

        alarm_type = vt_event.get_alarm_type()
        category = vt_event.category

        # === Build JSON payload ===
        payload = build_json_payload(vt_event, alarm_type, client_ip)
        ordered = counter.camera_lock(payload) if category == "counting" else nullcontext()
        with ordered:
            if category == "counting":
                payload["count"], payload["count_by_type"] = counter.add(payload)
            deliver(vt_event, payload, category, client_ip)

    def deliver(vt_event, payload, category, client_ip):
        alarm_type = payload["event_type"]

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
        ha_config = config.get("home_assistant") or {}
        webhooks = ha_config.get("webhooks") or {}
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
        elif "count" in payload:
            extra = f" | count {payload['count']}"
        if "target_type" in payload and "plate_number" not in payload:
            extra += f" | {payload['target_type']}"

        outputs = " ".join(filter(None, [mqtt_status, webhook_status]))
        print(f"[{ts}] {desc}{extra} from {client_ip} {outputs}")

    return on_event


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Viewtron camera events to Home Assistant (MQTT and webhooks)")
    parser.add_argument(
        "-c", "--config",
        help=f"path to the bridge config.yaml (or set {CONFIG_ENV}). Default: "
             "config.yaml in the current folder, the repo root, or next to this script")
    return parser.parse_args(argv)


def main(argv=None):
    global IMG_DIR
    args = parse_args(argv)
    config, config_path = load_config(args.config)
    IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(config_path)), "images")
    port = config.get("bridge_port") or 5002

    # === MQTT setup ===
    mqtt_bridge = None
    mqtt_config = config.get("mqtt") or {}
    if mqtt_config.get("enabled", False):
        mqtt_bridge = MQTTBridge(config)
        mqtt_bridge.connect()

    # === Print startup info ===
    print(f"\nViewtron → Home Assistant Bridge")
    print(f"{'=' * 50}")
    print(f"Config file:       {config_path}")

    if mqtt_bridge:
        print(f"MQTT broker:       {mqtt_bridge.broker}:{mqtt_bridge.port}")
        print(f"MQTT discovery:    {mqtt_config.get('discovery_prefix', 'homeassistant')}/")
        print(f"MQTT topics:       {mqtt_config.get('topic_prefix', 'viewtron')}/")
        print(f"Sensor off delay:  {mqtt_bridge.off_delay}s")
    else:
        print(f"MQTT:              disabled")

    ha_config = config.get("home_assistant") or {}
    webhooks = ha_config.get("webhooks") or {}
    if webhooks:
        ha_url = ha_config.get("url", "").rstrip("/")
        print(f"Webhooks:")
        for category, webhook_id in webhooks.items():
            print(f"  {category:12s} → {ha_url}/api/webhook/{webhook_id}")
    else:
        print(f"Webhooks:          disabled")

    print(f"Save images:       {config.get('save_images', True)}")
    print(f"{'=' * 50}")
    if not mqtt_bridge and not webhooks:
        print("WARNING: MQTT and webhooks are both disabled, so events will not reach")
        print(f"Home Assistant. Set mqtt.enabled: true in {config_path}.")

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
