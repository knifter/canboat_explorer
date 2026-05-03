"""
Background thread that reads frames from a python-can bus and pushes them
to a thread-safe queue. Supports pause/resume without disconnecting.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

import can

log = logging.getLogger(__name__)


class CanReader(threading.Thread):
    """
    Opens a python-can bus and streams RawFrame objects into `frame_queue`.

    Adapter types supported: waveshare, slcan, gs_usb, pcan, socketcan.
    NMEA 2000 bitrate is always 250 kbps.
    """

    def __init__(
        self,
        interface: str,
        channel: str | int,
        frame_queue: queue.Queue[can.Message],
        serial_baud: int = 2_000_000,
        can_baud: int = 250_000,
    ) -> None:
        super().__init__(daemon=True, name="CanReader")
        self.interface = interface
        self.channel = channel
        self.frame_queue = frame_queue
        self.serial_baud = serial_baud
        self.can_baud = can_baud

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

                try:
                    msg = bus.recv(timeout=0.1)
                except Exception as exc:
                    log.warning("recv error: %s", exc)
                    self.error = str(exc)
                    break

                if msg is None:
                    continue

                dlc = msg.dlc
                if dlc < 0 or dlc > 15:
                    log.error("impossible DLC %d from adapter", dlc)
                elif dlc > 8:
                    log.warning("DLC %d on classic CAN frame (9-15 = 8 data bytes)", dlc)

                if not msg.timestamp:
                    msg.timestamp = time.time()

                self.frame_queue.put(msg)
        finally:
            bus.shutdown()

    def _open_bus(self) -> can.BusABC:
        if self.interface == "waveshare":
            from nemafiddler.bus.waveshare_bus import WaveshareCANBus
            return WaveshareCANBus(
                channel=str(self.channel),
                tty_baudrate=self.serial_baud,
                bitrate=self.can_baud,
            )

        kwargs: dict = {"interface": self.interface, "channel": self.channel}
        if self.interface in ("slcan", "gs_usb", "pcan"):
            kwargs["bitrate"] = self.can_baud
        # socketcan: bitrate is set at OS level, not passed here

        return can.Bus(**kwargs)
