import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lianli_hydroshift import daemon as d
from lianli_hydroshift import openrgb as orgb


MASTER = bytes.fromhex("010203040506")
DEVICE = bytes.fromhex("a0a1a2a3a4a5")
OTHER_MASTER = bytes.fromhex("999999999999")


def record(*, master=MASTER, device=DEVICE, device_type=d.DEVICE_TYPE_WATERBLOCK2, coolant=33, rx_type=2):
    r = bytearray(42)
    r[0:6] = device
    r[6:12] = master
    r[12] = 8
    r[13] = rx_type
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
            self.assertIn("different master", str(cm.exception))
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


class DiscoveryClassificationTests(unittest.TestCase):
    ZERO = bytes(6)

    def _frame(self, *records):
        return bytes([d.USB_CMD_SEND_RF, len(records), 0, 0]) + b"".join(records)

    def test_tx_missing_takes_precedence(self):
        result = d.classify_discovery([], None, tx_present=False, rx_present=False)
        self.assertEqual(result.state, d.DiscoveryState.TX_MISSING)
        self.assertFalse(result.ok)
        self.assertIn("TX", result.message())

    def test_rx_missing_when_tx_present(self):
        result = d.classify_discovery([], None, tx_present=True, rx_present=False)
        self.assertEqual(result.state, d.DiscoveryState.RX_MISSING)
        self.assertIn("RX", result.message())

    def test_master_unknown_when_scan_fails(self):
        result = d.classify_discovery([], None)
        self.assertEqual(result.state, d.DiscoveryState.MASTER_UNKNOWN)
        self.assertIn("GET_MAC", result.message())

    def test_no_aio_records_when_only_non_aio_devices_visible(self):
        frame = self._frame(record(master=MASTER, device_type=5))
        result = d.classify_discovery([frame], MASTER)
        self.assertEqual(result.state, d.DiscoveryState.NO_AIO_RECORDS)
        self.assertIsNone(result.aio)
        self.assertIn("no HydroShift", result.message())

    def test_aio_unbound_when_master_all_zero(self):
        # The 2026-06-18 failure mode: AIO visible, master 00:..:00, invalid rx.
        unbound = record(master=self.ZERO, device=DEVICE)
        result = d.classify_discovery([self._frame(unbound)], MASTER)
        self.assertEqual(result.state, d.DiscoveryState.AIO_UNBOUND)
        self.assertIsNotNone(result.aio)
        self.assertEqual(result.aio["mac"], DEVICE)
        self.assertEqual(result.aio["master_mac"], self.ZERO)
        self.assertIn("AIO visible but unbound", result.message())

    def test_aio_foreign_when_bound_to_other_master(self):
        foreign = record(master=OTHER_MASTER, device=DEVICE)
        result = d.classify_discovery([self._frame(foreign)], MASTER)
        self.assertEqual(result.state, d.DiscoveryState.AIO_FOREIGN)
        self.assertEqual(result.aio["master_mac"], OTHER_MASTER)
        self.assertIn("different master", result.message())

    def test_aio_bound_is_the_only_healthy_state(self):
        frame = self._frame(record(master=MASTER, device=DEVICE))
        result = d.classify_discovery([frame], MASTER)
        self.assertEqual(result.state, d.DiscoveryState.AIO_BOUND)
        self.assertTrue(result.ok)
        self.assertEqual(result.aio["mac"], DEVICE)
        self.assertEqual(result.aio["seq_index"], 1)

    def test_bound_aio_preferred_over_unbound_when_both_visible(self):
        unbound = record(master=self.ZERO, device=bytes.fromhex("c0c1c2c3c4c5"))
        bound = record(master=MASTER, device=DEVICE)
        result = d.classify_discovery([self._frame(unbound, bound)], MASTER)
        self.assertEqual(result.state, d.DiscoveryState.AIO_BOUND)
        self.assertEqual(result.aio["mac"], DEVICE)

    def test_aio_detail_includes_identity_fields(self):
        rec = d.parse_record(record(master=self.ZERO))
        detail = d.aio_detail(rec)
        self.assertIn(DEVICE.hex(":"), detail)
        self.assertIn("master=00:00:00:00:00:00", detail)
        self.assertIn("rx_type=", detail)
        self.assertIn("device_type=", detail)


