#!/usr/bin/env python3
"""
lianli-macos-daemon v2 — fan control for Lian Li HydroShift II on macOS

Sends both AIO params AND fan PWM commands each cycle, matching the Linux
daemon's behaviour. This stops the AIO falling back to default mode every
other cycle.

USAGE:
    sudo ~/lianli-env/bin/python3 lianli_daemon_v2.py [--config PATH]
"""

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

try:
    import usb.core
    import usb.util
except ImportError:
    print("ERROR: pyusb not installed in this Python environment.")
    sys.exit(1)


# ---------- Hardware constants ----------

TX_IDS = [(0x0416, 0x8040), (0x1A86, 0xE304)]
RX_IDS = [(0x0416, 0x8041), (0x1A86, 0xE305)]

USB_CMD_SEND_RF = 0x10
USB_CMD_GET_MAC = 0x11
USB_TIMEOUT     = 1000

RF_SELECT                = 0x12
RF_PWM_CMD               = 0x10
RF_AIO_SWITCH_WIRELESS   = 0x19
RF_AIO_PARAMS            = 0x21
RF_DATA_SIZE             = 240
RF_CHUNK_SIZE            = 60
RF_CHUNKS                = 4
AIO_PARAM_LEN            = 32

DEVICE_TYPE_WATERBLOCK2  = 11

MIN_PWM = 26   # 10% min duty for WaterBlock2

KEEPALIVE_INTERVAL_S = 1.0
TEMP_HYSTERESIS_C    = 1.0
PWM_HYSTERESIS       = 5


# ---------- Default fan curve ----------
DEFAULT_FAN_CURVE = [
    (28, 40),
    (32, 60),
    (36, 90),
    (40, 130),
    (44, 180),
    (48, 230),
    (52, 255),
]

# Pump RPM target (constant for now). HS2 range: 1600-3200.
# 2000 is a good balance of cooling and quiet pump.
DEFAULT_PUMP_RPM = 2000


# ---------- USB helpers (unchanged) ----------

def find_dongle(id_list):
    for vid, pid in id_list:
        dev = usb.core.find(idVendor=vid, idProduct=pid)
        if dev is not None:
            return dev
    return None


def claim(dev):
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    usb.util.claim_interface(dev, 0)


def get_endpoints(dev):
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    out_ep = in_ep = None
    for ep in intf:
        if ep.bEndpointAddress & 0x80:
            in_ep = ep.bEndpointAddress
        else:
            out_ep = ep.bEndpointAddress
    return out_ep, in_ep


# ---------- Discovery (unchanged) ----------

def discover_master(tx):
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
        except usb.core.USBTimeoutError:
            continue
        if len(resp) >= 7 and resp[0] == USB_CMD_GET_MAC:
            mac = resp[1:7]
            if any(b != 0 for b in mac):
                return bytes(mac), ch
    return None, None


def collect_rx_frames(rx, count=3, max_wait=3.0):
    out_ep, in_ep = get_endpoints(rx)
    wake = bytearray(64)
    wake[0] = 0x10
    wake[1] = 0x01
    frames = []
    deadline = time.time() + max_wait
    while len(frames) < count and time.time() < deadline:
        try:
            rx.write(out_ep, bytes(wake), 500)
        except Exception:
            pass
        try:
            data = bytes(rx.read(in_ep, 512, 1000))
            if len(data) >= 100:
                frames.append(data)
        except usb.core.USBTimeoutError:
            continue
    return frames


def parse_record(data):
    if len(data) < 42 or data[41] != 0x1C or data[18] == 0xFF:
        return None
    mac = bytes(data[0:6])
    if mac == b"\x00" * 6 or mac == b"\xff" * 6:
        return None
    return {
        "mac": mac,
        "channel": data[12],
        "rx_type": data[13],
        "device_type": data[18],
        "fan_count": min(data[19], 4),
        "fan_rpms": [
            int.from_bytes(data[28:30], "big"),
            int.from_bytes(data[30:32], "big"),
            int.from_bytes(data[32:34], "big"),
            int.from_bytes(data[34:36], "big"),
        ],
        "current_pwm": list(data[36:40]),
        "coolant_temp": data[27] if data[18] in (10, 11) else None,
    }


