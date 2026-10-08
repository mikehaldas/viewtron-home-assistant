"""Plate status in build_json_payload, including viewtron 1.3.0 IP cameras."""

import json
import sys
import types
import unittest
from pathlib import Path


def _stub(name, **attrs):
    if name in sys.modules:
        return
    try:
        __import__(name)
    except ImportError:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module


_stub("viewtron", ViewtronServer=object)
_stub("requests")
_stub("yaml", safe_load=lambda *args, **kwargs: {})

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewtron-bridge"))
from viewtron_bridge import build_json_payload, format_plate_label  # noqa: E402


class Event:
    """Minimal stand-in for a parsed viewtron event."""

    def __init__(self, **methods):
        self._methods = methods

    def get_alarm_description(self):
        return "License Plate Detection"

    def get_ip_cam(self):
        return "Viewtron IPC"

    def get_time_stamp_formatted(self):
        return "2026-10-05 12:00:00"

    def get_plate_number(self):
        return "ABC1234"

    def __getattr__(self, name):
        if name in self._methods:
            return self._methods[name]
        raise AttributeError(name)


class BuildJsonPayloadLprTests(unittest.TestCase):
    def test_prefers_get_plate_group(self):
        event = Event(
            get_plate_group=lambda: "Residents",
            get_vehicle_list_type=lambda: "whiteList",
        )
        payload = build_json_payload(event, "VEHICE", "192.0.2.10")
        self.assertEqual(payload["plate_number"], "ABC1234")
        self.assertEqual(payload["plate_status"], "Residents")

    def test_vehicle_list_type_when_plate_group_is_missing(self):
        for status in ("whiteList", "blackList", "temporaryList"):
            with self.subTest(status=status):
                event = Event(get_vehicle_list_type=lambda status=status: status)
                payload = build_json_payload(event, "VEHICLE", "192.0.2.10")
                self.assertEqual(payload["plate_status"], status)
                self.assertFalse(hasattr(event, "get_plate_group"))

    def test_unknown_when_list_type_is_none(self):
        event = Event(get_vehicle_list_type=lambda: None)
        payload = build_json_payload(event, "vehicle", "192.0.2.10")
        self.assertEqual(payload["plate_status"], "Unknown")

    def test_unknown_when_neither_method_exists(self):
        event = Event()
        payload = build_json_payload(event, "VEHICE", "192.0.2.10")
        self.assertEqual(payload["plate_number"], "ABC1234")
        self.assertEqual(payload["plate_status"], "Unknown")

    def test_empty_plate_group_is_unknown(self):
        event = Event(get_plate_group=lambda: "")
        payload = build_json_payload(event, "VEHICE", "192.0.2.10")
        self.assertEqual(payload["plate_status"], "Unknown")

    def test_non_lpr_event_has_no_plate_fields(self):
        event = Event()
        payload = build_json_payload(event, "PEA", "192.0.2.10")
        self.assertNotIn("plate_number", payload)
        self.assertNotIn("plate_status", payload)
        for key in (
            "direction", "confidence", "plate_list",
            "vehicle_color", "vehicle_brand", "vehicle_type", "vehicle_model",
        ):
            self.assertNotIn(key, payload)

    def test_missing_sdk_attributes_are_null(self):
        event = Event(get_plate_group=lambda: "whiteList")
        payload = build_json_payload(event, "VEHICE", "192.0.2.10")
        for key in (
            "direction", "confidence", "plate_list",
            "vehicle_color", "vehicle_brand", "vehicle_type", "vehicle_model",
        ):
            self.assertIsNone(payload[key])
        self.assertIn('"direction": null', json.dumps(payload))
        self.assertEqual(format_plate_label(payload), "ABC1234 (whitelist)")

    def test_nvr_attributes_sit_beside_the_vehicle_object(self):
        event = Event(
            get_plate_group=lambda: "Residents",
            get_car_brand=lambda: "GMC",
            get_car_type=lambda: "mpv",
            get_car_color=lambda: "white",
            get_car_model=lambda: "GMC_SAVANA",
            get_plate_color=lambda: "blue",
        )
        event.direction = "approach"
        event.confidence = 90.0
        event.plate_list = None
        event.vehicle_color = "white"
        event.vehicle_brand = "GMC"
        event.vehicle_type = "mpv"
        event.vehicle_model = "GMC_SAVANA"
        payload = build_json_payload(event, "vehicle", "192.0.2.10")
        self.assertEqual(payload["plate_status"], "Residents")
        self.assertIsNone(payload["plate_list"])
        self.assertEqual(payload["direction"], "approach")
        self.assertEqual(payload["confidence"], 90.0)
        self.assertEqual(payload["vehicle_color"], "white")
        self.assertEqual(payload["vehicle_brand"], "GMC")
        self.assertEqual(payload["vehicle_type"], "mpv")
        self.assertEqual(payload["vehicle_model"], "GMC_SAVANA")
        self.assertEqual(payload["vehicle"], {
            "type": "mpv",
            "color": "white",
            "brand": "GMC",
            "model": "GMC_SAVANA",
        })
        self.assertEqual(payload["plate_color"], "blue")
        self.assertEqual(format_plate_label(payload), "ABC1234 (residents)")


if __name__ == "__main__":
    unittest.main()
