"""
Background thread that reads frames from a python-can bus and pushes them
to a thread-safe queue. Supports pause/resume without disconnecting.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import can


@dataclass(slots=True)
class RawFrame:
    timestamp: float       # seconds since epoch (time.time())
    arbitration_id: int
    dlc: int
    data: bytes            # always 8 bytes (zero-padded)


class CanReader(threading.Thread):
    """
    Opens a python-can bus and streams RawFrame objects into `frame_queue`.

    Adapter types supported: waveshare, slcan, gs_usb, pcan, socketcan.
    NMEA 2000 bitrate is always 250 kbps.
    """

    BITRATE = 250_000

    def __init__(
        self,
        interface: str,
        channel: str | int,
        frame_queue: queue.Queue[RawFrame],
    ) -> None:
        super().__init__(daemon=True, name="CanReader")
        self.interface = interface
        self.channel = channel
        self.frame_queue = frame_queue

        self._paused = threading.Event()
        self._paused.set()          # not paused initially
        self._stop_flag = threading.Event()
        self.error: str | None = None

    # ------------------------------------------------------------------
    # Public control API (called from the GUI thread)
    # ------------------------------------------------------------------

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    @property
    def is_paused(self) -> bool:
        return not self._paused.is_set()

    def stop(self) -> None:
        self._stop_flag.set()
        self._paused.set()      # unblock if waiting in pause

    # ------------------------------------------------------------------
    # Thread body
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            bus = self._open_bus()
        except Exception as exc:
            self.error = str(exc)
            return

        try:
            while not self._stop_flag.is_set():
                self._paused.wait()             # blocks while paused
                if self._stop_flag.is_set():
                    break

                msg = bus.recv(timeout=0.1)
                if msg is None:
                    continue

                data = bytes(msg.data).ljust(8, b"\x00")[:8]
                frame = RawFrame(
                    timestamp=time.time(),
                    arbitration_id=msg.arbitration_id,
                    dlc=msg.dlc,
                    data=data,
                )
                self.frame_queue.put(frame)
        finally:
            bus.shutdown()

    def _open_bus(self) -> can.BusABC:
        kwargs: dict = {"interface": self.interface, "channel": self.channel}

        if self.interface == "waveshare":
            # Waveshare USB-CAN-A uses a custom binary protocol via pyserial.
            # python-can wraps it as interface='serial' with bitrate.
            kwargs = {
                "interface": "serial",
                "channel": self.channel,
                "bitrate": self.BITRATE,
            }
        elif self.interface in ("slcan",):
            kwargs["bitrate"] = self.BITRATE
        elif self.interface == "gs_usb":
            kwargs["bitrate"] = self.BITRATE
        elif self.interface == "pcan":
            kwargs["bitrate"] = self.BITRATE
        # socketcan: bitrate is set at OS level, not passed here

        return can.Bus(**kwargs)
