"""Windows metadata inspection uses getters only and requires no real audio."""

import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aqua.audio import windows_endpoint as endpoint


class EndpointMetadataTests(unittest.TestCase):
    def test_guid_layout_matches_com_binary_identity(self):
        identifier = endpoint._Guid.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
        self.assertEqual(ctypes.sizeof(identifier), 16)
        self.assertEqual(bytes(identifier).hex(), "9503debc2fe57c468e3dc4579291692e")

    def test_nonwindows_is_unavailable_without_loading_audio(self):
        with patch.object(endpoint.sys, "platform", "other"), patch.object(endpoint, "get_backend") as backend:
            result = endpoint.inspect_capture_endpoints()
        self.assertFalse(result["available"])
        self.assertEqual(result["defaults"], [])
        self.assertEqual(result["devices"], [])
        backend.assert_not_called()

    def test_optional_backend_failure_is_reported_without_driver_text(self):
        with patch.object(endpoint.sys, "platform", "win32"), patch.object(endpoint, "get_backend", side_effect=OSError("private driver string")):
            result = endpoint.inspect_capture_endpoints()
        self.assertFalse(result["available"])
        self.assertEqual(result["errors"], ["Windows capture metadata unavailable (OSError)"])
        self.assertNotIn("private", str(result))

    def test_com_initialization_is_balanced_only_when_owned(self):
        for value, expected in ((0, 1), (1, 1), (-2147417850, 0)):
            with self.subTest(result=value):
                ole = SimpleNamespace(CoInitializeEx=Mock(return_value=value), CoUninitialize=Mock())
                with endpoint._apartment(ole):
                    pass
                self.assertEqual(ole.CoUninitialize.call_count, expected)

    def test_com_failure_does_not_uninitialize_someone_elses_apartment(self):
        ole = SimpleNamespace(CoInitializeEx=Mock(return_value=-1), CoUninitialize=Mock())
        with self.assertRaises(OSError):
            with endpoint._apartment(ole):
                self.fail("Failed initialization must not yield")
        ole.CoUninitialize.assert_not_called()

    def test_volume_snapshot_calls_only_known_getter_slots(self):
        slots = []

        def method(_pointer, slot, _result, *_types):
            slots.append(slot)

            def call(_self, *arguments):
                if slot == 5:
                    ctypes.cast(arguments[0], ctypes.POINTER(endpoint.UINT))[0] = 2
                elif slot == 15:
                    ctypes.cast(arguments[0], ctypes.POINTER(ctypes.c_int32))[0] = 0
                elif slot in (8, 9):
                    ctypes.cast(arguments[0], ctypes.POINTER(ctypes.c_float))[0] = 30 if slot == 8 else 1
                elif slot == 13:
                    ctypes.cast(arguments[1], ctypes.POINTER(ctypes.c_float))[0] = 1 if arguments[0] == 0 else .5
                elif slot == 20:
                    for pointer, value in zip(arguments, (-17, 30, .03125)):
                        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_float))[0] = value
                else:
                    raise AssertionError(f"Unexpected getter slot {slot}")
                return 0

            return call

        with patch.object(endpoint, "_method", side_effect=method):
            result = endpoint._volume(ctypes.c_void_p(123))
        self.assertEqual(set(slots), {5, 8, 9, 13, 15, 20})
        self.assertEqual(result, {
            "volume_percent": 100.0, "muted": False, "channels": 2,
            "channel_percents": [100.0, 50.0], "level_db": 30.0,
            "range_db": {"minimum": -17.0, "maximum": 30.0, "step": .03125},
        })

    def test_endpoint_identity_frees_only_its_allocated_string(self):
        text = ctypes.create_unicode_buffer("{capture-endpoint}")
        ole = SimpleNamespace(CoTaskMemFree=Mock())

        def get_identity(_pointer, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(text)
            return 0

        with patch.object(endpoint, "_method", return_value=get_identity) as getter, patch.object(endpoint, "_release") as release:
            result = endpoint._identity(ctypes.c_void_p(123), ole)
        self.assertEqual(result, "{capture-endpoint}")
        self.assertEqual(getter.call_args.args[1], 5)
        ole.CoTaskMemFree.assert_called_once()
        release.assert_not_called()  # The device can be PortAudio's borrowed reference.

    def test_mapping_does_not_guess_ambiguous_mme_names(self):
        backend = SimpleNamespace(
            query_devices=lambda: [{"name": "Same microphone", "hostapi": 0, "max_input_channels": 2}],
            query_hostapis=lambda: [{"name": "MME"}],
            _libname="fake-portaudio",
        )
        candidates = [
            {"name": "Same microphone", "endpoint_id": "one", "wavein_index": 0},
            {"name": "Same microphone", "endpoint_id": "two", "wavein_index": 1},
        ]
        with patch.object(endpoint, "_winmm_devices", return_value=candidates), patch.object(endpoint.ctypes, "CDLL", return_value=SimpleNamespace()):
            result = endpoint._map_devices(backend, Mock(), [{"role": "console", "endpoint_id": "one"}])
        self.assertIsNone(result[0]["endpoint_id"])
        self.assertIsNone(result[0]["matches_console_default"])
        self.assertIn("ambiguous", result[0]["mapping"])

    def test_unique_winmm_function_instance_identity_matches_default(self):
        backend = SimpleNamespace(
            query_devices=lambda: [{"name": "Microphone Array", "hostapi": 0, "max_input_channels": 2}],
            query_hostapis=lambda: [{"name": "MME"}],
            _libname="fake-portaudio",
        )
        candidate = {"name": "Microphone Array", "endpoint_id": "{same}", "wavein_index": 3}
        with patch.object(endpoint, "_winmm_devices", return_value=[candidate]), patch.object(endpoint.ctypes, "CDLL", return_value=SimpleNamespace()):
            result = endpoint._map_devices(backend, Mock(), [{"role": "console", "endpoint_id": "{same}"}])
        self.assertEqual(result[0]["endpoint_id"], "{same}")
        self.assertEqual(result[0]["wavein_index"], 3)
        self.assertTrue(result[0]["matches_console_default"])

    def test_unsupported_backend_stays_unknown(self):
        backend = SimpleNamespace(
            query_devices=lambda: [{"name": "Microphone Array", "hostapi": 0, "max_input_channels": 2}],
            query_hostapis=lambda: [{"name": "Windows DirectSound"}],
            _libname="fake-portaudio",
        )
        with patch.object(endpoint, "_winmm_devices", return_value=[]), patch.object(endpoint.ctypes, "CDLL", return_value=SimpleNamespace()):
            result = endpoint._map_devices(backend, Mock(), [{"role": "console", "endpoint_id": "{same}"}])
        self.assertIsNone(result[0]["endpoint_id"])
        self.assertIsNone(result[0]["matches_console_default"])


if __name__ == "__main__":
    unittest.main()
