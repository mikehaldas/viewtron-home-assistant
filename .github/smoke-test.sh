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

# Sanitized IPC plate post from the viewtron 1.4.0 fixtures. The device name
# is "Viewtron IPC", so the topic includes that name and the source address.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE_IP="127.0.0.4"
FIXTURE_ID="viewtron_ipc_${FIXTURE_IP//./_}"
FIXTURE_STATE="/tmp/${NAME}-aidrive.json"
docker exec mqtt mosquitto_sub -R -t "viewtron/${FIXTURE_ID}/lpr" -C 1 -W 30 \
  > "$FIXTURE_STATE" &
SUB=$!
sleep 1
curl -sf -m 10 --interface "$FIXTURE_IP" -X POST -H 'Content-Type: application/xml' \
  --data-binary @"${ROOT}/tests/fixtures/ipc-v2.1/plate-aidrive.xml" \
  "http://127.0.0.1:${PORT}/API" > /dev/null
wait "$SUB"
python3 - "$FIXTURE_STATE" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
expected = {
    "plate_number": "AIDRIVE",
    "plate_status": "blackList",
    "plate_list": "blackList",
    "direction": "away",
    "confidence": 99.0,
    "vehicle_color": "grey",
    "vehicle_brand": "Tesla",
    "vehicle_type": "saloon car",
    "vehicle_model": "Tesla_ModelS",
}
for key, value in expected.items():
    if payload.get(key) != value:
        raise SystemExit(f"{key}: {payload.get(key)!r} != {value!r}\n{payload}")
if "1970" in payload.get("timestamp", ""):
    raise SystemExit(f"timestamp fell back to epoch: {payload['timestamp']}")
print(f"fixture state ok {payload['timestamp']}")
PY
echo "${NAME}: OK"
