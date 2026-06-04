#!/usr/bin/env python3
"""Lian Li HydroShift II wireless AIO daemon.

This started as a macOS/PyUSB port of the relevant wireless pieces from
https://github.com/sgtaziz/lian-li-linux, with extra guard rails for running as
an always-on daemon.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Iterable

try:
    import usb.core as usb_core
    import usb.util as usb_util
except ImportError:  # pragma: no cover - exercised on hosts without pyusb
    usb_core = None
    usb_util = None

USB_ERROR = getattr(usb_core, "USBError", Exception) if usb_core else Exception
USB_TIMEOUT_ERROR = getattr(usb_core, "USBTimeoutError", TimeoutError) if usb_core else TimeoutError
LOG = logging.getLogger("lianli-hydroshift")

# ---------- Hardware constants ----------

TX_IDS = [(0x0416, 0x8040), (0x1A86, 0xE304)]
RX_IDS = [(0x0416, 0x8041), (0x1A86, 0xE305)]

USB_CMD_SEND_RF = 0x10
USB_CMD_GET_MAC = 0x11
USB_TIMEOUT = 1000

RF_SELECT = 0x12
RF_PWM_CMD = 0x10
RF_MASTER_CLOCK = 0x14
RF_AIO_SWITCH_WIRELESS = 0x19
RF_AIO_PARAMS = 0x21
RF_DATA_SIZE = 240
RF_CHUNK_SIZE = 60
RF_CHUNKS = 4
AIO_PARAM_LEN = 32

DEVICE_TYPE_WATERBLOCK = 10
DEVICE_TYPE_WATERBLOCK2 = 11
AIO_DEVICE_TYPES = {DEVICE_TYPE_WATERBLOCK, DEVICE_TYPE_WATERBLOCK2}

PUMP_MIN_RPM = 1600
PUMP_MAX_RPM_WATERBLOCK = 2500
PUMP_MAX_RPM_WATERBLOCK2 = 3200

# ---------- Quiet/performance defaults ----------

# PWM values are raw 0-255 duty. HydroShift II AIO fan minimum duty is 10%, so
# any non-zero value below min_pwm is lifted by apply_min_pwm(). This balanced
# coolant curve is quiet around idle/desktop temps, smooth through normal load,
# and intentionally protective once coolant reaches the mid/high 40s.
DEFAULT_FAN_CURVE = [
    (26.0, 35),   # ~14%: near-silent idle floor
    (30.0, 36),   # ~14%: avoid audible idle/load transition
    (32.0, 40),   # ~16%
    (34.0, 48),   # ~19%
    (36.0, 62),   # ~24%
    (38.0, 82),   # ~32%
    (40.0, 108),  # ~42%
    (42.0, 138),  # ~54%
    (44.0, 172),  # ~67%
    (46.0, 205),  # ~80%
    (48.0, 235),  # ~92%
    (50.0, 255),  # 100%: hot coolant / protective ceiling
]

# Pump curve is intentionally smoother than the fan curve. Keep flow quiet at
# idle, lift it before coolant is genuinely hot, and only use max pump when fan
# noise will dominate anyway.
DEFAULT_PUMP_CURVE_RPM = [
    (28.0, 1800),
    (34.0, 1800),
    (38.0, 1900),
    (42.0, 2200),
    (46.0, 2600),
    (48.0, 2900),
    (50.0, 3200),
]

DEFAULT_CONFIG: dict[str, Any] = {
    "fan_curve": [list(p) for p in DEFAULT_FAN_CURVE],
    "pump": {
        "mode": "curve",  # "curve" or "constant"
        "rpm": 2000,
        "curve": [list(p) for p in DEFAULT_PUMP_CURVE_RPM],
    },
    "min_pwm": 26,
    "pwm_hysteresis": 4,
    "rpm_hysteresis": 75,
    "pwm_ramp_up_per_tick": 4,
    "pwm_ramp_down_per_tick": 3,
    "rpm_ramp_up_per_tick": 75,
    "rpm_ramp_down_per_tick": 50,
    "coolant_min_valid_c": 18.0,
    "coolant_max_valid_c": 70.0,
    "coolant_smoothing_alpha": 0.45,
    "coolant_max_drop_per_tick_c": 0.75,
    "coolant_max_rise_per_tick_c": 2.0,
    "coolant_reject_drop_c": 4.0,
    "coolant_reject_drop_extra_c_per_s": 0.04,
    "keepalive_interval_s": 1.0,
    "log_interval_s": 10.0,
    "telemetry_soft_stale_s": 30.0,
    "telemetry_hard_stale_s": 600.0,
    "stale_pwm": 60,
    "stale_pump_rpm": 1950,
    "telemetry_max_stale_s": 600.0,
    "failsafe_pwm": 160,
    "failsafe_pump_rpm": 2800,
    "theme_index": 0,
    # Confirmed safe range is 0-12. Indexes 13+ corrupt the device display state
    # and require a physical USB dongle replug to recover.
    "theme_index_max": 12,
    "brightness": 80,
    "rotation": 0,
    # Optional upstream heartbeat. Leave off unless you see fallback RPM spikes.
    "send_master_clock": False,
}

running = True
reload_requested = False


def require_usb() -> None:
    if usb_core is None or usb_util is None:
        raise RuntimeError(
            "pyusb is not installed in this Python environment. "
            "Install it in the project venv with: python3 -m pip install -r requirements.txt"
        )


# ---------- Utility helpers ----------


def clamp_int(value: Any, low: int, high: int, name: str) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return max(low, min(high, v))


def clamp_float(value: Any, low: float, high: float, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    return max(low, min(high, v))


def interpolate_value(x: float, curve: list[tuple[float, int]]) -> int:
    pts = sorted(curve)
    if not pts:
        raise ValueError("curve must not be empty")
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for idx in range(len(pts) - 1):
        x1, y1 = pts[idx]
        x2, y2 = pts[idx + 1]
        if x1 <= x <= x2:
            if x2 == x1:
                return y2
            frac = (x - x1) / (x2 - x1)
            return int(round(y1 + frac * (y2 - y1)))
    return pts[-1][1]


def normalize_curve(
    raw: Any,
    *,
    name: str,
    y_low: int,
    y_high: int,
) -> list[tuple[float, int]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{name} must be a non-empty list of [coolant_c, value] points")
    points: list[tuple[float, int]] = []
    for idx, point in enumerate(raw):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"{name}[{idx}] must be [coolant_c, value]")
        temp = clamp_float(point[0], -20.0, 120.0, f"{name}[{idx}][0]")
        val = clamp_int(point[1], y_low, y_high, f"{name}[{idx}][1]")
        points.append((temp, val))
    points.sort(key=lambda p: p[0])
    return points


def deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------- USB helpers ----------


def find_dongle(id_list: Iterable[tuple[int, int]]):
    require_usb()
    for vid, pid in id_list:
        dev = usb_core.find(idVendor=vid, idProduct=pid)
        if dev is not None:
            LOG.debug("found USB dongle %04x:%04x", vid, pid)
            return dev
    return None


def claim(dev) -> None:
    require_usb()
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, USB_ERROR):
        # macOS commonly raises NotImplementedError here; the working script did
        # the right thing by treating this as non-fatal.
        pass
    try:
        dev.set_configuration()
    except USB_ERROR:
        # Already configured is fine.
        pass
    usb_util.claim_interface(dev, 0)


def release(dev, name: str) -> None:
    if dev is None or usb_util is None:
        return
    try:
        usb_util.release_interface(dev, 0)
    except Exception as exc:  # pragma: no cover - defensive daemon cleanup
        LOG.debug("%s release_interface failed: %s", name, exc)
    try:
        usb_util.dispose_resources(dev)
    except Exception as exc:  # pragma: no cover - defensive daemon cleanup
        LOG.debug("%s dispose_resources failed: %s", name, exc)


def get_endpoints(dev) -> tuple[int, int]:
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    out_ep = in_ep = None
    for ep in intf:
        if ep.bEndpointAddress & 0x80:
            in_ep = ep.bEndpointAddress
        else:
            out_ep = ep.bEndpointAddress
    if out_ep is None or in_ep is None:
        raise RuntimeError("USB interface 0 does not expose both IN and OUT endpoints")
    return out_ep, in_ep


# ---------- Discovery / telemetry ----------


def discover_master(tx) -> tuple[bytes | None, int | None]:
    out_ep, in_ep = get_endpoints(tx)
    channels = [8] + [c for c in range(2, 39, 2) if c != 8] + list(range(1, 40, 2))
    for ch in channels:
        cmd = bytearray(64)
        cmd[0] = USB_CMD_GET_MAC
        cmd[1] = ch
        try:
            tx.write(out_ep, bytes(cmd), USB_TIMEOUT)
        except Exception:
            continue
        try:
            resp = bytes(tx.read(in_ep, 64, 500))
        except USB_TIMEOUT_ERROR:
            continue
        if len(resp) >= 7 and resp[0] == USB_CMD_GET_MAC:
            mac = resp[1:7]
            if any(b != 0 for b in mac):
                return bytes(mac), ch
    return None, None


def collect_rx_frames(rx, count: int = 3, max_wait: float = 3.0) -> list[bytes]:
    out_ep, in_ep = get_endpoints(rx)
    wake = bytearray(64)
    wake[0] = USB_CMD_SEND_RF
    wake[1] = 0x01
    frames: list[bytes] = []
    deadline = time.time() + max_wait
    while len(frames) < count and time.time() < deadline:
        try:
            rx.write(out_ep, bytes(wake), 500)
        except Exception:
            pass
        try:
            data = bytes(rx.read(in_ep, 512, 1000))
            if len(data) >= 46:
                frames.append(data)
        except USB_TIMEOUT_ERROR:
            continue
    return frames


def parse_record(data: bytes, list_index: int | None = None) -> dict[str, Any] | None:
    if len(data) < 42 or data[41] != 0x1C or data[18] == 0xFF:
        return None
    mac = bytes(data[0:6])
    if mac == b"\x00" * 6 or mac == b"\xff" * 6:
        return None
    device_type = data[18]
    coolant = data[27] if device_type in AIO_DEVICE_TYPES and data[27] > 0 else None
    return {
        "mac": mac,
        "master_mac": bytes(data[6:12]),
        "channel": data[12],
        "rx_type": data[13],
        "device_type": device_type,
        "fan_count": min(data[19], 4),
        "effect_index": list(data[20:24]),
        "fan_types": list(data[24:28]),
        "coolant_temp": coolant,
        "fan_rpms": [
            int.from_bytes(data[28:30], "big"),
            int.from_bytes(data[30:32], "big"),
            int.from_bytes(data[32:34], "big"),
            int.from_bytes(data[34:36], "big"),
        ],
        "current_pwm": list(data[36:40]),
        "cmd_seq": data[40],
        "list_index": list_index,
        "seq_index": 1,
    }


def parse_frame_records(frame: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    # Normal RX GetDev response: [cmd, count, mobo_pwm bytes..., records...]
    if len(frame) >= 46 and frame[0] == USB_CMD_SEND_RF:
        device_count = frame[1]
        if 0 < device_count <= 12:
            offset = 4
            for idx in range(device_count):
                if offset + 42 > len(frame):
                    break
                rec = parse_record(frame[offset : offset + 42], list_index=idx)
                if rec:
                    records.append(rec)
                offset += 42
            if records:
                return records

    # Fallback for captures where the record is embedded at an unknown offset.
    seen: set[bytes] = set()
    for start in range(0, max(0, len(frame) - 42 + 1)):
        rec = parse_record(frame[start : start + 42], list_index=None)
        if rec and rec["mac"] not in seen:
            seen.add(rec["mac"])
            records.append(rec)
    return records


def find_aio(frames: list[bytes], master_mac: bytes | None = None) -> dict[str, Any] | None:
    all_records: list[dict[str, Any]] = []
    for frame in frames:
        all_records.extend(parse_frame_records(frame))

    if master_mac is not None:
        bound_records = [r for r in all_records if r["master_mac"] == master_mac]
    else:
        bound_records = all_records

    # Match upstream's stable command index: 1-based position among devices bound
    # to this master, not a rolling packet counter.
    for idx, rec in enumerate(bound_records):
        rec["seq_index"] = idx + 1

    for rec in bound_records:
        if rec["device_type"] == DEVICE_TYPE_WATERBLOCK2:
            return rec
    for rec in bound_records:
        if rec["device_type"] == DEVICE_TYPE_WATERBLOCK:
            return rec
    return None


# ---------- RF commands ----------


def send_rf_chunks(tx, rf_data: bytes, packet_channel: int, packet_rx_type: int) -> None:
    if len(rf_data) != RF_DATA_SIZE:
        raise ValueError(f"rf_data must be exactly {RF_DATA_SIZE} bytes")
    out_ep, _ = get_endpoints(tx)
    for chunk_idx in range(RF_CHUNKS):
        packet = bytearray(64)
        packet[0] = USB_CMD_SEND_RF
        packet[1] = chunk_idx
        packet[2] = packet_channel
        packet[3] = packet_rx_type
        start = chunk_idx * RF_CHUNK_SIZE
        end = start + RF_CHUNK_SIZE
        packet[4:64] = rf_data[start:end]
        tx.write(out_ep, bytes(packet), USB_TIMEOUT)
        time.sleep(0.001)


def send_rf_frame(tx, rf_data: bytes, device_channel: int, rx_type: int) -> None:
    send_rf_chunks(tx, rf_data, device_channel, rx_type)


def cmd_switch_wireless_theme(master_mac: bytes, master_ch: int, device_mac: bytes, rx_type: int) -> bytes:
    rf = bytearray(RF_DATA_SIZE)
    rf[0] = RF_SELECT
    rf[1] = RF_AIO_SWITCH_WIRELESS
    rf[2:8] = device_mac
    rf[8:14] = master_mac
    rf[14] = rx_type
    rf[15] = master_ch
    return bytes(rf)


def cmd_pwm(master_mac: bytes, master_ch: int, device_mac: bytes, rx_type: int, pwm_values: list[int], seq_index: int) -> bytes:
    if len(pwm_values) != 4:
        raise ValueError("pwm_values must contain four slots")
    rf = bytearray(RF_DATA_SIZE)
    rf[0] = RF_SELECT
    rf[1] = RF_PWM_CMD
    rf[2:8] = device_mac
    rf[8:14] = master_mac
    rf[14] = rx_type
    rf[15] = master_ch
    rf[16] = clamp_int(seq_index, 1, 255, "seq_index")
    rf[17:21] = bytes(clamp_int(v, 0, 255, "pwm") for v in pwm_values)
    return bytes(rf)


def cmd_aio_params(master_mac: bytes, master_ch: int, device_mac: bytes, rx_type: int, aio_param: bytes, seq_index: int) -> bytes:
    if len(aio_param) != AIO_PARAM_LEN:
        raise ValueError(f"aio_param must be exactly {AIO_PARAM_LEN} bytes")
    rf = bytearray(RF_DATA_SIZE)
    rf[0] = RF_SELECT
    rf[1] = RF_AIO_PARAMS
    rf[2:8] = device_mac
    rf[8:14] = master_mac
    rf[14] = rx_type
    rf[15] = master_ch
    rf[16] = clamp_int(seq_index, 1, 255, "seq_index")
    # aio_param starts at offset 18, matching upstream lian-li-linux.
    rf[18 : 18 + AIO_PARAM_LEN] = aio_param
    return bytes(rf)


def send_master_clock(tx, master_mac: bytes, master_ch: int) -> None:
    rf = bytearray(RF_DATA_SIZE)
    rf[0] = RF_SELECT
    rf[1] = RF_MASTER_CLOCK
    rf[8:14] = master_mac
    send_rf_chunks(tx, bytes(rf), packet_channel=master_ch, packet_rx_type=0xFF)


# ---------- Pump / AIO parameter block ----------


def square_pump_timer(rpm: int) -> int:
    """Map target pump RPM -> firmware timer for WaterBlock2 / HydroShift II Square."""
    rpm_f = float(max(PUMP_MIN_RPM, min(PUMP_MAX_RPM_WATERBLOCK2, int(rpm))))
    if rpm_f <= 1800.0:
        timer = 1590.0 - (rpm_f - 1600.0) * 0.95
    elif rpm_f <= 2000.0:
        timer = 1400.0 - (rpm_f - 1800.0)
    elif rpm_f <= 2200.0:
        timer = 1200.0 - (rpm_f - 2000.0)
    elif rpm_f <= 2400.0:
        timer = 1000.0 - (rpm_f - 2200.0)
    elif rpm_f <= 2600.0:
        timer = 800.0 - (rpm_f - 2400.0)
    elif rpm_f <= 2800.0:
        timer = 580.0 - (rpm_f - 2600.0) * 1.11
    elif rpm_f <= 3000.0:
        timer = 330.0 - (rpm_f - 2800.0) * 1.2
    else:
        timer = 90.0 - (rpm_f - 3000.0) * 0.45
    return max(0, min(0xFFFF, int(timer)))


def build_aio_param(
    pump_rpm: int,
    *,
    theme_index: int = 0,
    theme_index_max: int = 31,
    brightness: int = 80,
    rotation: int = 0,
) -> bytes:
    """Build the 32-byte AIO parameter block.

    Most sensor/LCD fields are left disabled because macOS sensor integration is
    intentionally out of scope for this first daemon. Pump RPM, brightness,
    rotation and theme are active.
    """
    p = bytearray(AIO_PARAM_LEN)
    p[0] = 0  # cpu_temp
    p[1] = 0  # cpu_load
    p[2] = 0  # gpu_temp
    p[3] = 0  # gpu_load
    p[6] = 30  # LCD refresh loop interval used by upstream defaults
    p[7] = 1
    p[8] = 0  # cpu_temp_enabled
    p[9] = 0  # cpu_load_enabled
    p[10] = 0  # gpu_temp_enabled
    p[11] = 0  # gpu_load_enabled
    # ARGB colors: white text/value/unit.
    p[13] = 0xFF
    p[14] = 0xFF
    p[15] = 0xFF
    p[16] = 0xFF
    p[17] = 0xFF
    p[18] = 0xFF
    p[19] = 0xFF
    p[20] = 0xFF
    p[21] = 0xFF
    p[22] = 0xFF
    p[23] = 0xFF
    p[24] = 0xFF
    p[25] = clamp_int(brightness, 0, 100, "brightness")
    p[26] = 1
    max_theme = clamp_int(theme_index_max, 0, 255, "theme_index_max")
    p[27] = clamp_int(theme_index, 0, max_theme, "theme_index")
    timer = square_pump_timer(pump_rpm)
    p[28] = (timer >> 8) & 0xFF
    p[29] = timer & 0xFF
    p[30] = clamp_int(rotation, 0, 3, "rotation")
    return bytes(p)


def resolve_pump_rpm(coolant_c: float, cfg: dict[str, Any]) -> int:
    pump_cfg = cfg["pump"]
    mode = str(pump_cfg.get("mode", "curve")).lower()
    if mode == "constant":
        rpm = pump_cfg.get("rpm", DEFAULT_CONFIG["pump"]["rpm"])
    elif mode == "curve":
        rpm = interpolate_value(coolant_c, pump_cfg["curve"])
    else:
        raise ValueError('pump.mode must be "curve" or "constant"')
    return clamp_int(rpm, PUMP_MIN_RPM, PUMP_MAX_RPM_WATERBLOCK2, "pump rpm")


# ---------- Fan curve / config ----------


def interpolate_pwm(coolant_c: float, curve: list[tuple[float, int]]) -> int:
    return interpolate_value(float(coolant_c), curve)


def apply_min_pwm(pwm: int, min_pwm: int) -> int:
    pwm = clamp_int(pwm, 0, 255, "pwm")
    min_pwm = clamp_int(min_pwm, 0, 255, "min_pwm")
    if 0 < pwm < min_pwm:
        return min_pwm
    return pwm


def load_config(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path is not None and Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("config root must be a JSON object")
        cfg = deep_merge(cfg, user)

    cfg["fan_curve"] = normalize_curve(cfg["fan_curve"], name="fan_curve", y_low=0, y_high=255)
    pump_cfg = cfg.get("pump")
    if not isinstance(pump_cfg, dict):
        raise ValueError("pump must be an object")
    pump_cfg["mode"] = str(pump_cfg.get("mode", "curve")).lower()
    pump_cfg["rpm"] = clamp_int(pump_cfg.get("rpm", 2000), PUMP_MIN_RPM, PUMP_MAX_RPM_WATERBLOCK2, "pump.rpm")
    pump_cfg["curve"] = normalize_curve(
        pump_cfg.get("curve", DEFAULT_CONFIG["pump"]["curve"]),
        name="pump.curve",
        y_low=PUMP_MIN_RPM,
        y_high=PUMP_MAX_RPM_WATERBLOCK2,
    )

    cfg["min_pwm"] = clamp_int(cfg.get("min_pwm", 26), 0, 255, "min_pwm")
    cfg["pwm_hysteresis"] = clamp_int(cfg.get("pwm_hysteresis", 4), 0, 50, "pwm_hysteresis")
    cfg["rpm_hysteresis"] = clamp_int(cfg.get("rpm_hysteresis", 75), 0, 500, "rpm_hysteresis")
    cfg["pwm_ramp_up_per_tick"] = clamp_int(cfg.get("pwm_ramp_up_per_tick", 4), 1, 255, "pwm_ramp_up_per_tick")
    cfg["pwm_ramp_down_per_tick"] = clamp_int(cfg.get("pwm_ramp_down_per_tick", 3), 1, 255, "pwm_ramp_down_per_tick")
    cfg["rpm_ramp_up_per_tick"] = clamp_int(cfg.get("rpm_ramp_up_per_tick", 75), 1, 2000, "rpm_ramp_up_per_tick")
    cfg["rpm_ramp_down_per_tick"] = clamp_int(cfg.get("rpm_ramp_down_per_tick", 50), 1, 2000, "rpm_ramp_down_per_tick")
    cfg["coolant_min_valid_c"] = clamp_float(cfg.get("coolant_min_valid_c", 15.0), -20.0, 80.0, "coolant_min_valid_c")
    cfg["coolant_max_valid_c"] = clamp_float(cfg.get("coolant_max_valid_c", 70.0), cfg["coolant_min_valid_c"], 100.0, "coolant_max_valid_c")
    cfg["coolant_smoothing_alpha"] = clamp_float(cfg.get("coolant_smoothing_alpha", 0.45), 0.05, 1.0, "coolant_smoothing_alpha")
    cfg["coolant_max_drop_per_tick_c"] = clamp_float(cfg.get("coolant_max_drop_per_tick_c", 0.75), 0.1, 10.0, "coolant_max_drop_per_tick_c")
    cfg["coolant_max_rise_per_tick_c"] = clamp_float(cfg.get("coolant_max_rise_per_tick_c", 2.0), 0.1, 10.0, "coolant_max_rise_per_tick_c")
    cfg["coolant_reject_drop_c"] = clamp_float(cfg.get("coolant_reject_drop_c", 4.0), 1.0, 20.0, "coolant_reject_drop_c")
    cfg["coolant_reject_drop_extra_c_per_s"] = clamp_float(cfg.get("coolant_reject_drop_extra_c_per_s", 0.04), 0.0, 0.5, "coolant_reject_drop_extra_c_per_s")
    cfg["keepalive_interval_s"] = clamp_float(cfg.get("keepalive_interval_s", 1.0), 0.2, 10.0, "keepalive_interval_s")
    cfg["log_interval_s"] = clamp_float(cfg.get("log_interval_s", 10.0), 1.0, 3600.0, "log_interval_s")
    cfg["telemetry_soft_stale_s"] = clamp_float(cfg.get("telemetry_soft_stale_s", cfg.get("telemetry_max_stale_s", 30.0)), 1.0, 300.0, "telemetry_soft_stale_s")
    cfg["telemetry_hard_stale_s"] = clamp_float(cfg.get("telemetry_hard_stale_s", 600.0), cfg["telemetry_soft_stale_s"], 900.0, "telemetry_hard_stale_s")
    cfg["telemetry_max_stale_s"] = cfg["telemetry_hard_stale_s"]
    cfg["stale_pwm"] = apply_min_pwm(cfg.get("stale_pwm", 60), cfg["min_pwm"])
    cfg["stale_pump_rpm"] = clamp_int(cfg.get("stale_pump_rpm", 1950), PUMP_MIN_RPM, PUMP_MAX_RPM_WATERBLOCK2, "stale_pump_rpm")
    cfg["failsafe_pwm"] = apply_min_pwm(cfg.get("failsafe_pwm", 160), cfg["min_pwm"])
    cfg["failsafe_pump_rpm"] = clamp_int(cfg.get("failsafe_pump_rpm", 2800), PUMP_MIN_RPM, PUMP_MAX_RPM_WATERBLOCK2, "failsafe_pump_rpm")
    cfg["theme_index_max"] = clamp_int(cfg.get("theme_index_max", 12), 0, 12, "theme_index_max")
    cfg["theme_index"] = clamp_int(cfg.get("theme_index", 0), 0, cfg["theme_index_max"], "theme_index")
    cfg["brightness"] = clamp_int(cfg.get("brightness", 80), 0, 100, "brightness")
    cfg["rotation"] = clamp_int(cfg.get("rotation", 0), 0, 3, "rotation")
    cfg["send_master_clock"] = bool(cfg.get("send_master_clock", False))
    return cfg


def write_default_config(path: str | os.PathLike[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
        f.write("\n")


def set_theme_in_config(path: str | os.PathLike[str], theme_index: int) -> int:
    """Update theme_index in the config file in place. Returns the clamped value written."""
    p = Path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config root must be a JSON object")
    else:
        data = copy.deepcopy(DEFAULT_CONFIG)
        p.parent.mkdir(parents=True, exist_ok=True)
    max_theme = clamp_int(data.get("theme_index_max", DEFAULT_CONFIG["theme_index_max"]), 0, 255, "theme_index_max")
    clamped = clamp_int(theme_index, 0, max_theme, "theme_index")
    data["theme_index"] = clamped
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return clamped


def curve_preview(cfg: dict[str, Any]) -> str:
    temps = [28, 30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50]
    parts = []
    for temp in temps:
        pwm = apply_min_pwm(interpolate_pwm(temp, cfg["fan_curve"]), cfg["min_pwm"])
        pump = resolve_pump_rpm(temp, cfg)
        parts.append(f"{temp}C={pwm}/255 fan, {pump}rpm pump")
    return "; ".join(parts)


def coolant_rejection_reason(
    raw_c: float,
    previous_c: float | None,
    cfg: dict[str, Any],
    stale_age_s: float = 0.0,
) -> str | None:
    raw = float(raw_c)
    if raw < cfg["coolant_min_valid_c"] or raw > cfg["coolant_max_valid_c"]:
        return "outside valid range"
    if previous_c is not None:
        allowed_drop = cfg["coolant_reject_drop_c"] + max(0.0, stale_age_s) * cfg["coolant_reject_drop_extra_c_per_s"]
        if raw < float(previous_c) - allowed_drop:
            return "sudden drop"
    return None


def filter_coolant_reading(raw_c: float, previous_c: float | None, cfg: dict[str, Any]) -> float:
    """Smooth coolant telemetry before feeding curves.

    Coolant changes slowly. Reject clearly implausible readings before calling
    this. Then allow upward movement faster than downward movement so the
    controller remains protective under load.
    """
    raw = float(raw_c)
    if previous_c is None:
        return raw

    previous = float(previous_c)
    if raw < previous - cfg["coolant_max_drop_per_tick_c"]:
        limited = previous - cfg["coolant_max_drop_per_tick_c"]
    elif raw > previous + cfg["coolant_max_rise_per_tick_c"]:
        limited = previous + cfg["coolant_max_rise_per_tick_c"]
    else:
        limited = raw

    alpha = cfg["coolant_smoothing_alpha"]
    return previous + alpha * (limited - previous)


def slew_limit(current: int, target: int, *, up_step: int, down_step: int) -> int:
    """Limit target changes to avoid audible step changes."""
    if target > current:
        return min(target, current + up_step)
    if target < current:
        return max(target, current - down_step)
    return target


def stale_targets(
    last_coolant: float | None,
    last_target_pwm: int | None,
    last_pump_rpm: int | None,
    cfg: dict[str, Any],
) -> tuple[int, int]:
    """Moderate stale-telemetry targets that avoid full-blast spikes.

    Missing telemetry during heavy CPU load is common on this Hackintosh/PyUSB
    path. Keep airflow/flow conservative but not alarming while waiting for RX
    to recover. Hard failsafe remains separate for very long telemetry loss.
    """
    if last_coolant is not None:
        curve_pwm = apply_min_pwm(interpolate_pwm(float(last_coolant), cfg["fan_curve"]), cfg["min_pwm"])
        curve_pump = resolve_pump_rpm(float(last_coolant), cfg)
    else:
        curve_pwm = cfg["stale_pwm"]
        curve_pump = cfg["stale_pump_rpm"]
    pwm = max(last_target_pwm or 0, curve_pwm, cfg["stale_pwm"])
    pump = max(last_pump_rpm or 0, curve_pump, cfg["stale_pump_rpm"])
    return min(255, pwm), min(PUMP_MAX_RPM_WATERBLOCK2, pump)


# ---------- Signals / daemon loop ----------


def handle_sig(signum, frame) -> None:  # noqa: ANN001 - signal API shape
    global running
    running = False
    LOG.info("shutdown signal received")


def handle_sighup(signum, frame) -> None:  # noqa: ANN001 - signal API shape
    global reload_requested
    reload_requested = True
    LOG.info("reload signal received")


def setup_signals() -> None:
    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, handle_sighup)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def send_control_packets(
    *,
    tx,
    master_mac: bytes,
    master_ch: int,
    device_mac: bytes,
    device_channel: int,
    rx_type: int,
    seq_index: int,
    target_pwm: int,
    pump_rpm: int,
    cfg: dict[str, Any],
) -> None:
    if cfg["send_master_clock"]:
        send_master_clock(tx, master_mac, master_ch)

    aio_param = build_aio_param(
        pump_rpm=pump_rpm,
        theme_index=cfg["theme_index"],
        theme_index_max=cfg["theme_index_max"],
        brightness=cfg["brightness"],
        rotation=cfg["rotation"],
    )
    rf = cmd_aio_params(master_mac, master_ch, device_mac, rx_type, aio_param, seq_index=seq_index)
    send_rf_frame(tx, rf, device_channel, rx_type)

    # AIO fan slots are 0..2; slot 3 is the pump tach slot and is ignored for fan PWM.
    pwm_values = [target_pwm, target_pwm, target_pwm, 0]
    rf = cmd_pwm(master_mac, master_ch, device_mac, rx_type, pwm_values, seq_index=seq_index)
    send_rf_frame(tx, rf, device_channel, rx_type)


def run_theme_scan(
    *,
    tx,
    master_mac: bytes,
    master_ch: int,
    device_mac: bytes,
    device_channel: int,
    rx_type: int,
    seq_index: int,
    cfg: dict[str, Any],
    start: int,
    end: int,
    dwell_s: float,
) -> None:
    LOG.info("theme scan: %s..%s, %.1fs per theme", start, end, dwell_s)
    step = 1 if end >= start else -1
    scan_theme_max = clamp_int(max(start, end, cfg["theme_index_max"]), 0, 255, "scan_theme_max")
    target_pwm = apply_min_pwm(interpolate_pwm(35.0, cfg["fan_curve"]), cfg["min_pwm"])
    pump_rpm = resolve_pump_rpm(35.0, cfg)
    switch_rf = cmd_switch_wireless_theme(master_mac, master_ch, device_mac, rx_type)
    # Use a local rolling seq so the device doesn't ignore packets where seq
    # hasn't changed — the original prototype used (seq % 255) + 1 each cycle.
    scan_seq = seq_index
    for theme in range(start, end + step, step):
        if not running:
            break
        scan_cfg = dict(cfg)
        scan_cfg["theme_index_max"] = scan_theme_max
        scan_cfg["theme_index"] = clamp_int(theme, 0, scan_theme_max, "theme")
        LOG.info("theme_index=%s", scan_cfg["theme_index"])
        # Re-send the wireless switch command before each theme so the device
        # picks up the new theme_index from the subsequent aio_params packet.
        for _ in range(3):
            send_rf_frame(tx, switch_rf, device_channel, rx_type)
            time.sleep(0.002)
        # Keep sending control packets throughout the dwell period so the device
        # has multiple chances to apply the theme, matching the main loop cadence.
        deadline = time.time() + dwell_s
        while time.time() < deadline and running:
            send_control_packets(
                tx=tx,
                master_mac=master_mac,
                master_ch=master_ch,
                device_mac=device_mac,
                device_channel=device_channel,
                rx_type=rx_type,
                seq_index=scan_seq,
                target_pwm=target_pwm,
                pump_rpm=pump_rpm,
                cfg=scan_cfg,
            )
            scan_seq = (scan_seq % 255) + 1
            time.sleep(1.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Control a Lian Li HydroShift II wireless AIO")
    default_cfg = os.path.expanduser("~/.config/lianli-hydroshift/config.json")
    parser.add_argument("--config", default=default_cfg)
    parser.add_argument("--write-default-config", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--scan-themes", nargs=2, type=int, metavar=("START", "END"), help="send theme indexes in sequence for discovery")
    parser.add_argument("--theme-dwell-s", type=float, default=3.0)
    parser.add_argument("--set-theme", type=int, metavar="N", help="update theme_index in config and exit")
    args = parser.parse_args(argv)

    setup_logging(args.log_level)
    setup_signals()

    if args.write_default_config:
        write_default_config(args.config)
        LOG.info("wrote default config to %s", args.config)
        return 0

    if args.set_theme is not None:
        try:
            written = set_theme_in_config(args.config, args.set_theme)
            LOG.info("set theme_index=%s in %s", written, args.config)
        except Exception as exc:
            LOG.error("failed to set theme: %s", exc)
            return 1
        return 0

    try:
        require_usb()
        cfg = load_config(args.config)
    except Exception as exc:
        LOG.error("startup failed: %s", exc)
        return 1

    LOG.info("lianli-hydroshift daemon starting")
    LOG.info("config: %s", args.config)
    LOG.info("curve preview: %s", curve_preview(cfg))
    LOG.info("theme=%s brightness=%s rotation=%s", cfg["theme_index"], cfg["brightness"], cfg["rotation"])

    tx = rx = None
    try:
        tx = find_dongle(TX_IDS)
        rx = find_dongle(RX_IDS)
        if tx is None or rx is None:
            LOG.error("Lian Li wireless dongles not found (tx=%s rx=%s)", bool(tx), bool(rx))
            return 1
        claim(tx)
        claim(rx)

        master_mac, master_ch = discover_master(tx)
        if master_mac is None or master_ch is None:
            LOG.error("TX dongle did not respond to GET_MAC scan")
            return 1
        LOG.info("master: %s ch=%s", master_mac.hex(":"), master_ch)

        frames = collect_rx_frames(rx, count=5, max_wait=5.0)
        rec = find_aio(frames, master_mac=master_mac)
        if rec is None:
            LOG.error("no bound HydroShift/WaterBlock AIO device found")
            return 1

        device_mac = rec["mac"]
        device_channel = rec["channel"]
        rx_type = rec["rx_type"]
        seq_index = rec.get("seq_index", 1)
        last_raw_coolant = float(rec["coolant_temp"]) if rec["coolant_temp"] is not None else None
        last_coolant = last_raw_coolant
        last_good_telemetry = time.time() if last_coolant is not None else 0.0
        LOG.info(
            "device: %s ch=%s rx=%s seq=%s coolant=%sC rpm=%s pwm=%s",
            device_mac.hex(":"),
            device_channel,
            rx_type,
            seq_index,
            last_coolant,
            rec["fan_rpms"],
            rec["current_pwm"],
        )

        LOG.info("engaging wireless theme mode")
        switch_rf = cmd_switch_wireless_theme(master_mac, master_ch, device_mac, rx_type)
        for _ in range(10):
            send_rf_frame(tx, switch_rf, device_channel, rx_type)
            time.sleep(0.002)
        LOG.info("wireless theme mode engaged")

        if args.scan_themes:
            start, end = args.scan_themes
            run_theme_scan(
                tx=tx,
                master_mac=master_mac,
                master_ch=master_ch,
                device_mac=device_mac,
                device_channel=device_channel,
                rx_type=rx_type,
                seq_index=seq_index,
                cfg=cfg,
                start=start,
                end=end,
                dwell_s=max(0.5, args.theme_dwell_s),
            )
            return 0

        LOG.info("entering control loop")
        last_log = 0.0
        last_target_pwm: int | None = None
        last_pump_rpm: int | None = None
        last_tel: dict[str, Any] | None = rec

        global reload_requested
        while running:
            loop_start = time.time()

            if reload_requested:
                try:
                    cfg = load_config(args.config)
                    LOG.info("config reloaded: %s", curve_preview(cfg))
                except Exception as exc:
                    LOG.warning("config reload failed, keeping previous config: %s", exc)
                reload_requested = False

            frames = collect_rx_frames(rx, count=2, max_wait=1.5)
            tel = find_aio(frames, master_mac=master_mac)
            if tel is not None:
                last_tel = tel
                device_channel = tel["channel"]
                rx_type = tel["rx_type"]
                seq_index = tel.get("seq_index", seq_index)
                if tel["coolant_temp"] is not None:
                    raw_coolant = float(tel["coolant_temp"])
                    current_stale_age = time.time() - last_good_telemetry if last_good_telemetry else 0.0
                    rejection = coolant_rejection_reason(raw_coolant, last_coolant, cfg, current_stale_age)
                    if rejection is None:
                        last_raw_coolant = raw_coolant
                        last_coolant = filter_coolant_reading(raw_coolant, last_coolant, cfg)
                        last_good_telemetry = time.time()
                    else:
                        LOG.warning("discarding implausible coolant telemetry: %.1fC (%s)", raw_coolant, rejection)
            else:
                LOG.warning("no fresh telemetry; using previous state")

            stale_age = time.time() - last_good_telemetry if last_good_telemetry else float("inf")
            if last_coolant is None or stale_age > cfg["telemetry_hard_stale_s"]:
                telemetry_state = "hard_stale"
            elif stale_age > cfg["telemetry_soft_stale_s"]:
                telemetry_state = "soft_stale"
            else:
                telemetry_state = "ok"
            failsafe = telemetry_state == "hard_stale"

            if telemetry_state == "hard_stale":
                target_pwm = max(last_target_pwm or 0, cfg["failsafe_pwm"])
                pump_rpm = max(last_pump_rpm or 0, cfg["failsafe_pump_rpm"])
            elif telemetry_state == "soft_stale":
                target_pwm, pump_rpm = stale_targets(last_coolant, last_target_pwm, last_pump_rpm, cfg)
            else:
                desired_pwm = apply_min_pwm(interpolate_pwm(float(last_coolant), cfg["fan_curve"]), cfg["min_pwm"])
                if last_target_pwm is not None and abs(desired_pwm - last_target_pwm) < cfg["pwm_hysteresis"]:
                    target_pwm = last_target_pwm
                else:
                    target_pwm = desired_pwm

                desired_pump = resolve_pump_rpm(float(last_coolant), cfg)
                if last_pump_rpm is not None and abs(desired_pump - last_pump_rpm) < cfg["rpm_hysteresis"]:
                    pump_rpm = last_pump_rpm
                else:
                    pump_rpm = desired_pump

            if last_target_pwm is not None:
                target_pwm = slew_limit(
                    last_target_pwm,
                    target_pwm,
                    up_step=cfg["pwm_ramp_up_per_tick"],
                    down_step=cfg["pwm_ramp_down_per_tick"],
                )
            if last_pump_rpm is not None:
                pump_rpm = slew_limit(
                    last_pump_rpm,
                    pump_rpm,
                    up_step=cfg["rpm_ramp_up_per_tick"],
                    down_step=cfg["rpm_ramp_down_per_tick"],
                )

            try:
                send_control_packets(
                    tx=tx,
                    master_mac=master_mac,
                    master_ch=master_ch,
                    device_mac=device_mac,
                    device_channel=device_channel,
                    rx_type=rx_type,
                    seq_index=seq_index,
                    target_pwm=target_pwm,
                    pump_rpm=pump_rpm,
                    cfg=cfg,
                )
                last_target_pwm = target_pwm
                last_pump_rpm = pump_rpm
            except Exception as exc:
                LOG.warning("sending control packets failed: %s", exc)

            if time.time() - last_log >= cfg["log_interval_s"]:
                rpm = last_tel["fan_rpms"] if last_tel else [0, 0, 0, 0]
                LOG.info(
                    "coolant=%.1fC raw=%sC stale=%.1fs telemetry=%s failsafe=%s fan_pwm=%s/%s pump_target=%srpm rpm=%s",
                    last_coolant if last_coolant is not None else -1.0,
                    f"{last_raw_coolant:.1f}" if last_raw_coolant is not None else "n/a",
                    stale_age,
                    telemetry_state,
                    failsafe,
                    target_pwm,
                    255,
                    pump_rpm,
                    rpm,
                )
                last_log = time.time()

            elapsed = time.time() - loop_start
            sleep_for = cfg["keepalive_interval_s"] - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    finally:
        release(tx, "TX")
        release(rx, "RX")
        LOG.info("goodbye")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
