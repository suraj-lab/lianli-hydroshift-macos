#!/usr/bin/env python3
"""Send repeated wireless-switch commands to recover a corrupted AIO display state.

If the RX dongle can't be read (device in bad state), falls back to sweeping
common channel/rx_type combinations using the TX master MAC alone.
"""

import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from lianli_hydroshift.daemon import (
    TX_IDS, RX_IDS,
    claim, release, discover_master, collect_rx_frames, find_aio,
    cmd_switch_wireless_theme, send_rf_frame,
    USB_ERROR,
)

REPEATS = 50
REPEAT_DELAY = 0.05


def send_switch(tx, switch_rf, channel, rx_type, repeats: int = REPEATS) -> None:
    for i in range(repeats):
        send_rf_frame(tx, switch_rf, channel, rx_type)
        time.sleep(REPEAT_DELAY)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{repeats}", flush=True)


def main() -> int:
    try:
        import usb.core as usb_core
    except ImportError:
        print("ERROR: pyusb not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 1

    tx = rx = None
    try:
        tx = next((usb_core.find(idVendor=v, idProduct=p) for v, p in TX_IDS if usb_core.find(idVendor=v, idProduct=p)), None)
        rx = next((usb_core.find(idVendor=v, idProduct=p) for v, p in RX_IDS if usb_core.find(idVendor=v, idProduct=p)), None)
        if tx is None or rx is None:
            print(f"ERROR: dongles not found (tx={bool(tx)} rx={bool(rx)})", file=sys.stderr)
            return 1

        claim(tx)
        claim(rx)

        master_mac, master_ch = discover_master(tx)
        if master_mac is None:
            print("ERROR: TX dongle did not respond to GET_MAC scan", file=sys.stderr)
            return 1
        print(f"master: {master_mac.hex(':')}  ch={master_ch}")

        # Try to discover device via RX — may fail if device is in a bad state.
        rec = None
        try:
            frames = collect_rx_frames(rx, count=5, max_wait=5.0)
            rec = find_aio(frames, master_mac=master_mac)
        except (USB_ERROR, Exception) as exc:
            print(f"RX collection failed ({exc}); falling back to channel sweep")

        if rec is not None:
            print(f"device: {rec['mac'].hex(':')}  ch={rec['channel']}  rx={rec['rx_type']}")
            switch_rf = cmd_switch_wireless_theme(master_mac, master_ch, rec["mac"], rec["rx_type"])
            print(f"Sending {REPEATS}x wireless switch on known channel {rec['channel']}...", flush=True)
            send_switch(tx, switch_rf, rec["channel"], rec["rx_type"])
        else:
            # Device MAC unknown — sweep the most common channel/rx_type pairs.
            # Use a broadcast-style placeholder MAC (all zeros means the dongle
            # may forward to any paired device on that channel).
            print("Device not discoverable; sweeping common channels with null MAC...", flush=True)
            null_mac = b"\x00" * 6
            for channel in [8, 6, 10, 4, 12, 2, 14]:
                for rx_type in [2, 1, 3]:
                    switch_rf = cmd_switch_wireless_theme(master_mac, master_ch, null_mac, rx_type)
                    print(f"  ch={channel} rx_type={rx_type}", flush=True)
                    send_switch(tx, switch_rf, channel, rx_type, repeats=10)

        print("\nDone. Restart the daemon to resume normal control:")
        print("  sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift")
        return 0

    finally:
        release(tx, "TX")
        release(rx, "RX")


if __name__ == "__main__":
    raise SystemExit(main())
