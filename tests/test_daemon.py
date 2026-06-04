import unittest

from lianli_hydroshift import daemon as d


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

    def test_slew_limit_slows_audible_steps(self):
        self.assertEqual(d.slew_limit(35, 60, up_step=4, down_step=3), 39)
        self.assertEqual(d.slew_limit(60, 35, up_step=4, down_step=3), 57)
        self.assertEqual(d.slew_limit(35, 37, up_step=4, down_step=3), 37)


if __name__ == "__main__":
    unittest.main()