def find_waterblock2(frames):
    for f in frames:
        for start in range(len(f) - 42):
            r = parse_record(f[start:start + 42])
            if r and r["device_type"] == DEVICE_TYPE_WATERBLOCK2:
                return r
    return None


# ---------- RF commands ----------

def send_rf_frame(tx, rf_data, device_channel, rx_type):
    out_ep, _ = get_endpoints(tx)
    for chunk_idx in range(RF_CHUNKS):
        packet = bytearray(64)
        packet[0] = USB_CMD_SEND_RF
        packet[1] = chunk_idx
        packet[2] = device_channel
        packet[3] = rx_type
        s = chunk_idx * RF_CHUNK_SIZE
        e = s + RF_CHUNK_SIZE
        packet[4:64] = rf_data[s:e]
        tx.write(out_ep, bytes(packet), USB_TIMEOUT)
        time.sleep(0.001)


def cmd_switch_wireless_theme(master_mac, master_ch, device_mac, rx_type):
    rf = bytearray(RF_DATA_SIZE)
    rf[0]    = RF_SELECT
    rf[1]    = RF_AIO_SWITCH_WIRELESS
    rf[2:8]  = device_mac
    rf[8:14] = master_mac
    rf[14]   = rx_type
    rf[15]   = master_ch
    return bytes(rf)


def cmd_pwm(master_mac, master_ch, device_mac, rx_type, pwm_values, seq_index=1):
    rf = bytearray(RF_DATA_SIZE)
    rf[0]     = RF_SELECT
    rf[1]     = RF_PWM_CMD
    rf[2:8]   = device_mac
    rf[8:14]  = master_mac
    rf[14]    = rx_type
    rf[15]    = master_ch
    rf[16]    = seq_index
    rf[17:21] = bytes(pwm_values)
    return bytes(rf)


def square_pump_timer(rpm):
    """Map target pump RPM → firmware timer for WaterBlock2 (HS2 Square)."""
    rpm = max(1600, min(3200, rpm))
    rpm = float(rpm)
    if rpm <= 1800:
        t = 1590.0 - (rpm - 1600) * 0.95
    elif rpm <= 2000:
        t = 1400.0 - (rpm - 1800)
    elif rpm <= 2200:
        t = 1200.0 - (rpm - 2000)
    elif rpm <= 2400:
        t = 1000.0 - (rpm - 2200)
    elif rpm <= 2600:
        t = 800.0 - (rpm - 2400)
    elif rpm <= 2800:
        t = 580.0 - (rpm - 2600) * 1.11
    elif rpm <= 3000:
        t = 330.0 - (rpm - 2800) * 1.2
    else:
        t = 90.0 - (rpm - 3000) * 0.45
    return max(0, min(0xFFFF, int(t)))


def build_aio_param(pump_rpm, theme_index=0, brightness=80, rotation=0):
    """
    Build the 32-byte AIO parameter block. Layout from
    crates/lianli-daemon/src/aio_controller.rs::build_aio_param.

    Most fields are sensor values we don't have on macOS — leave them
    as zero with the "enabled" flags off. Pump RPM and theme/brightness
    matter; everything else is cosmetic for the LCD screen.
    """
    p = bytearray(AIO_PARAM_LEN)
    p[0] = 0      # cpu_temp
    p[1] = 0      # cpu_load
    p[2] = 0      # gpu_temp
    p[3] = 0      # gpu_load
    p[6] = 30     # loop_interval (LCD refresh, default 30)
    p[7] = 1      # always 1
    p[8] = 0      # cpu_temp_enabled
    p[9] = 0      # cpu_load_enabled
    p[10] = 0     # gpu_temp_enabled
    p[11] = 0     # gpu_load_enabled
    # ARGB colours for screen text — sensible defaults
    # str_color (white, full alpha)
    p[13] = 0xff; p[14] = 0xff; p[15] = 0xff; p[16] = 0xff
    # val_color (white)
    p[17] = 0xff; p[18] = 0xff; p[19] = 0xff; p[20] = 0xff
    # unit_color (white)
    p[21] = 0xff; p[22] = 0xff; p[23] = 0xff; p[24] = 0xff
    p[25] = max(0, min(100, brightness))
    p[26] = 1
    p[27] = max(0, min(12, theme_index))
    timer = square_pump_timer(pump_rpm)
    p[28] = (timer >> 8) & 0xff
    p[29] = timer & 0xff
    p[30] = max(0, min(3, rotation))
    return bytes(p)


