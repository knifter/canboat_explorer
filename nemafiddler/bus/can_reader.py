"""
Background thread that reads frames from a python-can bus and pushes them
to a thread-safe queue. Supports pause/resume without disconnecting.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

import can

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RawFrame:
    timestamp: float       # seconds since epoch (time.time())
    arbitration_id: int
    dlc: int               # 0xFF = error/malformed marker
    data: bytes            # always 8 bytes (zero-padded); error text for error frames
    is_extended_id: bool = True
    is_error: bool = False # True for frames the adapter sent but we couldn't parse


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

                try:
                    msg = bus.recv(timeout=0.1)
                except Exception as exc:
                    log.warning("recv error: %s", exc)
                    # Push a placeholder so the UI and log see it — DLC=0xFF is the marker
                    err_text = str(exc)[:8].encode("ascii", errors="replace").ljust(8, b"\x00")
                    self.frame_queue.put(RawFrame(
                        timestamp=time.time(),
                        arbitration_id=0,
                        dlc=0xFF,
                        data=err_text,
                        is_extended_id=False,
                        is_error=True,
                    ))
                    continue

                if msg is None:
                    continue

                dlc = msg.dlc
                if dlc < 0 or dlc > 15:
                    log.error("impossible DLC %d from adapter", dlc)
                elif dlc > 8:
                    log.warning("DLC %d on classic CAN frame (9-15 = 8 data bytes)", dlc)
                data = bytes(msg.data)[:8].ljust(8, b"\x00")
                frame = RawFrame(
                    timestamp=time.time(),
                    arbitration_id=msg.arbitration_id,
                    dlc=dlc,
                    data=data,
                    is_extended_id=msg.is_extended_id,
                )
                self.frame_queue.put(frame)
        finally:
            bus.shutdown()

    def _open_bus(self) -> can.BusABC:
        if self.interface == "waveshare":
            from nemafiddler.bus.waveshare_bus import WaveshareCANBus
            return WaveshareCANBus(channel=str(self.channel), bitrate=self.BITRATE)

        kwargs: dict = {"interface": self.interface, "channel": self.channel}
        if self.interface in ("slcan", "gs_usb", "pcan"):
            kwargs["bitrate"] = self.BITRATE
        # socketcan: bitrate is set at OS level, not passed here

        return can.Bus(**kwargs)
