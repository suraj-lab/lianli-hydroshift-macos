"""Health-check tool for Lian Li HydroShift daemon.

Usage: python -m lianli_hydroshift.doctor [--config PATH]
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import enum
from datetime import datetime
from pathlib import Path

LOG = __import__("logging").getLogger("lianli-hydroshift.doctor")

DEFAULT_LOG_DIR = os.path.expanduser("~/Library/Logs/lianli-hydroshift")
LAUNCHD_LABEL = "com.suraj.lianli-hydroshift"
DOCTOR_TELEMETRY_STALE_S = 120.0

TX_IDS = [(0x0416, 0x8040), (0x1A86, 0xE304)]
RX_IDS = [(0x0416, 0x8041), (0x1A86, 0xE305)]
LCD_IDS = [(0x1CBE, 0xA034)]


class HealthStatus(enum.StrEnum):
    HEALTHY = "healthy"
    DISCONNECTED = "disconnected"
    DAEMON_DOWN = "daemon_down"
    UNBOUND = "unbound"
    STALE = "stale"
    RGB_NOT_APPLIED = "rgb_not_applied"
    UNKNOWN = "unknown"


# Substrings matched against discovery log lines, mapped to a simple state string.
# The daemon no longer distinguishes unbound from absent; "not found bound" covers
# both, and recover-display.sh --dry-run is the actual diagnoser.
_DISCOVERY_LOG_MARKERS = [
    ("HydroShift AIO not found bound to this master", "unbound"),
    ("wireless dongle not found", "dongle_missing"),
    ("did not respond to GET_MAC", "master_unknown"),
    ("entering control loop", "bound"),
]


def _parse_log_timestamp(line: str) -> datetime | None:
    parts = line.split(None, 2)
    if len(parts) < 2:
        return None
    stamp = f"{parts[0]} {parts[1]}"
    for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(stamp, fmt)
        except ValueError:
            continue
    return None


def parse_last_telemetry(text: str) -> tuple[datetime | None, str | None]:
    for line in reversed(text.splitlines()):
        if "telemetry=" in line and "coolant=" in line:
            state = None
            for token in line.split():
                if token.startswith("telemetry="):
                    state = token.split("=", 1)[1]
                    break
            return _parse_log_timestamp(line), state
    return None, None


def parse_recent_discovery_state(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        for marker, state in _DISCOVERY_LOG_MARKERS:
            if marker in line:
                return state
    return None


def parse_last_rgb_applied(text: str) -> datetime | None:
    for line in reversed(text.splitlines()):
        if "OpenRGB RGB frame" in line:
            return _parse_log_timestamp(line)
    return None


def parse_last_daemon_start(text: str) -> datetime | None:
    for line in reversed(text.splitlines()):
        if "lianli-hydroshift daemon starting" in line:
            return _parse_log_timestamp(line)
    return None


def read_daemon_log() -> str:
    for name in (f"{LAUNCHD_LABEL}.err", f"{LAUNCHD_LABEL}.log"):
        path = os.path.join(DEFAULT_LOG_DIR, name)
        try:
            with open(path, "r", errors="replace") as fh:
                # ponytail: 20MB cap; the telemetry-heavy log grows ~2MB/week and a
                # 200KB window lost the morning's "daemon starting"/RGB lines by noon
                return fh.read()[-20_000_000:]
        except OSError:
            continue
    return ""


def _load_config_host_port(config_path: str) -> tuple[str, int]:
    """Load just the OpenRGB host/port from config (best-effort)."""
    try:
        with open(config_path, "r") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            return cfg.get("openrgb_host", "127.0.0.1"), int(cfg.get("openrgb_port", 6743))
    except Exception:
        pass
    return "127.0.0.1", 6743


def usb_presence() -> dict[str, bool]:
    """Non-invasive presence check (enumeration only, no claim).

    pyusb first — it sees devices even when the root daemon has claimed them,
    and system_profiler omits these dongles entirely on the Hackintosh (388c04d).
    """
    try:
        import usb.core as usb_core
    except ImportError:
        usb_core = None

    def present(ids: list[tuple[int, int]]) -> bool:
        if usb_core is not None:
            try:
                if any(usb_core.find(idVendor=v, idProduct=p) is not None for v, p in ids):
                    return True
            except Exception:
                pass
        # Fall back to system_profiler on macOS
        try:
            output = subprocess.run(
                ["system_profiler", "SPUSBDataType"],
                capture_output=True, text=True, timeout=5.0,
            ).stdout
            for vid, pid in ids:
                if f"Product ID: 0x{pid:04x}" in output and f"Vendor ID: 0x{vid:04x}" in output:
                    return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        return False

    return {"tx": present(TX_IDS), "rx": present(RX_IDS), "lcd": present(LCD_IDS)}


def tcp_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def process_running(pattern: str) -> bool:
    try:
        proc = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    current_pid = os.getpid()
    for line in proc.stdout.splitlines():
        try:
            if int(line.strip()) != current_pid:
                return True
        except ValueError:
            continue
    return False


def classify_health(
    *,
    tx_present: bool,
    rx_present: bool,
    daemon_running: bool,
    telemetry_age_s: float | None,
    discovery_state: str | None,
    openrgb_port_open: bool,
    rgb_applied: bool,
) -> tuple[HealthStatus, str]:
    if not tx_present or not rx_present:
        missing = " and ".join(n for n, ok in (("TX", tx_present), ("RX", rx_present)) if not ok)
        return (
            HealthStatus.DISCONNECTED,
            f"{missing} dongle missing — reseat/replug the USB dongle, then re-run.",
        )
    if discovery_state == "unbound":
        return (
            HealthStatus.UNBOUND,
            "AIO not found bound to this master (may be unbound or powered off) — "
            "run ./scripts/recover-display.sh --dry-run to diagnose, then without "
            "--dry-run if it reports an unbound AIO.",
        )
    if not daemon_running:
        return (
            HealthStatus.DAEMON_DOWN,
            f"Daemon not running — sudo launchctl kickstart -k system/{LAUNCHD_LABEL}",
        )
    if telemetry_age_s is None or telemetry_age_s > DOCTOR_TELEMETRY_STALE_S:
        return (
            HealthStatus.STALE,
            "No fresh telemetry — check the daemon log; if persistent, "
            f"sudo launchctl kickstart -k system/{LAUNCHD_LABEL}",
        )
    if not openrgb_port_open or not rgb_applied:
        return (
            HealthStatus.RGB_NOT_APPLIED,
            "Cooling is healthy but RGB is not applied — start/reload OpenRGB "
            "with ~/.config/OpenRGB/MacOS.orp (LaunchAgent org.openrgb).",
        )
    return (HealthStatus.HEALTHY, "All checks passed.")


def run_doctor(config_path: str) -> int:
    presence = usb_presence()
    log_text = read_daemon_log()
    now = datetime.now()

    ts, telemetry_state = parse_last_telemetry(log_text)
    telemetry_age_s = (now - ts).total_seconds() if ts is not None else None
    discovery_state = parse_recent_discovery_state(log_text)
    rgb_ts = parse_last_rgb_applied(log_text)
    daemon_start_ts = parse_last_daemon_start(log_text)
    rgb_applied_this_run = rgb_ts is not None and (
        daemon_start_ts is None or rgb_ts >= daemon_start_ts
    )
    daemon_running = process_running("lianli_hydroshift.daemon")

    host, port = _load_config_host_port(config_path)
    port_open = tcp_port_open(host, port)
    openrgb_running = process_running("OpenRGB.app/Contents/MacOS/OpenRGB")

    def yn(ok: bool) -> str:
        return "ok" if ok else "MISSING"

    print("== Lian Li HydroShift doctor ==")
    print(f"  USB TX dongle : {yn(presence['tx'])}")
    print(f"  USB RX dongle : {yn(presence['rx'])}")
    print(f"  LCD direct USB: {'present' if presence['lcd'] else 'absent (normal when wireless theme is active)'}")
    print(f"  Daemon process: {'running' if daemon_running else 'NOT running'}")
    if telemetry_age_s is not None:
        print(f"  Telemetry     : {telemetry_state} (last seen {telemetry_age_s:.0f}s ago)")
    else:
        print("  Telemetry     : none found in log")
    if discovery_state is not None:
        print(f"  Discovery     : {discovery_state}")
    print(f"  OpenRGB bridge: {'reachable' if port_open else 'closed'} ({host}:{port})")
    print(f"  OpenRGB app   : {'running' if openrgb_running else 'not running'}")
    if rgb_ts is not None:
        print(f"  Last RGB frame: {(now - rgb_ts).total_seconds():.0f}s ago")

    status, action = classify_health(
        tx_present=presence["tx"],
        rx_present=presence["rx"],
        daemon_running=daemon_running,
        telemetry_age_s=telemetry_age_s,
        discovery_state=discovery_state,
        openrgb_port_open=port_open,
        rgb_applied=rgb_applied_this_run,
    )
    print()
    print(f"  STATUS: {status.value.upper()}")
    print(f"  ACTION: {action}")
    return 0 if status is HealthStatus.HEALTHY else 1