def cmd_aio_params(master_mac, master_ch, device_mac, rx_type,
                   aio_param, seq_index=1):
    rf = bytearray(RF_DATA_SIZE)
    rf[0]     = RF_SELECT
    rf[1]     = RF_AIO_PARAMS
    rf[2:8]   = device_mac
    rf[8:14]  = master_mac
    rf[14]    = rx_type
    rf[15]    = master_ch
    rf[16]    = seq_index
    # NOTE: aio_param starts at offset 18, not 17 (per Rust source line 56)
    rf[18:18 + AIO_PARAM_LEN] = aio_param
    return bytes(rf)


# ---------- Fan curve ----------

def interpolate_pwm(coolant_c, curve):
    pts = sorted(curve)
    if coolant_c <= pts[0][0]:
        return pts[0][1]
    if coolant_c >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        t1, p1 = pts[i]
        t2, p2 = pts[i + 1]
        if t1 <= coolant_c <= t2:
            frac = (coolant_c - t1) / (t2 - t1)
            return int(round(p1 + frac * (p2 - p1)))
    return pts[-1][1]


def apply_min_pwm(pwm):
    if 0 < pwm < MIN_PWM:
        return MIN_PWM
    return max(0, min(255, pwm))


# ---------- Config ----------

def load_config(path):
    default = {
        "fan_curve": DEFAULT_FAN_CURVE,
        "pump_rpm": DEFAULT_PUMP_RPM,
        "theme_index": 0,
        "brightness": 80,
        "rotation": 0,
        "log_interval_s": 10,
    }
    if path is None or not Path(path).exists():
        return default
    with open(path) as f:
        user = json.load(f)
    cfg = dict(default)
    cfg.update(user)
    cfg["fan_curve"] = [tuple(p) for p in cfg["fan_curve"]]
    return cfg


def write_default_config(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "fan_curve": [list(p) for p in DEFAULT_FAN_CURVE],
            "pump_rpm": DEFAULT_PUMP_RPM,
            "theme_index": 0,
            "brightness": 80,
            "rotation": 0,
            "log_interval_s": 10,
            "_comment": "fan_curve = [[coolant_c, pwm_0_to_255], ...]"
        }, f, indent=2)


# ---------- Main loop ----------

running = True

def handle_sig(signum, frame):
    global running
    running = False
    print("\nShutdown signal received...")

signal.signal(signal.SIGINT,  handle_sig)
signal.signal(signal.SIGTERM, handle_sig)


