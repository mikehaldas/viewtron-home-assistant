"""Plate attributes parsed from the sanitized IPC 2.1 fixture.

The fixture is the plate post added with viewtron 1.4.0. It checks that
the bridge publishes the SDK fields, keeps the list label, and uses the
camera's event time.
"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

from viewtron import ViewtronEvent

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewtron-bridge"))
from viewtron_bridge import build_json_payload, format_plate_label  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ipc-v2.1" / "plate-aidrive.xml"

# currentTime in the fixture, microseconds.
EVENT_TIME_US = 1791408287427999


def camera_event_time(microseconds):
    seconds, remainder = divmod(microseconds, 1_000_000)
    return str(datetime.fromtimestamp(seconds).replace(microsecond=remainder))


class IpcPlateFixtureTests(unittest.TestCase):
    def setUp(self):
        self.event = ViewtronEvent(FIXTURE.read_text())
        self.payload = build_json_payload(
            self.event, self.event.get_alarm_type(), "192.0.2.10"
        )

    def test_new_fields_are_published_with_the_existing_plate_status(self):
        self.assertEqual(self.event.category, "lpr")
        self.assertEqual(self.payload["event_type"], "VEHICE")
        self.assertEqual(self.payload["camera_name"], "Viewtron IPC")
        self.assertEqual(self.payload["plate_number"], "AIDRIVE")
        self.assertEqual(self.payload["plate_status"], "blackList")
        self.assertEqual(self.payload["plate_list"], "blackList")
        self.assertEqual(self.payload["direction"], "away")
        self.assertEqual(self.payload["confidence"], 99.0)
        self.assertIsInstance(self.payload["confidence"], float)
        self.assertEqual(self.payload["vehicle_color"], "grey")
        self.assertEqual(self.payload["vehicle_brand"], "Tesla")
        self.assertEqual(self.payload["vehicle_type"], "saloon car")
        self.assertEqual(self.payload["vehicle_model"], "Tesla_ModelS")
        # IPC plate events do not use the NVR vehicle object.
        self.assertNotIn("vehicle", self.payload)
        self.assertNotIn("plate_color", self.payload)
        encoded = json.dumps(self.payload)
        self.assertIn('"confidence": 99.0', encoded)
        self.assertIn('"direction": "away"', encoded)

    def test_timestamp_is_the_camera_event_time(self):
        expected = camera_event_time(EVENT_TIME_US)
        self.assertEqual(self.event.get_time_stamp_formatted(), expected)
        self.assertEqual(self.payload["timestamp"], expected)
        self.assertNotIn("1970", self.payload["timestamp"])

    def test_console_list_label(self):
        self.assertEqual(format_plate_label(self.payload), "AIDRIVE (blacklist)")


if __name__ == "__main__":
    unittest.main()
