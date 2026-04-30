"""
python-can Bus subclass for the Waveshare USB-CAN-A adapter
(CH340, variable-length binary frame protocol).

Protocol reference: "USB (Serial port) to CAN protocol defines" (Waveshare)
  https://files.waveshare.com/wiki/USB-CAN-A/Demo/USB%20(Serial%20port)%20to%20CAN%20protocol%20defines.pdf

Variable-length frame format:
  byte 0      : 0xAA  (start of frame)
  byte 1      : type  = 0xC0 | (is_extended:1 << 5) | (is_remote:1 << 4) | dlc:4
  bytes 2-5   : arbitration ID, little-endian uint32 (extended) or uint16 (standard)
  bytes 6..   : data payload, exactly dlc bytes
  last byte   : 0x55  (end of frame)

UART baud: 2 000 000 bps (adapter default, DIP switch configured).
CAN rate : 250 000 bps  (NMEA 2000 fixed, DIP switch configured).
"""
from __future__ import annotations

import logging
import struct
import threading
import time

import can
import serial

log = logging.getLogger(__name__)

_SOF = 0xAA
_EOF = 0x55


def _frame_len(is_extended: bool, dlc: int) -> int:
    return (7 if is_extended else 5) + dlc


class WaveshareCANBus(can.BusABC):
    def __init__(
        self,
        channel: str,
        tty_baudrate: int = 2_000_000,
        bitrate: int = 250_000,
        **kwargs,
    ) -> None:
        self._ser = serial.Serial(channel, tty_baudrate, timeout=0)
        self._rx_buf: bytearray = bytearray()
        self._lock = threading.Lock()
        log.info("WaveshareCANBus: %s @ %d baud  CAN %d bps", channel, tty_baudrate, bitrate)
        super().__init__(channel=channel, bitrate=bitrate, **kwargs)

    def send(self, msg: can.Message, timeout: float | None = None) -> None:
        is_ext = bool(msg.is_extended_id)
        rtr    = bool(msg.is_remote_frame)
        data   = bytes(msg.data)
        dlc    = min(len(data), 8)
        type_byte = 0xC0 | (int(is_ext) << 5) | (int(rtr) << 4) | dlc
        if is_ext:
            id_bytes = struct.pack('<I', msg.arbitration_id & 0x1FFFFFFF)
        else:
            id_bytes = struct.pack('<H', msg.arbitration_id & 0x7FF)
        frame = bytes([_SOF, type_byte]) + id_bytes + data[:dlc] + bytes([_EOF])
        with self._lock:
            self._ser.write(frame)

    def _recv_internal(
        self, timeout: float | None
    ) -> tuple[can.Message | None, bool]:
        deadline = None if timeout is None else time.monotonic() + (timeout or 0)
        while True:
            with self._lock:
                chunk = self._ser.read(256)
            if chunk:
                self._rx_buf.extend(chunk)
            msg = self._parse_one()
            if msg is not None:
                return msg, False
            if deadline is not None and time.monotonic() >= deadline:
                return None, False
            time.sleep(0.001)

    def _parse_one(self) -> can.Message | None:
        buf = self._rx_buf
        start = buf.find(_SOF)
        if start < 0:
            buf.clear()
            return None
        if start > 0:
            del buf[:start]
        if len(buf) < 2:
            return None

        if buf[1] == _EOF:
            # Fixed 20-byte mode (AA 55 header):
            #   [0]    0xAA  sync
            #   [1]    0x55  sync
            #   [2-4]  header flags (3 bytes, ignored on RX)
            #   [5-8]  CAN ID, little-endian uint32
            #   [9]    DLC
            #   [10-17] data (8 bytes)
            #   [18]   reserved (0x00)
            #   [19]   checksum = sum(bytes[2:18]) % 256
            if len(buf) < 20:
                return None
            frame = bytes(buf[:20])
            if frame[19] != sum(frame[2:18]) % 256:
                del buf[:1]   # bad checksum — skip and re-sync
                return None
            del buf[:20]
            arb_id = struct.unpack('<I', frame[5:9])[0] & 0x1FFFFFFF
            dlc    = min(frame[9], 8)
            data   = frame[10 : 10 + dlc]
            return can.Message(
                arbitration_id=arb_id,
                data=data,
                is_extended_id=True,
                is_remote_frame=False,
                timestamp=time.time(),
            )

        # Variable-length mode (AA [typebyte] ... 55):
        #   type byte = 0xC0 | (ext<<5) | (rtr<<4) | dlc
        type_byte = buf[1]
        is_ext    = bool(type_byte & 0x20)
        is_remote = bool(type_byte & 0x10)
        dlc       = type_byte & 0x0F
        total     = _frame_len(is_ext, dlc)
        if len(buf) < total:
            return None
        if buf[total - 1] != _EOF:
            del buf[:1]
            return None
        frame = bytes(buf[:total])
        del buf[:total]
        if is_ext:
            arb_id = struct.unpack('<I', frame[2:6])[0] & 0x1FFFFFFF
            data   = frame[6 : 6 + dlc]
        else:
            arb_id = struct.unpack('<H', frame[2:4])[0] & 0x7FF
            data   = frame[4 : 4 + dlc]
        return can.Message(
            arbitration_id=arb_id,
            data=data,
            is_extended_id=is_ext,
            is_remote_frame=is_remote,
            timestamp=time.time(),
        )

    def shutdown(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass
        super().shutdown()