class BindPacketTests(unittest.TestCase):
    def test_bind_packet_layout_matches_upstream_handshake(self):
        rf = d.cmd_bind_aio(MASTER, 8, DEVICE, [40, 41, 42, 43], target_rx=1)
        self.assertEqual(len(rf), d.RF_DATA_SIZE)
        self.assertEqual(rf[0], d.RF_SELECT)
        self.assertEqual(rf[1], d.RF_PWM_CMD)
        self.assertEqual(rf[2:8], DEVICE)
        # Target master copied to bytes 8..14.
        self.assertEqual(rf[8:14], MASTER)
        self.assertEqual(rf[15], 8)  # master channel
        # Target rx copied to both byte 14 and byte 16.
        self.assertEqual(rf[14], 1)
        self.assertEqual(rf[16], 1)
        # Current PWM echoed at bytes 17..21 so the bind does not disturb fans.
        self.assertEqual(rf[17:21], bytes([40, 41, 42, 43]))

    def test_bind_packet_rejects_wrong_pwm_length(self):
        with self.assertRaises(ValueError):
            d.cmd_bind_aio(MASTER, 8, DEVICE, [40, 41, 42], target_rx=1)

    def test_save_config_is_broadcast(self):
        rf = d.cmd_save_config(MASTER)
        self.assertEqual(len(rf), d.RF_DATA_SIZE)
        self.assertEqual(rf[0], d.RF_SELECT)
        self.assertEqual(rf[1], d.RF_SAVE_CONFIG)
        # Broadcast target ff:ff:ff:ff:ff:ff and rx 0xff.
        self.assertEqual(rf[2:8], b"\xff" * 6)
        self.assertEqual(rf[8:14], MASTER)
        self.assertEqual(rf[14], 0xFF)


class DoctorLogParsingTests(unittest.TestCase):
    TELEMETRY = (
        "2026-06-18 07:59:01,123 INFO coolant=33.0C raw=33.0C stale=0.5s "
        "telemetry=ok failsafe=False fan_pwm=40/255 pump_target=1800rpm rpm=1810"
    )

    def test_parse_last_telemetry_returns_state_and_timestamp(self):
        ts, state = d.parse_last_telemetry("noise\n" + self.TELEMETRY + "\nmore noise")
        self.assertEqual(state, "ok")
        self.assertEqual(ts.year, 2026)
        self.assertEqual(ts.minute, 59)

    def test_parse_last_telemetry_picks_most_recent(self):
        old = self.TELEMETRY.replace("telemetry=ok", "telemetry=soft_stale")
        new = self.TELEMETRY  # later in the file
        _, state = d.parse_last_telemetry(old + "\n" + new)
        self.assertEqual(state, "ok")

    def test_parse_last_telemetry_handles_missing(self):
        self.assertEqual(d.parse_last_telemetry("nothing here"), (None, None))

    def test_parse_recent_discovery_state_detects_unbound(self):
        text = (
            "2026-06-18 07:00:00,000 INFO master: 01:02:03:04:05:06 ch=8\n"
            "2026-06-18 07:00:05,000 WARNING AIO visible but unbound "
            "(mac=2d:a3:74:e5:66:e1 master=00:00:00:00:00:00 ch=8 rx_type=254 device_type=11)"
        )
        self.assertEqual(d.parse_recent_discovery_state(text), d.DiscoveryState.AIO_UNBOUND)

    def test_parse_recent_discovery_state_detects_bound_control_loop(self):
        self.assertEqual(
            d.parse_recent_discovery_state("2026-06-18 07:00:10,000 INFO entering control loop"),
            d.DiscoveryState.AIO_BOUND,
        )

    def test_parse_last_rgb_applied_returns_timestamp(self):
        ts = d.parse_last_rgb_applied("2026-06-18 07:01:00,000 INFO applied OpenRGB RGB frame: 96 LEDs")
        self.assertIsNotNone(ts)
        self.assertEqual(ts.hour, 7)

    def test_parse_last_daemon_start_returns_most_recent_start(self):
        text = "\n".join([
            "2026-06-18 07:00:00,000 INFO lianli-hydroshift daemon starting",
            "2026-06-18 07:01:00,000 INFO applied OpenRGB RGB frame: 96 LEDs",
            "2026-06-18 07:02:00,000 INFO lianli-hydroshift daemon starting",
        ])
        ts = d.parse_last_daemon_start(text)
        self.assertIsNotNone(ts)
        self.assertEqual(ts.minute, 2)

    def test_process_running_ignores_current_doctor_process(self):
        class Completed:
            returncode = 0
            stdout = str(d.os.getpid()) + "\n"

        with patch.object(d.subprocess, "run", return_value=Completed()):
            self.assertFalse(d.process_running("lianli_hydroshift.daemon"))


