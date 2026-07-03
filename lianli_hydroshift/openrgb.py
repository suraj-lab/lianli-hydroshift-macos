"""Minimal OpenRGB SDK server for the HydroShift daemon.

The daemon remains the only process that owns the Lian Li USB/RF path.  OpenRGB
connects over its SDK protocol and this module translates colour requests into a
thread-safe pending RGB frame that the daemon's normal control loop can flush.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Iterable

LOG = logging.getLogger("lianli-hydroshift.openrgb")

MAGIC = b"ORGB"
HEADER_SIZE = 16
SERVER_PROTOCOL_VERSION = 4

PKT_REQUEST_CONTROLLER_COUNT = 0
PKT_REQUEST_CONTROLLER_DATA = 1
PKT_REQUEST_PROTOCOL_VERSION = 40
PKT_SET_CLIENT_NAME = 50
PKT_RESIZE_ZONE = 1000
PKT_UPDATE_LEDS = 1050
PKT_UPDATE_ZONE_LEDS = 1051
PKT_UPDATE_SINGLE_LED = 1052
PKT_SET_CUSTOM_MODE = 1100
PKT_UPDATE_MODE = 1101
PKT_SAVE_MODE = 1102

DEVICE_TYPE_COOLER = 3
MODE_FLAG_HAS_BRIGHTNESS = 1 << 4
MODE_FLAG_HAS_PER_LED_COLOR = 1 << 5
MODE_FLAG_HAS_MODE_SPECIFIC_COLOR = 1 << 6
COLOR_MODE_PER_LED = 1
COLOR_MODE_MODE_SPECIFIC = 2
ZONE_TYPE_LINEAR = 1

Color = tuple[int, int, int]


@dataclass(frozen=True)
class OpenRgbZone:
    name: str
    led_count: int


@dataclass(frozen=True)
class OpenRgbDevice:
    name: str
    vendor: str
    serial: str
    zones: list[OpenRgbZone]

    @property
    def total_leds(self) -> int:
        return sum(zone.led_count for zone in self.zones)


def clamp_color(value: int) -> int:
    return max(0, min(255, int(value)))


def split_zones(colors: list[Color], zones: list[OpenRgbZone]) -> list[list[Color]]:
    split: list[list[Color]] = []
    offset = 0
    for zone in zones:
        end = offset + zone.led_count
        zone_colors = colors[offset:end]
        if len(zone_colors) < zone.led_count:
            zone_colors.extend([(0, 0, 0)] * (zone.led_count - len(zone_colors)))
        split.append(zone_colors)
        offset = end
    return split


class OpenRgbBridge:
    """OpenRGB SDK server plus pending colour state."""

    def __init__(self, device: OpenRgbDevice, *, host: str = "127.0.0.1", port: int = 6743):
        self.device = device
        self.host = host
        self.port = int(port)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_clients = 0
        self.running = False
        self.error: str | None = None
        self.protocol_version = SERVER_PROTOCOL_VERSION
        self._zone_colors: list[list[Color]] = [
            [(255, 255, 255)] * zone.led_count for zone in device.zones
        ]
        self._dirty = False
        # Whether a real client frame has ever been applied (vs the default white
        # state). Used so we only re-apply cached colours we actually received.
        self._received_frame = False
        self._served_controller_data = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="openrgb-sdk", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        # Wake accept() without waiting for the timeout. Only do this after a
        # successful bind so we do not poke an unrelated process on the same port.
        if self.running:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.2):
                    pass
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self.running = False

    @property
    def has_received_frame(self) -> bool:
        with self._lock:
            return self._received_frame

    def take_pending_frame(self) -> list[Color] | None:
        with self._lock:
            if not self._dirty:
                return None
            self._dirty = False
            return [color for zone in self._zone_colors for color in zone]

    def prime_frame(self, colors: Iterable[Color]) -> None:
        """Seed the bridge with a previously applied frame and queue it for re-send.

        Used after a daemon reconnect/rebind, USB reopen, or wireless-theme
        re-engage so the cooler is restored to the last known OpenRGB colours
        without waiting for the client to push a fresh update.
        """
        color_list = [(clamp_color(r), clamp_color(g), clamp_color(b)) for r, g, b in colors]
        if not color_list:
            return
        with self._lock:
            self._zone_colors = split_zones(color_list, self.device.zones)
            self._received_frame = True
            self._dirty = True

    def set_all_colors(self, colors: Iterable[Color]) -> None:
        color_list = [(clamp_color(r), clamp_color(g), clamp_color(b)) for r, g, b in colors]
        if not color_list:
            return
        with self._lock:
            self._zone_colors = split_zones(color_list, self.device.zones)
            self._dirty = True
            self._received_frame = True

    def set_zone_colors(self, zone_idx: int, colors: Iterable[Color]) -> None:
        color_list = [(clamp_color(r), clamp_color(g), clamp_color(b)) for r, g, b in colors]
        with self._lock:
            if zone_idx < 0 or zone_idx >= len(self._zone_colors):
                return
            zone_len = self.device.zones[zone_idx].led_count
            if len(color_list) < zone_len:
                color_list.extend([self._zone_colors[zone_idx][-1] if self._zone_colors[zone_idx] else (0, 0, 0)] * (zone_len - len(color_list)))
            self._zone_colors[zone_idx] = color_list[:zone_len]
            self._dirty = True
            self._received_frame = True

    def set_single_led(self, led_idx: int, color: Color) -> None:
        with self._lock:
            offset = 0
            for zone_idx, zone in enumerate(self.device.zones):
                if offset <= led_idx < offset + zone.led_count:
                    self._zone_colors[zone_idx][led_idx - offset] = (
                        clamp_color(color[0]),
                        clamp_color(color[1]),
                        clamp_color(color[2]),
                    )
                    self._dirty = True
                    self._received_frame = True
                    return
                offset += zone.led_count

    def _serve(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((self.host, self.port))
                listener.listen(4)
                listener.settimeout(0.2)
                self.running = True
                self.error = None
                LOG.info("OpenRGB SDK bridge listening on %s:%s", self.host, self.port)
                while not self._stop.is_set():
                    try:
                        conn, addr = listener.accept()
                    except socket.timeout:
                        continue
                    except OSError as exc:
                        if not self._stop.is_set():
                            LOG.warning("OpenRGB accept failed: %s", exc)
                        continue
                    thread = threading.Thread(
                        target=self._handle_client,
                        args=(conn, addr),
                        name="openrgb-client",
                        daemon=True,
                    )
                    thread.start()
        except OSError as exc:
            self.running = False
            self.error = str(exc)
            LOG.error("OpenRGB SDK bridge failed: %s", exc)
        finally:
            self.running = False
            LOG.info("OpenRGB SDK bridge stopped")

    def _handle_client(self, conn: socket.socket, addr) -> None:  # noqa: ANN001 - socket addr shape
        with conn:
            conn.settimeout(300)
            with self._lock:
                self._active_clients += 1
            LOG.info("OpenRGB client connected from %s", addr)
            try:
                while not self._stop.is_set():
                    header = _recv_exact(conn, HEADER_SIZE)
                    if not header:
                        return
                    if header[:4] != MAGIC:
                        LOG.debug("OpenRGB client sent invalid magic")
                        return
                    dev_idx, pkt_id, pkt_size = struct.unpack_from("<III", header, 4)
                    payload = _recv_exact(conn, pkt_size) if pkt_size else b""
                    if payload is None:
                        return
                    self._handle_packet(conn, dev_idx, pkt_id, payload)
            except (OSError, struct.error) as exc:
                LOG.debug("OpenRGB client disconnected: %s", exc)
            finally:
                with self._lock:
                    self._active_clients = max(0, self._active_clients - 1)
                LOG.info("OpenRGB client disconnected from %s", addr)

    def _send_packet(self, conn: socket.socket, dev_idx: int, pkt_id: int, payload: bytes) -> None:
        conn.sendall(MAGIC + struct.pack("<III", int(dev_idx), int(pkt_id), len(payload)) + payload)

    def _handle_packet(self, conn: socket.socket, dev_idx: int, pkt_id: int, payload: bytes) -> None:
        if pkt_id == PKT_REQUEST_PROTOCOL_VERSION:
            client_version = struct.unpack_from("<I", payload + b"\0\0\0\0", 0)[0]
            self.protocol_version = min(client_version, SERVER_PROTOCOL_VERSION) if client_version else SERVER_PROTOCOL_VERSION
            self._send_packet(conn, 0, PKT_REQUEST_PROTOCOL_VERSION, struct.pack("<I", SERVER_PROTOCOL_VERSION))
        elif pkt_id == PKT_SET_CLIENT_NAME:
            name = payload.rstrip(b"\0").decode("utf-8", "replace")
            LOG.info("OpenRGB client name: %s", name)
        elif pkt_id == PKT_REQUEST_CONTROLLER_COUNT:
            self._send_packet(conn, 0, PKT_REQUEST_CONTROLLER_COUNT, struct.pack("<I", 1))
        elif pkt_id == PKT_REQUEST_CONTROLLER_DATA:
            data = self._build_controller_data() if dev_idx == 0 else b""
            if dev_idx == 0 and not self._served_controller_data:
                self._served_controller_data = True
                LOG.info(
                    "OpenRGB profile matched device: %s (serial wireless:%s)",
                    self.device.name,
                    self.device.serial,
                )
            self._send_packet(conn, dev_idx, PKT_REQUEST_CONTROLLER_DATA, data)
        elif pkt_id == PKT_SET_CUSTOM_MODE:
            return
        elif pkt_id == PKT_UPDATE_LEDS:
            self._handle_update_leds(payload)
        elif pkt_id == PKT_UPDATE_ZONE_LEDS:
            self._handle_update_zone_leds(payload)
        elif pkt_id == PKT_UPDATE_SINGLE_LED:
            self._handle_update_single_led(payload)
        elif pkt_id in (PKT_UPDATE_MODE, PKT_SAVE_MODE):
            self._handle_update_mode(payload)
        elif pkt_id == PKT_RESIZE_ZONE:
            return
        else:
            LOG.debug("OpenRGB unhandled packet id=%s size=%s", pkt_id, len(payload))

    def _handle_update_leds(self, payload: bytes) -> None:
        if len(payload) < 6:
            return
        count = struct.unpack_from("<H", payload, 4)[0]
        self.set_all_colors(_parse_colors(payload[6:], count))

    def _handle_update_zone_leds(self, payload: bytes) -> None:
        if len(payload) < 10:
            return
        zone_idx = struct.unpack_from("<I", payload, 4)[0]
        count = struct.unpack_from("<H", payload, 8)[0]
        self.set_zone_colors(zone_idx, _parse_colors(payload[10:], count))

    def _handle_update_single_led(self, payload: bytes) -> None:
        if len(payload) < 8:
            return
        led_idx = struct.unpack_from("<I", payload, 0)[0]
        self.set_single_led(led_idx, (payload[4], payload[5], payload[6]))

    def _handle_update_mode(self, payload: bytes) -> None:
        # ponytail: only Direct mode is supported; full mode parsing adds ~100 lines
        # for a feature OpenRGB rarely uses on cooler devices
        return

    def _build_controller_data(self) -> bytes:
        buf = bytearray()
        buf += struct.pack("<I", 0)  # data_size placeholder
        buf += struct.pack("<I", DEVICE_TYPE_COOLER)
        _write_string(buf, self.device.name)
        if self.protocol_version >= 1:
            _write_string(buf, self.device.vendor)
        _write_string(buf, f"{self.device.vendor} {self.device.name} OpenRGB bridge")
        _write_string(buf, "lianli-hydroshift-macos")
        _write_string(buf, self.device.serial)
        _write_string(buf, f"wireless:{self.device.serial}")

        modes = [_mode_entry("Direct", 0, MODE_FLAG_HAS_PER_LED_COLOR, COLOR_MODE_PER_LED, self.protocol_version)]
        buf += struct.pack("<H", len(modes))
        buf += struct.pack("<i", 0)
        for mode in modes:
            buf += mode

        buf += struct.pack("<H", len(self.device.zones))
        for zone in self.device.zones:
            _write_string(buf, zone.name)
            buf += struct.pack("<IIIIH", ZONE_TYPE_LINEAR, zone.led_count, zone.led_count, zone.led_count, 0)
            if self.protocol_version >= 4:
                buf += struct.pack("<H", 0)  # segments

        buf += struct.pack("<H", self.device.total_leds)
        led_idx = 0
        for zone in self.device.zones:
            for i in range(zone.led_count):
                _write_string(buf, f"{zone.name} LED {i + 1}")
                buf += struct.pack("<I", led_idx)
                led_idx += 1

        buf += struct.pack("<H", self.device.total_leds)
        for color in [c for zone in self._zone_colors for c in zone]:
            buf += bytes([color[0], color[1], color[2], 0])

        struct.pack_into("<I", buf, 0, len(buf))
        return bytes(buf)


def _recv_exact(conn: socket.socket, size: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = conn.recv(size - len(chunks))
        if not chunk:
            return None
        chunks += chunk
    return bytes(chunks)


def _parse_colors(data: bytes, count: int) -> list[Color]:
    colors: list[Color] = []
    for idx in range(count):
        offset = idx * 4
        if offset + 3 > len(data):
            break
        colors.append((data[offset], data[offset + 1], data[offset + 2]))
    return colors


def _write_string(buf: bytearray, value: str) -> None:
    encoded = value.encode("utf-8")
    buf += struct.pack("<H", len(encoded) + 1)
    buf += encoded + b"\0"


def _mode_entry(
    name: str,
    value: int,
    flags: int,
    color_mode: int,
    protocol_version: int,
    *,
    colors_min: int = 0,
    colors_max: int = 0,
    default_speed: int = 2,
    default_brightness: int = 4,
) -> bytes:
    buf = bytearray()
    _write_string(buf, name)
    buf += struct.pack("<iIII", value, flags, 0, 4)  # value, flags, speed_min, speed_max
    if protocol_version >= 3:
        buf += struct.pack("<II", 0, 100)  # brightness_min, brightness_max
    buf += struct.pack("<II", colors_min, colors_max)
    buf += struct.pack("<I", default_speed)
    if protocol_version >= 3:
        buf += struct.pack("<I", default_brightness)
    buf += struct.pack("<I", 1)  # direction right
    buf += struct.pack("<I", color_mode)
    buf += struct.pack("<H", 0)  # colors
    return bytes(buf)

