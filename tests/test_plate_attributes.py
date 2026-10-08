"""Plate attributes from the viewtron 1.4.0 IPC fixture."""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

from viewtron import ViewtronEvent

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewtron-bridge"))
from viewtron_bridge import build_json_payload, plate_log_suffix  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ipc-v2.1" / "plate-aidrive.xml"
CLIENT_IP = "192.0.2.10"


def payload_for(xml):
    event = ViewtronEvent(xml)
    payload = build_json_payload(event, event.get_alarm_type(), CLIENT_IP)
    return event, payload


class PlateFixtureTests(unittest.TestCase):
    def test_aidrive_attributes_and_camera_time(self):
        before = datetime.now()
        event, payload = payload_for(FIXTURE.read_text())

        self.assertEqual(payload["event_type"], "VEHICE")
        self.assertEqual(payload["plate_number"], "AIDRIVE")
        self.assertEqual(payload["plate_status"], "blackList")
        self.assertEqual(payload["direction"], "away")
        self.assertEqual(payload["confidence"], 99.0)
        self.assertEqual(payload["plate_list"], "blackList")
        self.assertEqual(payload["vehicle_color"], "grey")
        self.assertEqual(payload["vehicle_brand"], "Tesla")
        self.assertEqual(payload["vehicle_type"], "saloon car")
        self.assertEqual(payload["vehicle_model"], "Tesla_ModelS")
        self.assertNotIn("vehicle", payload)

        self.assertEqual(payload["timestamp"], event.get_time_stamp_formatted())
        self.assertTrue(payload["timestamp"].endswith(".427999"))
        stamp = datetime.strptime(payload["timestamp"], "%Y-%m-%d %H:%M:%S.%f")
        self.assertGreater(abs((stamp - before).total_seconds()), 60)
        self.assertEqual(plate_log_suffix(payload), " | AIDRIVE (blacklist)")

        encoded = json.loads(json.dumps(payload))
        self.assertEqual(encoded["confidence"], 99.0)
        self.assertEqual(encoded["direction"], "away")
        self.assertEqual(encoded["plate_list"], "blackList")
        self.assertEqual(encoded["timestamp"], payload["timestamp"])

    def test_leave_is_away_and_approach_allow_list(self):
        xml = FIXTURE.read_text()
        _, leave = payload_for(xml.replace(
            ">away</vehicleDirect>", ">leave</vehicleDirect>",
        ))
        self.assertEqual(leave["direction"], "away")
        self.assertEqual(leave["plate_status"], "blackList")

        approach_xml = xml.replace(
            ">away</vehicleDirect>", ">approach</vehicleDirect>",
        ).replace(
            ">blackList</vehicleListType>", ">whiteList</vehicleListType>",
        )
        _, approach = payload_for(approach_xml)
        self.assertEqual(approach["direction"], "approach")
        self.assertEqual(approach["plate_list"], "whiteList")
        self.assertEqual(approach["plate_status"], "whiteList")
        self.assertGreaterEqual(approach["confidence"], 90)

    def test_unknown_direction_is_null(self):
        xml = FIXTURE.read_text().replace(
            ">away</vehicleDirect>", ">sideways</vehicleDirect>",
        )
        _, payload = payload_for(xml)
        self.assertIn("direction", payload)
        self.assertIsNone(payload["direction"])
        self.assertIsNone(json.loads(json.dumps(payload))["direction"])


if __name__ == "__main__":
    unittest.main()
