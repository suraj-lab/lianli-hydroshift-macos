import json
import tempfile
import unittest
from pathlib import Path

from lianli_hydroshift import daemon as d
from lianli_hydroshift import openrgb as orgb


MASTER = bytes.fromhex("010203040506")
DEVICE = bytes.fromhex("a0a1a2a3a4a5")
OTHER_MASTER = bytes.fromhex("999999999999")


def record(*, master=MASTER, device=DEVICE, device_type=d.DEVICE_TYPE_WATERBLOCK2, coolant=33):
    r = bytearray(42)
    r[0:6] = device
    r[6:12] = master
    r[12] = 8
    r[13] = 2
    r[18] = device_type
    r[19] = 3
    r[20:24] = b"\x01\x02\x03\x04"
    r[24:28] = b"\x28\x28\x28" + bytes([coolant])
    r[28:30] = (900).to_bytes(2, "big")
    r[30:32] = (910).to_bytes(2, "big")
    r[32:34] = (920).to_bytes(2, "big")
    r[34:36] = (2000).to_bytes(2, "big")
    r[36:40] = bytes([40, 40, 40, 0])
    r[40] = 7
    r[41] = 0x1C
    return bytes(r)


class DaemonProtocolTests(unittest.TestCase):
    def test_square_pump_timer_matches_upstream_samples(self):
        samples = {
            1600: 1590,
            1700: 1495,
            1900: 1300,
            2100: 1100,
            2300: 900,
            2500: 700,
            2700: 469,
            2900: 210,
            3100: 45,
            3200: 0,
        }
        for rpm, timer in samples.items():
            self.assertEqual(d.square_pump_timer(rpm), timer)

    def test_aio_params_start_at_rf_offset_18(self):
        param = d.build_aio_param(pump_rpm=2000, theme_index=3, brightness=80, rotation=1)
        self.assertEqual(len(param), d.AIO_PARAM_LEN)
        self.assertEqual(int.from_bytes(param[28:30], "big"), 1200)
        self.assertEqual(param[27], 3)
        rf = d.cmd_aio_params(MASTER, 8, DEVICE, 2, param, seq_index=1)
        self.assertEqual(rf[0], d.RF_SELECT)
        self.assertEqual(rf[1], d.RF_AIO_PARAMS)
        self.assertEqual(rf[18 : 18 + d.AIO_PARAM_LEN], param)
        self.assertEqual(rf[17], 0)

    def test_parse_normal_frame_filters_by_master_and_uses_bound_seq_index(self):
        other_device = bytes.fromhex("b0b1b2b3b4b5")
        frame = bytes([d.USB_CMD_SEND_RF, 2, 0, 0]) + record(master=OTHER_MASTER, device=other_device) + record(master=MASTER)
        aio = d.find_aio([frame], master_mac=MASTER)
        self.assertIsNotNone(aio)
        self.assertEqual(aio["mac"], DEVICE)
        self.assertEqual(aio["master_mac"], MASTER)
        self.assertEqual(aio["coolant_temp"], 33)
        # Only one device is bound to this master in the frame, so command index is 1.
        self.assertEqual(aio["seq_index"], 1)

    def test_discovery_summary_counts_unbound_records(self):
        frame = bytes([d.USB_CMD_SEND_RF, 1, 0, 0]) + record(master=OTHER_MASTER)
        summary = d.discovery_summary([frame], MASTER)
        self.assertIn("frames=1", summary)
        self.assertIn("records=1", summary)
        self.assertIn("bound=0", summary)
        self.assertIn(str(d.DEVICE_TYPE_WATERBLOCK2), summary)

    def test_connect_hydroshift_releases_handles_when_aio_missing(self):
        tx = object()
        rx = object()
        claimed = []
        released = []
        originals = {
            "find_dongle": d.find_dongle,
            "claim": d.claim,
            "release": d.release,
            "discover_master": d.discover_master,
            "collect_rx_frames": d.collect_rx_frames,
        }

        def fake_find_dongle(ids):
            return tx if ids == d.TX_IDS else rx

        try:
            d.find_dongle = fake_find_dongle
            d.claim = claimed.append
            d.release = lambda dev, name: released.append((dev, name))
            d.discover_master = lambda _tx: (MASTER, 8)
            d.collect_rx_frames = lambda _rx, count=3, max_wait=3.0: [
                bytes([d.USB_CMD_SEND_RF, 1, 0, 0]) + record(master=OTHER_MASTER)
            ]
            with self.assertRaises(d.DiscoveryError) as cm:
                d.connect_hydroshift()
            self.assertIn("no bound HydroShift", str(cm.exception))
            self.assertEqual(claimed, [tx, rx])
            self.assertEqual(released, [(tx, "TX"), (rx, "RX")])
        finally:
            for name, original in originals.items():
                setattr(d, name, original)

    def test_fallback_scan_checks_last_possible_record_offset(self):
        # Regression for range(len(frame) - 42), which missed a record at the
        # final valid start offset.
        frame = b"xxxxx" + record()
        parsed = d.parse_frame_records(frame)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["mac"], DEVICE)

    def test_default_curves_are_quiet_then_aggressive(self):
        cfg = d.load_config(None)
        low_pwm = d.apply_min_pwm(d.interpolate_pwm(30, cfg["fan_curve"]), cfg["min_pwm"])
        mid_pwm = d.apply_min_pwm(d.interpolate_pwm(40, cfg["fan_curve"]), cfg["min_pwm"])
        hot_pwm = d.apply_min_pwm(d.interpolate_pwm(50, cfg["fan_curve"]), cfg["min_pwm"])
        self.assertLess(low_pwm, 45)
        self.assertGreater(mid_pwm, low_pwm)
        self.assertEqual(hot_pwm, 255)
        self.assertEqual(d.resolve_pump_rpm(28, cfg), 1800)
        self.assertEqual(d.resolve_pump_rpm(34, cfg), 1800)
        self.assertEqual(d.resolve_pump_rpm(50, cfg), 3200)

    def test_coolant_filter_limits_sudden_drops(self):
        cfg = d.load_config(None)
        filtered = d.filter_coolant_reading(28.0, 30.0, cfg)
        self.assertGreater(filtered, 29.0)
        filtered_rise = d.filter_coolant_reading(45.0, 30.0, cfg)
        self.assertLess(filtered_rise, 32.0)
        self.assertGreater(filtered_rise, 30.0)

    def test_coolant_rejection_catches_bogus_low_readings(self):
        cfg = d.load_config(None)
        self.assertIsNotNone(d.coolant_rejection_reason(14.0, 25.0, cfg))
        self.assertIsNotNone(d.coolant_rejection_reason(20.0, 30.0, cfg))
        self.assertIsNone(d.coolant_rejection_reason(29.0, 30.0, cfg))

    def test_coolant_rejection_allows_sustained_cooldown(self):
        cfg = d.load_config(None)
        self.assertIsNotNone(d.coolant_rejection_reason(29.0, 36.0, cfg, stale_age_s=0))
        self.assertIsNone(d.coolant_rejection_reason(29.0, 36.0, cfg, stale_age_s=90))

    def test_stale_targets_are_moderate_not_full_blast(self):
        cfg = d.load_config(None)
        pwm, pump = d.stale_targets(30.0, 35, 1800, cfg)
        self.assertEqual(pwm, cfg["stale_pwm"])
        self.assertEqual(pump, cfg["stale_pump_rpm"])
        self.assertLess(pwm, cfg["failsafe_pwm"])
        self.assertLess(pump, cfg["failsafe_pump_rpm"])

    def test_rgb_direct_frame_layout_matches_wireless_protocol(self):
        compressed = bytes(range(230))
        frames = d.build_rgb_direct_frames(
            master_mac=MASTER,
            device_mac=DEVICE,
            colors=[(255, 0, 0), (0, 255, 0)],
            effect_index=b"\x01\x02\x03\x04",
            compressor=lambda raw: compressed,
            interval_ms=5000,
        )
        self.assertEqual(len(frames), 3)
        header, first, second = frames
        self.assertEqual(header[0], d.RF_SELECT)
        self.assertEqual(header[1], d.RF_SET_RGB)
        self.assertEqual(header[2:8], DEVICE)
        self.assertEqual(header[8:14], MASTER)
        self.assertEqual(header[14:18], b"\x01\x02\x03\x04")
        self.assertEqual(header[18], 0)
        self.assertEqual(header[19], 3)  # header + two payload packets
        self.assertEqual(int.from_bytes(header[20:24], "big"), len(compressed))
        self.assertEqual(int.from_bytes(header[25:27], "big"), 1)
        self.assertEqual(header[27], 2)
        self.assertEqual(int.from_bytes(header[32:34], "big"), 5000)
        self.assertEqual(first[18], 1)
        self.assertEqual(first[20:240], compressed[:220])
        self.assertEqual(second[18], 2)
        self.assertEqual(second[20:30], compressed[220:])

    def test_openrgb_device_from_aio_record_has_pump_and_fan_zones(self):
        rec = d.parse_record(record(coolant=33))
        dev = d.openrgb_device_from_record(rec)
        self.assertEqual(dev.name, "HydroShift II LCD-S (Wireless)")
        self.assertEqual([z.name for z in dev.zones], ["Pump Head", "Fan 1", "Fan 2", "Fan 3"])
        self.assertEqual(dev.total_leds, 96)

    def test_slew_limit_slows_audible_steps(self):
        self.assertEqual(d.slew_limit(35, 60, up_step=4, down_step=3), 39)
        self.assertEqual(d.slew_limit(60, 35, up_step=4, down_step=3), 57)
        self.assertEqual(d.slew_limit(35, 37, up_step=4, down_step=3), 37)


