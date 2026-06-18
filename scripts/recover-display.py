#!/usr/bin/env python3
"""Send repeated wireless-switch commands to recover a corrupted AIO display state.

Uses TX-only mode: skips RX collection entirely to avoid the RX I/O error
corrupting the shared libusb context. Falls back to last-known device details
from daemon logs if RX discovery is unavailable.
"""

import argparse
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from lianli_hydroshift.daemon import (
    TX_IDS, RX_IDS,
    AIO_DEVICE_TYPES,
    claim, release, get_endpoints, discover_master, collect_rx_frames, find_aio,
    parse_frame_records,
    cmd_switch_wireless_theme, cmd_bind_aio, cmd_save_config, send_rf_frame,
    USB_ERROR,
)

# Last-known device details from daemon logs — used when RX is unreadable.
KNOWN_DEVICE_MAC = bytes.fromhex("2da374e566e1")
KNOWN_DEVICE_CHANNEL = 8
KNOWN_RX_TYPE = 1

REPEATS = 200
REPEAT_DELAY = 0.02


def send_switch(tx, switch_rf, channel, rx_type, repeats: int = REPEATS, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[dry-run] would send {repeats}x wireless switch frames "
              f"(ch={channel} rx={rx_type})")
        return
    for i in range(repeats):
        send_rf_frame(tx, switch_rf, channel, rx_type)
        time.sleep(REPEAT_DELAY)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{repeats}", flush=True)


def try_reset_rx(rx, dry_run: bool = False) -> None:
    """Send CMD_RESET to the RX dongle to clear its wireless state."""
    if dry_run:
        print("[dry-run] would send RX reset command")
        return
    try:
        out_ep, _ = get_endpoints(rx)
        cmd = bytearray(64)
        cmd[0] = 0x11  # USB_CMD_GET_MAC envelope
        cmd[1] = 0x08  # reset sub-command (from lian-li-linux CMD_RESET)
        rx.write(out_ep, bytes(cmd), 1000)
        time.sleep(0.1)
        print("RX reset command sent.")
    except Exception as exc:
        print(f"RX reset failed ({exc}); continuing anyway")


def find_any_aio(frames):
    for frame in frames:
        for rec in parse_frame_records(frame):
            if rec["device_type"] in AIO_DEVICE_TYPES:
                return rec
    return None


def try_rx_discovery(rx, master_mac):
    """Attempt RX device discovery. Returns bound or unbound AIO record, or None."""
    try:
        frames = collect_rx_frames(rx, count=5, max_wait=5.0)
        bound = find_aio(frames, master_mac=master_mac)
        if bound is not None:
            return bound
        aio = find_any_aio(frames)
        if aio is not None:
            print(
                "AIO is visible but not bound to this master "
                f"(master={aio['master_mac'].hex(':')} rx={aio['rx_type']}); will re-bind"
            )
            return aio
        return None
    except Exception as exc:
        print(f"RX collection failed ({exc}); using last-known device details")
        return None


def send_bind_packet(tx, rec, master_mac, master_ch, target_rx: int) -> None:
    rf = cmd_bind_aio(master_mac, master_ch, rec["mac"], rec["current_pwm"], target_rx)
    for _ in range(6):
        send_rf_frame(tx, rf, rec["channel"], rec["rx_type"])
        time.sleep(0.03)


def save_rf_config(tx, master_mac, master_ch) -> None:
    rf = cmd_save_config(master_mac)
    for _ in range(3):
        send_rf_frame(tx, rf, master_ch, 0xFF)
        time.sleep(0.2)


