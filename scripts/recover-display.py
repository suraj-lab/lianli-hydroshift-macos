#!/usr/bin/env python3
"""Send repeated wireless-switch commands to recover a corrupted AIO display state.

Uses TX-only mode: skips RX collection entirely to avoid the RX I/O error
corrupting the shared libusb context. Falls back to last-known device details
from daemon logs if RX discovery is unavailable.
"""

import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from lianli_hydroshift.daemon import (
    TX_IDS, RX_IDS,
    claim, release, get_endpoints, discover_master, collect_rx_frames, find_aio,
    cmd_switch_wireless_theme, send_rf_frame,
    USB_ERROR,
)

# Last-known device details from daemon logs — used when RX is unreadable.
KNOWN_DEVICE_MAC = bytes.fromhex("2da374e566e1")
KNOWN_DEVICE_CHANNEL = 8
KNOWN_RX_TYPE = 1

REPEATS = 200
REPEAT_DELAY = 0.02


def send_switch(tx, switch_rf, channel, rx_type, repeats: int = REPEATS) -> None:
    for i in range(repeats):
        send_rf_frame(tx, switch_rf, channel, rx_type)
        time.sleep(REPEAT_DELAY)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{repeats}", flush=True)


def try_reset_rx(rx) -> None:
    """Send CMD_RESET to the RX dongle to clear its wireless state."""
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


def try_rx_discovery(rx, master_mac):
    """Attempt RX device discovery. Returns record or None — never raises."""
    try:
        frames = collect_rx_frames(rx, count=5, max_wait=5.0)
        return find_aio(frames, master_mac=master_mac)
    except Exception as exc:
        print(f"RX collection failed ({exc}); using last-known device details")
        return None


def main() -> int:
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

        # --- Try RX in its own isolated block; release before using TX ---
        rx = None
        rec = None
        try:
            for vid, pid in RX_IDS:
                rx = usb_core.find(idVendor=vid, idProduct=pid)
                if rx:
                    break
            if rx:
                claim(rx)
                try_reset_rx(rx)
                rec = try_rx_discovery(rx, master_mac)
        except Exception as exc:
            print(f"RX setup failed ({exc}); using last-known device details")
        finally:
            # Release RX before touching TX so a bad RX state can't corrupt TX.
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
        print(f"Sending {REPEATS}x wireless switch commands...", flush=True)
        send_switch(tx, switch_rf, device_ch, rx_type)

        print("\nDone. Restart the daemon to resume normal control:")
        print("  sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift")
        return 0

    finally:
        release(tx, "TX")


if __name__ == "__main__":
    raise SystemExit(main())
