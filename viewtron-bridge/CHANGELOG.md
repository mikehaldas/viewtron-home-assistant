# Changelog

## 1.1.1

- License plate events from an IP camera reach Home Assistant when the
  installed viewtron package has no `get_plate_group()`. viewtron 1.3.0
  exposes `get_vehicle_list_type()` instead (`whiteList`, `blackList`,
  `temporaryList`, or none). The bridge uses `get_plate_group()` when it
  exists and `get_vehicle_list_type()` otherwise, so the plate is published
  instead of raising `AttributeError`.
- The viewtron requirement stays at 1.3.1 or newer. 1.3.1 is the latest
  release on PyPI, and its NVR license plate events include
  `get_plate_group()` (the NVR's plate group name).
- Tested against a Viewtron LPR camera. The plate, status, and both images
  reached Home Assistant.

## 1.1.0

- The first face, intrusion or counting event from a new camera now shows up
  in Home Assistant. The bridge waits briefly after creating an entity so
  Home Assistant is listening before the event is sent.
- Intrusion and Face Detected binary sensors now turn off 30 seconds after
  the last event (`off_delay`) instead of becoming unavailable.
- Intrusion, face, counting and video metadata pictures get their own image
  entities and no longer replace the license plate crop.
- Object Count is now a number (objects counted since the bridge started,
  with a per-type breakdown) instead of the event description.
- Video metadata events (full-frame person/vehicle detection) now create an
  Object Detected binary sensor and image entities.
- Events include `target_type` (person, car, motor) when the camera sends it.
- The add-on image no longer depends on the base image passed in by the
  Supervisor. Older Supervisor versions supplied a base without Python, so
  the build failed.
- Add-on options are written to the bridge config with proper quoting, so
  MQTT passwords with quotes or other special characters work.
- The bridge keeps retrying the MQTT connection if the broker isn't up yet
  when it starts.

## 1.0.1

- Require viewtron SDK 1.3.1 or newer. License plate events now reach Home
  Assistant again: the bridge calls `get_plate_group()`, which 1.3.0 didn't
  have, so plate events failed before they were published.
- SDK 1.3.1 also fixes a crash on events with an empty image, and keeps the
  camera connection open when an event can't be parsed.
- Fix the SDK version pin in the Dockerfile. It was unquoted, so the shell
  treated `>=1.3.0` as an output redirect and the pin was ignored.
