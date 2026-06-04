#!/usr/bin/env python3
"""Send CMD_REBOOT to the HydroShift II LCD via its direct USB connection.

Uses the WinUSB LCD protocol reverse-engineered by sgtaziz/lian-li-linux:
  - DES-CBC encryption, key+IV = b"slv3tuzx"
  - 512-byte encrypted command frames sent over bulk OUT endpoint
  - Sequence: StopPlay → SwitchToDesktop → Reboot

The LCD device shows up as 1CBE:A021 (Circle) or 1CBE:A034 (Square/S).
"""

import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

try:
    from Crypto.Cipher import DES
    from Crypto.Util.Padding import pad
except ImportError:
    print("ERROR: pycryptodome not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import usb.core
    import usb.util
except ImportError:
    print("ERROR: pyusb not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

# From lian-li-linux crates/lianli-devices/src/crypto.rs
DES_KEY = b"slv3tuzx"
DES_IV  = b"slv3tuzx"

# Known VID:PID pairs for the HydroShift II LCD direct USB interface
LCD_IDS = [(0x1CBE, 0xA034), (0x1CBE, 0xA021)]

# Command codes (from crypto.rs)
CMD_GET_VER           = 0x0A
CMD_REBOOT            = 0x0B
CMD_STOP_PLAY         = 0x7B
CMD_SWITCH_TO_DESKTOP = 0x96

FRAME_SIZE    = 512
PAYLOAD_SIZE  = 500  # plaintext payload before encryption
TRAILER_A     = 0xA1
TRAILER_B     = 0x1A


def build_cmd(cmd: int, timestamp_ms: int) -> bytes:
    """Build a 512-byte encrypted WinUSB LCD command frame."""
    plaintext = bytearray(PAYLOAD_SIZE)
    plaintext[0] = cmd
    plaintext[2] = 0x1A
    plaintext[3] = 0x6D
    ts = timestamp_ms & 0xFFFFFFFF
    plaintext[4] = ts & 0xFF
    plaintext[5] = (ts >> 8) & 0xFF
    plaintext[6] = (ts >> 16) & 0xFF
    plaintext[7] = (ts >> 24) & 0xFF

    padded    = pad(bytes(plaintext), DES.block_size)   # 504 bytes
    cipher    = DES.new(DES_KEY, DES.MODE_CBC, iv=DES_IV)
    encrypted = cipher.encrypt(padded)                  # 504 bytes

    frame = bytearray(FRAME_SIZE)
    frame[:len(encrypted)] = encrypted
    frame[510] = TRAILER_A
    frame[511] = TRAILER_B
    return bytes(frame)


def find_out_endpoint(dev):
    cfg  = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    for ep in intf:
        if not (ep.bEndpointAddress & 0x80):
            return ep.bEndpointAddress
    raise RuntimeError("No bulk OUT endpoint found on LCD device")


def send_cmd(dev, out_ep: int, cmd: int, label: str, timestamp_ms: int) -> None:
    frame = build_cmd(cmd, timestamp_ms)
    dev.write(out_ep, frame, timeout=3000)
    print(f"  → {label} (0x{cmd:02X}) sent")


def main() -> int:
    dev = None
    for vid, pid in LCD_IDS:
        dev = usb.core.find(idVendor=vid, idProduct=pid)
        if dev is not None:
            print(f"Found LCD device: {vid:04x}:{pid:04x}")
            break

    if dev is None:
        print(f"ERROR: HydroShift II LCD USB device not found", file=sys.stderr)
        print(f"  Tried: {', '.join(f'{v:04x}:{p:04x}' for v,p in LCD_IDS)}", file=sys.stderr)
        return 1

    # Reset the device to clear any stalled state and force exclusive access.
    try:
        dev.reset()
        time.sleep(0.5)
        # Re-find after reset — the device object may be stale.
        for vid, pid in LCD_IDS:
            dev = usb.core.find(idVendor=vid, idProduct=pid)
            if dev is not None:
                break
        print("Device reset OK")
    except usb.core.USBError as exc:
        print(f"Reset failed ({exc}), continuing anyway")

    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass

    try:
        usb.util.claim_interface(dev, 0)
    except usb.core.USBError as exc:
        print(f"ERROR: could not claim LCD interface: {exc}", file=sys.stderr)
        return 1

    try:
        out_ep = find_out_endpoint(dev)
        print(f"Bulk OUT endpoint: 0x{out_ep:02X}")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        usb.util.release_interface(dev, 0)
        return 1

    try:
        # Clear any endpoint stall before sending — a halted endpoint causes timeout.
        try:
            dev.clear_halt(out_ep)
            print(f"Cleared halt on EP 0x{out_ep:02X}")
        except usb.core.USBError:
            pass

        t0 = 0
        print("Sending init + switch-to-desktop + reboot sequence...")
        # GetVer first — some devices require a handshake before accepting commands.
        try:
            send_cmd(dev, out_ep, CMD_GET_VER, "GetVer", t0)
            t0 += 50
        except usb.core.USBError as exc:
            print(f"  GetVer timed out ({exc}), continuing anyway")
        send_cmd(dev, out_ep, CMD_STOP_PLAY,         "StopPlay",         t0)
        t0 += 50
        send_cmd(dev, out_ep, CMD_SWITCH_TO_DESKTOP, "SwitchToDesktop",  t0)
        t0 += 50
        send_cmd(dev, out_ep, CMD_REBOOT,            "Reboot",           t0)
        print("Done — device should reboot its display controller.")
        print("Wait a few seconds, then restart the daemon:")
        print("  sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift")
        return 0
    except usb.core.USBError as exc:
        print(f"ERROR sending command: {exc}", file=sys.stderr)
        return 1
    finally:
        usb.util.release_interface(dev, 0)
        usb.util.dispose_resources(dev)


if __name__ == "__main__":
    raise SystemExit(main())
