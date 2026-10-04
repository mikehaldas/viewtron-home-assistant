#!/usr/bin/env bash
# Post a sample license plate event to a running bridge container and check
# that the MQTT discovery config and the plate state are published.
# Usage: smoke-test.sh <bridge port> <name> <camera source IP, 127.0.0.x>
set -euo pipefail
PORT="$1"
NAME="$2"
SRC_IP="$3"
CAMERA_ID="${SRC_IP//./_}"
SAMPLE_URL="https://raw.githubusercontent.com/mikehaldas/IP-Camera-API/main/examples/ipc-v1x/lpr.xml"

for _ in $(seq 1 30); do
  (echo > "/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null && break
  sleep 1
done

curl -sfL -o /tmp/lpr.xml "$SAMPLE_URL"

# -R skips retained messages, so only a fresh publish counts
docker exec mqtt mosquitto_sub -R -t "viewtron/${CAMERA_ID}/lpr" -C 1 -W 30 \
  > "/tmp/${NAME}-state.json" &
SUB=$!
sleep 1
curl -sf -m 10 --interface "$SRC_IP" -X POST -H 'Content-Type: application/xml' \
  --data-binary @/tmp/lpr.xml "http://127.0.0.1:${PORT}/API" > /dev/null
wait "$SUB"

echo "State: $(cat "/tmp/${NAME}-state.json")"
grep -q '"plate_number": "ABC1234"' "/tmp/${NAME}-state.json"

docker exec mqtt mosquitto_sub -t "homeassistant/sensor/${CAMERA_ID}/plate/config" -C 1 -W 5 \
  | grep -q "\"unique_id\": \"viewtron_${CAMERA_ID}_plate\""
echo "${NAME}: OK"