def main():
    parser = argparse.ArgumentParser()
    default_cfg = os.path.expanduser("~/.config/lianli/config.json")
    parser.add_argument("--config", default=default_cfg)
    parser.add_argument("--write-default-config", action="store_true")
    args = parser.parse_args()

    if args.write_default_config:
        write_default_config(args.config)
        print(f"Wrote default config to {args.config}")
        sys.exit(0)

    cfg = load_config(args.config)
    print(f"lianli-macos-daemon v2 starting")
    print(f"  Config:    {args.config}")
    print(f"  Fan curve: {cfg['fan_curve']}")
    print(f"  Pump RPM:  {cfg['pump_rpm']}")
    print(f"  Theme:     {cfg['theme_index']}, brightness={cfg['brightness']}\n")

    tx = find_dongle(TX_IDS)
    rx = find_dongle(RX_IDS)
    if tx is None or rx is None:
        print("ERROR: Lian Li wireless dongles not found.")
        sys.exit(1)
    claim(tx); claim(rx)

    master_mac, master_ch = discover_master(tx)
    if master_mac is None:
        print("ERROR: TX dongle did not respond to GET_MAC scan")
        sys.exit(1)
    print(f"  Master: {master_mac.hex(':')}  ch={master_ch}")

    frames = collect_rx_frames(rx, count=5, max_wait=5.0)
    rec = find_waterblock2(frames)
    if rec is None:
        print("ERROR: No WaterBlock2 device found")
        sys.exit(1)
    device_mac = rec["mac"]
    device_channel = rec["channel"]
    rx_type = rec["rx_type"]
    print(f"  Device: {device_mac.hex(':')}  ch={device_channel}  rx={rx_type}")
    print(f"  Coolant: {rec['coolant_temp']}°C\n")

    # 1. Engage wireless theme mode (once)
    print("  Engaging wireless theme mode (this should switch the screen)...")
    switch_rf = cmd_switch_wireless_theme(master_mac, master_ch, device_mac, rx_type)
    for _ in range(10):
        send_rf_frame(tx, switch_rf, device_channel, rx_type)
        time.sleep(0.002)
    print("  Done\n")

    print("Entering control loop (Ctrl+C to stop)...")

    last_log = 0
    last_target_pwm = -1
    last_coolant = None
    seq = 1

    while running:
        loop_start = time.time()

        # Telemetry
        frames = collect_rx_frames(rx, count=2, max_wait=1.5)
        tel = find_waterblock2(frames)
        if tel is None:
            print("  [warn] no telemetry")
            time.sleep(KEEPALIVE_INTERVAL_S)
            continue

        coolant = tel["coolant_temp"]

        # Curve with hysteresis
        if last_coolant is not None and abs(coolant - last_coolant) < TEMP_HYSTERESIS_C:
            target_pwm = last_target_pwm
        else:
            target_pwm = apply_min_pwm(interpolate_pwm(coolant, cfg["fan_curve"]))
            last_coolant = coolant
        if abs(target_pwm - last_target_pwm) < PWM_HYSTERESIS and last_target_pwm > 0:
            target_pwm = last_target_pwm

        # 2. Send AIO params (every cycle)
        try:
            aio_param = build_aio_param(
                pump_rpm=cfg["pump_rpm"],
                theme_index=cfg["theme_index"],
                brightness=cfg["brightness"],
                rotation=cfg["rotation"],
            )
            rf = cmd_aio_params(master_mac, master_ch, device_mac, rx_type,
                                aio_param, seq_index=seq)
            send_rf_frame(tx, rf, device_channel, rx_type)
        except Exception as e:
            print(f"  [warn] aio_params failed: {e}")

        # 3. Send fan PWM (every cycle)
        pwm_values = [target_pwm, target_pwm, target_pwm, 0]  # slot 3 ignored for AIO
        try:
            rf = cmd_pwm(master_mac, master_ch, device_mac, rx_type,
                         pwm_values, seq_index=seq)
            send_rf_frame(tx, rf, device_channel, rx_type)
        except Exception as e:
            print(f"  [warn] pwm failed: {e}")

        last_target_pwm = target_pwm
        seq = (seq % 255) + 1

        if time.time() - last_log >= cfg["log_interval_s"]:
            print(f"  coolant={coolant}°C  target_pwm={target_pwm} "
                  f"({target_pwm/255*100:.0f}%)  "
                  f"rpm={tel['fan_rpms'][:3]}  pump={tel['fan_rpms'][3]}")
            last_log = time.time()

        elapsed = time.time() - loop_start
        if elapsed < KEEPALIVE_INTERVAL_S:
            time.sleep(KEEPALIVE_INTERVAL_S - elapsed)

    usb.util.release_interface(tx, 0)
    usb.util.release_interface(rx, 0)
    print("Goodbye.")


if __name__ == "__main__":
    main()