class DoctorClassificationTests(unittest.TestCase):
    def _kwargs(self, **overrides):
        base = dict(
            tx_present=True,
            rx_present=True,
            daemon_running=True,
            telemetry_age_s=2.0,
            discovery_state=d.DiscoveryState.AIO_BOUND,
            openrgb_port_open=True,
            rgb_applied=True,
        )
        base.update(overrides)
        return base

    def test_healthy_when_everything_passes(self):
        status, action = d.classify_health(**self._kwargs())
        self.assertEqual(status, d.HealthStatus.HEALTHY)
        self.assertIn("passed", action.lower())

    def test_disconnected_takes_precedence(self):
        status, action = d.classify_health(**self._kwargs(rx_present=False, daemon_running=False))
        self.assertEqual(status, d.HealthStatus.DISCONNECTED)
        self.assertIn("RX", action)
        self.assertIn("replug", action.lower())

    def test_unbound_detected_before_daemon_down(self):
        status, action = d.classify_health(
            **self._kwargs(discovery_state=d.DiscoveryState.AIO_UNBOUND, daemon_running=False)
        )
        self.assertEqual(status, d.HealthStatus.UNBOUND)
        self.assertIn("recover-display.sh", action)

    def test_daemon_down_when_hardware_present_but_no_process(self):
        status, action = d.classify_health(**self._kwargs(daemon_running=False, discovery_state=None))
        self.assertEqual(status, d.HealthStatus.DAEMON_DOWN)
        self.assertIn("kickstart", action)

    def test_stale_when_telemetry_old(self):
        status, _ = d.classify_health(**self._kwargs(telemetry_age_s=9999.0))
        self.assertEqual(status, d.HealthStatus.STALE)

    def test_stale_when_no_telemetry_seen(self):
        status, _ = d.classify_health(**self._kwargs(telemetry_age_s=None))
        self.assertEqual(status, d.HealthStatus.STALE)

    def test_rgb_not_applied_when_cooling_healthy_but_no_frame(self):
        status, action = d.classify_health(**self._kwargs(rgb_applied=False))
        self.assertEqual(status, d.HealthStatus.RGB_NOT_APPLIED)
        self.assertIn("RGB", action)

    def test_rgb_not_applied_when_bridge_closed(self):
        status, _ = d.classify_health(**self._kwargs(openrgb_port_open=False))
        self.assertEqual(status, d.HealthStatus.RGB_NOT_APPLIED)


