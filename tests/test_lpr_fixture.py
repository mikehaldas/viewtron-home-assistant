"""Plate attributes parsed from sanitized IPC plate posts.

The fixtures are V1 plate posts. They check that the bridge publishes the
SDK fields, keeps the list label, and uses the camera's event time.
whiteList, blackList, and a post with no vehicleListType are all covered.
"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

from viewtron import ViewtronEvent

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewtron-bridge"))
from viewtron_bridge import build_json_payload, format_plate_label  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ipc-v2.1"
WHITELIST = FIXTURE_DIR / "plate-ib36nl.xml"
BLACKLIST = FIXTURE_DIR / "plate-blacklist-away.xml"
UNLISTED = FIXTURE_DIR / "plate-unlisted-approach.xml"

# currentTime in the fixtures, microseconds.
EVENT_TIME_US = 1791471201542438


def camera_event_time(microseconds):
    seconds, remainder = divmod(microseconds, 1_000_000)
    return str(datetime.fromtimestamp(seconds).replace(microsecond=remainder))


def load_fixture(path):
    event = ViewtronEvent(path.read_text())
    payload = build_json_payload(event, event.get_alarm_type(), "192.0.2.10")
    return event, payload


class IpcPlateFixtureTests(unittest.TestCase):
    def assert_published_plate(self, path, plate, status, plate_list, direction, label):
        event, payload = load_fixture(path)
        self.assertEqual(event.category, "lpr")
        self.assertEqual(payload["event_type"], "VEHICE")
        self.assertEqual(payload["camera_name"], "LPR-TEST")
        self.assertEqual(payload["plate_number"], plate)
        self.assertEqual(payload["plate_status"], status)
        self.assertEqual(payload["plate_list"], plate_list)
        self.assertEqual(payload["direction"], direction)
        self.assertEqual(payload["confidence"], 99.0)
        self.assertIsInstance(payload["confidence"], float)
        self.assertEqual(payload["vehicle_color"], "white")
        self.assertEqual(payload["vehicle_brand"], "TestBrand")
        self.assertEqual(payload["vehicle_type"], "saloon car")
        self.assertEqual(payload["vehicle_model"], "TestModel")
        # IPC plate events do not use the NVR vehicle object.
        self.assertNotIn("vehicle", payload)
        self.assertNotIn("plate_color", payload)
        encoded = json.dumps(payload)
        self.assertIn('"confidence": 99.0', encoded)
        self.assertIn(f'"direction": "{direction}"', encoded)
        if plate_list is None:
            self.assertIn('"plate_list": null', encoded)
        expected = camera_event_time(EVENT_TIME_US)
        self.assertEqual(event.get_time_stamp_formatted(), expected)
        self.assertEqual(payload["timestamp"], expected)
        self.assertNotIn("1970", payload["timestamp"])
        self.assertEqual(format_plate_label(payload), label)
        for image in (event.get_source_image_bytes(), event.get_target_image_bytes()):
            self.assertTrue(image.startswith(b"\xff\xd8\xff"))
            self.assertTrue(image.endswith(b"\xff\xd9"))
        return event, payload

    def test_whitelist_approach_publishes_plate_fields(self):
        text = WHITELIST.read_text()
        self.assertIn("whiteList", text)
        self.assertIn(">approach<", text)
        self.assert_published_plate(
            WHITELIST,
            plate="IB36NL",
            status="whiteList",
            plate_list="whiteList",
            direction="approach",
            label="IB36NL (whitelist)",
        )

    def test_blacklist_away_publishes_plate_fields(self):
        text = BLACKLIST.read_text()
        self.assertIn("blackList", text)
        self.assertIn(">away<", text)
        self.assert_published_plate(
            BLACKLIST,
            plate="TEST456",
            status="blackList",
            plate_list="blackList",
            direction="away",
            label="TEST456 (blacklist)",
        )

    def test_unlisted_approach_has_no_vehicle_list(self):
        text = UNLISTED.read_text()
        self.assertNotIn("vehicleListType", text)
        self.assertIn(">approach<", text)
        self.assert_published_plate(
            UNLISTED,
            plate="TEST123",
            status="Unknown",
            plate_list=None,
            direction="approach",
            label="TEST123 (unknown)",
        )


if __name__ == "__main__":
    unittest.main()
