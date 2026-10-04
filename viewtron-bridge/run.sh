#!/bin/sh

# Viewtron Bridge — start script
# Works in two modes:
#   HAOS Add-on:  reads options from /data/options.json (written by the HA Supervisor)
#   Standalone:   reads options from environment variables (docker run -e ...)
#
# Either way it writes /config.yaml and starts the bridge with it.

CONFIG_OUT=/config.yaml

if [ -f /data/options.json ]; then
    echo "Starting Viewtron Bridge (Home Assistant add-on mode)"
    MODE=addon
else
    echo "Starting Viewtron Bridge (standalone Docker mode)"
    MODE=standalone
fi

# Build the config in Python so passwords and other values are quoted
# safely. JSON is valid YAML, so the bridge reads it as its config.yaml.
MODE="$MODE" CONFIG_OUT="$CONFIG_OUT" python3 - << 'PYEOF' || exit 1
import json
import os

if os.environ["MODE"] == "addon":
    with open("/data/options.json") as f:
        opts = json.load(f)
    get = lambda key, default: opts.get(key, default)
else:
    env_names = {
        "bridge_port": "BRIDGE_PORT", "mqtt_broker": "MQTT_BROKER",
        "mqtt_port": "MQTT_PORT", "mqtt_username": "MQTT_USERNAME",
        "mqtt_password": "MQTT_PASSWORD", "save_images": "SAVE_IMAGES",
    }
    get = lambda key, default: os.environ.get(env_names[key]) or default

def as_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")

config = {
    "bridge_port": int(get("bridge_port", 5002)),
    "save_images": as_bool(get("save_images", False)),
    "mqtt": {
        "enabled": True,
        "broker": str(get("mqtt_broker", "localhost")),
        "port": int(get("mqtt_port", 1883)),
        "username": str(get("mqtt_username", "")),
        "password": str(get("mqtt_password", "")),
        "discovery_prefix": "homeassistant",
        "topic_prefix": "viewtron",
    },
    "home_assistant": {"url": "http://supervisor/core", "webhooks": {}},
}

with open(os.environ["CONFIG_OUT"], "w") as f:
    json.dump(config, f, indent=2)

print(f"Bridge port: {config['bridge_port']}")
print(f"MQTT broker: {config['mqtt']['broker']}:{config['mqtt']['port']}")
PYEOF

cd / && exec python3 /viewtron_bridge.py --config "$CONFIG_OUT"