class AutoRebindTests(unittest.TestCase):
    ZERO = bytes(6)

    def _frame(self, *records):
        return bytes([d.USB_CMD_SEND_RF, len(records), 0, 0]) + b"".join(records)

    def _unbound_result(self):
        frame = self._frame(record(master=self.ZERO, device=DEVICE))
        return d.classify_discovery([frame], MASTER)

    def _cfg(self, **overrides):
        base = {"auto_rebind_visible_aio": True, "auto_rebind_allow_list": []}
        base.update(overrides)
        return base

    def test_disabled_by_default_config(self):
        cfg = d.load_config(None)
        self.assertFalse(cfg["auto_rebind_visible_aio"])
        eligible, reason = d.should_auto_rebind(self._unbound_result(), cfg)
        self.assertFalse(eligible)
        self.assertIn("disabled", reason)

    def test_eligible_for_single_unbound_aio_when_enabled(self):
        eligible, reason = d.should_auto_rebind(self._unbound_result(), self._cfg())
        self.assertTrue(eligible)
        self.assertIn(DEVICE.hex(":"), reason)

    def test_eligible_with_duplicate_records_for_same_unbound_aio(self):
        # collect_rx_frames samples multiple frames; the same visible AIO can
        # appear once per frame. That is still one physical AIO and should be
        # auto-rebindable.
        frame = self._frame(record(master=self.ZERO, device=DEVICE))
        result = d.classify_discovery([frame, frame, frame], MASTER)
        eligible, reason = d.should_auto_rebind(result, self._cfg())
        self.assertTrue(eligible)
        self.assertIn(DEVICE.hex(":"), reason)

    def test_not_eligible_for_bound_state(self):
        frame = self._frame(record(master=MASTER, device=DEVICE))
        bound = d.classify_discovery([frame], MASTER)
        eligible, reason = d.should_auto_rebind(bound, self._cfg())
        self.assertFalse(eligible)
        self.assertIn("not auto-rebindable", reason)

    def test_not_eligible_when_multiple_aios_visible(self):
        frame = self._frame(
            record(master=self.ZERO, device=DEVICE),
            record(master=self.ZERO, device=bytes.fromhex("c0c1c2c3c4c5")),
        )
        result = d.classify_discovery([frame], MASTER)
        eligible, reason = d.should_auto_rebind(result, self._cfg())
        self.assertFalse(eligible)
        self.assertIn("exactly one", reason)

    def test_allow_list_blocks_unlisted_mac(self):
        cfg = self._cfg(auto_rebind_allow_list=["99:99:99:99:99:99"])
        eligible, reason = d.should_auto_rebind(self._unbound_result(), cfg)
        self.assertFalse(eligible)
        self.assertIn("allow_list", reason)

    def test_allow_list_permits_listed_mac(self):
        cfg = self._cfg(auto_rebind_allow_list=[DEVICE.hex(":")])
        eligible, _ = d.should_auto_rebind(self._unbound_result(), cfg)
        self.assertTrue(eligible)

    def test_not_eligible_when_target_rx_occupied_by_non_aio_device(self):
        occupied = record(
            master=MASTER,
            device=bytes.fromhex("b0b1b2b3b4b5"),
            device_type=5,
            rx_type=d.AUTO_REBIND_TARGET_RX,
        )
        unbound = record(master=self.ZERO, device=DEVICE, rx_type=0xFE)
        result = d.classify_discovery([self._frame(occupied, unbound)], MASTER)
        eligible, reason = d.should_auto_rebind(result, self._cfg())
        self.assertFalse(eligible)
        self.assertIn("target rx", reason)

    def test_normalize_mac_list_lowercases_and_handles_dashes(self):
        self.assertEqual(
            d.normalize_mac_list(["2D-A3-74-E5-66-E1", " AA:BB:CC:DD:EE:FF "]),
            ["2d:a3:74:e5:66:e1", "aa:bb:cc:dd:ee:ff"],
        )
        self.assertEqual(d.normalize_mac_list("not-a-list"), [])


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

    def _dev(self):
        return orgb.OpenRgbDevice(
            name="Test AIO",
            vendor="Lian Li",
            serial="aa:bb:cc:dd:ee:ff",
            zones=[orgb.OpenRgbZone("Pump", 2), orgb.OpenRgbZone("Fan", 2)],
        )

    def test_fresh_bridge_has_no_received_frame_and_no_pending(self):
        bridge = orgb.OpenRgbBridge(self._dev())
        self.assertFalse(bridge.has_received_frame)
        self.assertIsNone(bridge.take_pending_frame())

    def test_client_update_marks_received_frame(self):
        bridge = orgb.OpenRgbBridge(self._dev())
        bridge.set_single_led(0, (1, 2, 3))
        self.assertTrue(bridge.has_received_frame)

    def test_prime_frame_queues_cached_colors_for_resend(self):
        frame = [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]
        bridge = orgb.OpenRgbBridge(self._dev())
        bridge.prime_frame(frame)
        self.assertTrue(bridge.has_received_frame)
        # The primed frame is pending exactly once.
        self.assertEqual(bridge.take_pending_frame(), frame)
        self.assertIsNone(bridge.take_pending_frame())

    def test_prime_frame_ignores_empty(self):
        bridge = orgb.OpenRgbBridge(self._dev())
        bridge.prime_frame([])
        self.assertFalse(bridge.has_received_frame)
        self.assertIsNone(bridge.take_pending_frame())


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


class RgbFramePersistTests(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            config_path = f.name
        try:
            frame = [(255, 0, 128), (0, 255, 64), (10, 20, 30)]
            d.save_rgb_frame(config_path, frame)
            loaded = d.load_rgb_frame(config_path)
            self.assertEqual(loaded, frame)
            # File is next to config, not config itself.
            persist_path = d._rgb_frame_path(config_path)
            self.assertTrue(persist_path.exists())
            self.assertNotEqual(str(persist_path), str(config_path))
        finally:
            d._rgb_frame_path(config_path).unlink(missing_ok=True)

    def test_load_returns_none_when_no_file_exists(self):
        result = d.load_rgb_frame("/nonexistent/path/to/config.json")
        self.assertIsNone(result)

    def test_load_returns_none_for_corrupt_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            config_path = f.name
        try:
            persist_path = d._rgb_frame_path(config_path)
            persist_path.write_text("not json")
            result = d.load_rgb_frame(config_path)
            self.assertIsNone(result)
        finally:
            persist_path.unlink(missing_ok=True)

    def test_load_returns_none_for_empty_list(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            config_path = f.name
        try:
            persist_path = d._rgb_frame_path(config_path)
            persist_path.write_text("[]")
            result = d.load_rgb_frame(config_path)
            self.assertIsNone(result)
        finally:
            persist_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
