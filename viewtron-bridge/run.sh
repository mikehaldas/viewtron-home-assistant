#!/usr/bin/env bashio

# Read options from HA add-on config
BRIDGE_PORT=$(bashio::config 'bridge_port')
MQTT_BROKER=$(bashio::config 'mqtt_broker')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USERNAME=$(bashio::config 'mqtt_username')
MQTT_PASSWORD=$(bashio::config 'mqtt_password')
SAVE_IMAGES=$(bashio::config 'save_images')

# Generate config.yaml from add-on options
cat > /config.yaml << EOF
bridge_port: ${BRIDGE_PORT}
save_images: ${SAVE_IMAGES}
mqtt:
  enabled: true
  broker: ${MQTT_BROKER}
  port: ${MQTT_PORT}
  username: ${MQTT_USERNAME}
  password: ${MQTT_PASSWORD}
  discovery_prefix: homeassistant
  topic_prefix: viewtron
home_assistant:
  url: http://supervisor/core
  webhooks: {}
EOF

bashio::log.info "Starting Viewtron Bridge on port ${BRIDGE_PORT}"
bashio::log.info "MQTT broker: ${MQTT_BROKER}:${MQTT_PORT}"

cd / && exec python3 viewtron_bridge.py