class OpenRgbBridgeTests(unittest.TestCase):
    def test_bridge_splits_full_led_updates_into_zones(self):
        dev = orgb.OpenRgbDevice(
            name="Test AIO",
            vendor="Lian Li",
            serial="aa:bb:cc:dd:ee:ff",
            zones=[orgb.OpenRgbZone("Pump", 2), orgb.OpenRgbZone("Fan", 3)],
        )
        bridge = orgb.OpenRgbBridge(dev)
        bridge.set_all_colors([(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15)])
        self.assertEqual(
            bridge.take_pending_frame(),
            [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15)],
        )
        self.assertIsNone(bridge.take_pending_frame())

    def test_bridge_zone_and_single_led_updates_preserve_flat_order(self):
        dev = orgb.OpenRgbDevice(
            name="Test AIO",
            vendor="Lian Li",
            serial="aa:bb:cc:dd:ee:ff",
            zones=[orgb.OpenRgbZone("Pump", 2), orgb.OpenRgbZone("Fan", 2)],
        )
        bridge = orgb.OpenRgbBridge(dev)
        bridge.set_zone_colors(1, [(9, 8, 7), (6, 5, 4)])
        bridge.set_single_led(0, (1, 2, 3))
        self.assertEqual(bridge.take_pending_frame(), [(1, 2, 3), (255, 255, 255), (9, 8, 7), (6, 5, 4)])


class SetThemeConfigTests(unittest.TestCase):
    def _write_config(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f)

    def test_set_theme_updates_existing_config(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"theme_index": 0, "theme_index_max": 31}, f)
            path = f.name
        d.set_theme_in_config(path, 7)
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["theme_index"], 7)
        self.assertEqual(data["theme_index_max"], 31)

    def test_set_theme_creates_config_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "config.json"
            written = d.set_theme_in_config(path, 5)
            self.assertEqual(written, 5)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["theme_index"], 5)

    def test_set_theme_clamps_to_theme_index_max(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"theme_index_max": 12}, f)
            path = f.name
        written = d.set_theme_in_config(path, 99)
        self.assertEqual(written, 12)
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["theme_index"], 12)

    def test_set_theme_clamps_negative_to_zero(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({}, f)
            path = f.name
        written = d.set_theme_in_config(path, -3)
        self.assertEqual(written, 0)


if __name__ == "__main__":
    unittest.main()
