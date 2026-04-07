#!/bin/sh

# Viewtron Bridge — start script
# Works in two modes:
#   HAOS Add-on:  reads config from /data/options.json (set by HA Supervisor)
#   Standalone:   reads config from environment variables

# Check if running as HA add-on (bashio available)
if command -v bashio > /dev/null 2>&1; then
    # === HAOS ADD-ON MODE ===
    BRIDGE_PORT=$(bashio::config 'bridge_port')
    MQTT_BROKER=$(bashio::config 'mqtt_broker')
    MQTT_PORT=$(bashio::config 'mqtt_port')
    MQTT_USERNAME=$(bashio::config 'mqtt_username')
    MQTT_PASSWORD=$(bashio::config 'mqtt_password')
    SAVE_IMAGES=$(bashio::config 'save_images')
    bashio::log.info "Starting Viewtron Bridge (HA Add-on mode)"
elif [ -f /data/options.json ]; then
    # === HA ADD-ON without bashio — parse JSON directly ===
    BRIDGE_PORT=$(python3 -c "import json; print(json.load(open('/data/options.json')).get('bridge_port', 5002))")
    MQTT_BROKER=$(python3 -c "import json; print(json.load(open('/data/options.json')).get('mqtt_broker', 'localhost'))")
    MQTT_PORT=$(python3 -c "import json; print(json.load(open('/data/options.json')).get('mqtt_port', 1883))")
    MQTT_USERNAME=$(python3 -c "import json; print(json.load(open('/data/options.json')).get('mqtt_username', ''))")
    MQTT_PASSWORD=$(python3 -c "import json; print(json.load(open('/data/options.json')).get('mqtt_password', ''))")
    SAVE_IMAGES=$(python3 -c "import json; print(str(json.load(open('/data/options.json')).get('save_images', False)).lower())")
    echo "Starting Viewtron Bridge (Add-on mode, options.json)"
else
    # === STANDALONE DOCKER MODE ===
    BRIDGE_PORT=${BRIDGE_PORT:-5002}
    MQTT_BROKER=${MQTT_BROKER:-localhost}
    MQTT_PORT=${MQTT_PORT:-1883}
    MQTT_USERNAME=${MQTT_USERNAME:-}
    MQTT_PASSWORD=${MQTT_PASSWORD:-}
    SAVE_IMAGES=${SAVE_IMAGES:-false}
    echo "Starting Viewtron Bridge (standalone Docker mode)"
fi

# Generate config.yaml
cat > /config.yaml << EOF
bridge_port: ${BRIDGE_PORT}
save_images: ${SAVE_IMAGES}
mqtt:
  enabled: true
  broker: ${MQTT_BROKER}
  port: ${MQTT_PORT}
  username: "${MQTT_USERNAME}"
  password: "${MQTT_PASSWORD}"
  discovery_prefix: homeassistant
  topic_prefix: viewtron
home_assistant:
  url: http://supervisor/core
  webhooks: {}
EOF

echo "Bridge port: ${BRIDGE_PORT}"
echo "MQTT broker: ${MQTT_BROKER}:${MQTT_PORT}"

cd / && exec python3 viewtron_bridge.py