def bind_if_needed(tx, rx, rec, master_mac, master_ch, dry_run: bool = False):
    if rec["master_mac"] == master_mac and rec["rx_type"] not in (0, 0xFE, 0xFF):
        return rec

    target_rx = 1
    if dry_run:
        print(
            f"[dry-run] AIO needs binding: current master={rec['master_mac'].hex(':')} "
            f"rx={rec['rx_type']} -> would bind to master={master_mac.hex(':')} "
            f"rx={target_rx}, then broadcast SaveConfig"
        )
        return rec

    print(f"Binding AIO to master {master_mac.hex(':')} rx={target_rx}...")
    deadline = time.time() + 8.0
    attempt = 0
    latest = rec
    while time.time() < deadline:
        attempt += 1
        send_bind_packet(tx, latest, master_mac, master_ch, target_rx)
        time.sleep(0.2)
        frames = collect_rx_frames(rx, count=5, max_wait=4.0)
        for frame in frames:
            for candidate in parse_frame_records(frame):
                if candidate["mac"] == rec["mac"]:
                    latest = candidate
        print(
            f"  attempt {attempt}: master={latest['master_mac'].hex(':')} "
            f"rx={latest['rx_type']}"
        )
        if latest["master_mac"] == master_mac and latest["rx_type"] == target_rx:
            save_rf_config(tx, master_mac, master_ch)
            print("Bind converged and RF config saved.")
            return latest

    print("Bind did not converge before timeout; continuing with latest observed details")
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="diagnose discovery/bind state and print the intended actions "
        "without sending any RF writes",
    )
    args = parser.parse_args()
    dry_run = args.dry_run
    if dry_run:
        print("=== DRY RUN: no RF writes will be sent ===")

    try:
        import usb.core as usb_core
    except ImportError:
        print("ERROR: pyusb not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 1

    # --- TX only first ---
    tx = None
    try:
        for vid, pid in TX_IDS:
            tx = usb_core.find(idVendor=vid, idProduct=pid)
            if tx:
                break
        if tx is None:
            print("ERROR: TX dongle not found", file=sys.stderr)
            return 1

        claim(tx)
        master_mac, master_ch = discover_master(tx)
        if master_mac is None:
            print("ERROR: TX dongle did not respond to GET_MAC scan", file=sys.stderr)
            return 1
        print(f"master: {master_mac.hex(':')}  ch={master_ch}")

        # --- Try RX discovery and repair an unbound AIO if one is visible. ---
        rx = None
        rec = None
        try:
            for vid, pid in RX_IDS:
                rx = usb_core.find(idVendor=vid, idProduct=pid)
                if rx:
                    break
            if rx:
                claim(rx)
                try_reset_rx(rx, dry_run=dry_run)
                rec = try_rx_discovery(rx, master_mac)
                if rec is not None:
                    rec = bind_if_needed(tx, rx, rec, master_mac, master_ch, dry_run=dry_run)
        except Exception as exc:
            print(f"RX setup failed ({exc}); using last-known device details")
        finally:
            # Release RX before the TX-only wireless-switch burst.
            release(rx, "RX")
            rx = None

        if rec is not None:
            device_mac = rec["mac"]
            device_ch = rec["channel"]
            rx_type = rec["rx_type"]
            print(f"device (discovered): {device_mac.hex(':')}  ch={device_ch}  rx={rx_type}")
        else:
            device_mac = KNOWN_DEVICE_MAC
            device_ch = KNOWN_DEVICE_CHANNEL
            rx_type = KNOWN_RX_TYPE
            print(f"device (last-known): {device_mac.hex(':')}  ch={device_ch}  rx={rx_type}")

        switch_rf = cmd_switch_wireless_theme(master_mac, master_ch, device_mac, rx_type)
        if not dry_run:
            print(f"Sending {REPEATS}x wireless switch commands...", flush=True)
        send_switch(tx, switch_rf, device_ch, rx_type, dry_run=dry_run)

        if dry_run:
            print("\nDry run complete. Re-run without --dry-run to apply.")
            return 0

        print("\nDone. Restart the daemon to resume normal control:")
        print("  sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift")
        return 0

    finally:
        release(tx, "TX")


if __name__ == "__main__":
    raise SystemExit(main())
