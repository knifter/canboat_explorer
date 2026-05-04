"""
Fixed-size binary session log.

Record layout (22 bytes per frame):
  [0:8]   timestamp  — float64, seconds since epoch (big-endian)
  [8:12]  arb_id     — uint32 (big-endian)
  [12]    dlc        — uint8
  [13:21] data       — 8 bytes (zero-padded)
  [21]    flags      — uint8  bit 0: is_error_frame
                              bit 1: is_remote_frame
                              bit 2: is_fd
"""
from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Iterator

import can

RECORD_SIZE = 22
_PACK = struct.Struct(">dIB8sB")  # float64, uint32, uint8, 8s, uint8  → 22 bytes

_FLAG_ERROR  = 0x01
_FLAG_REMOTE = 0x02
_FLAG_FD     = 0x04


def encode(msg: can.Message) -> bytes:
    flags = ((_FLAG_ERROR  if msg.is_error_frame  else 0) |
             (_FLAG_REMOTE if msg.is_remote_frame else 0) |
             (_FLAG_FD     if msg.is_fd           else 0))
    data  = bytes(msg.data)[:8].ljust(8, b"\x00")
    return _PACK.pack(msg.timestamp, msg.arbitration_id, msg.dlc, data, flags)


def decode(raw: bytes) -> can.Message:
    ts, arb_id, dlc, data, flags = _PACK.unpack(raw)
    return can.Message(
        timestamp=ts,
        arbitration_id=arb_id,
        dlc=dlc,
        data=data[:dlc],
        is_extended_id=arb_id > 0x7FF,
        is_error_frame=bool(flags & _FLAG_ERROR),
        is_remote_frame=bool(flags & _FLAG_REMOTE),
        is_fd=bool(flags & _FLAG_FD),
    )


class SessionLog:
    """
    Manages the on-disk binary log for one session.

    Modes:
      - Live (append=True, path may or may not pre-exist):
          Opens file for appending. Call `append()` on every incoming frame.
      - Read-only (append=False):
          Opens existing file for reading only. `append()` is a no-op.

    `load()` reads the entire file into memory and returns a list of RawFrame.
    """

    def __init__(self, path: Path, *, append: bool) -> None:
        self.path = path
        self._append = append
        self._fh = None

        if append:
            self._fh = path.open("ab")

    # ------------------------------------------------------------------
    # Write side (live mode only)
    # ------------------------------------------------------------------

    def append(self, msg: can.Message) -> None:
        if self._fh is None:
            return
        self._fh.write(encode(msg))
        self._fh.flush()        # crash-safe: each frame on disk before UI

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    def load(self) -> list[can.Message]:
        """Read all records from disk into memory."""
        frames: list[can.Message] = []
        if not self.path.exists():
            return frames
        data = self.path.read_bytes()
        n_records = len(data) // RECORD_SIZE
        for i in range(n_records):
            chunk = data[i * RECORD_SIZE : (i + 1) * RECORD_SIZE]
            try:
                frames.append(decode(chunk))
            except struct.error:
                break   # truncated record at end (e.g. from a crash) — stop here
        return frames

    def iter_records(self) -> Iterator[can.Message]:
        """Iterate records without loading all into memory (for large files)."""
        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            while True:
                chunk = fh.read(RECORD_SIZE)
                if len(chunk) < RECORD_SIZE:
                    break
                try:
                    yield decode(chunk)
                except struct.error:
                    break

    # ------------------------------------------------------------------
    # Session management helpers
    # ------------------------------------------------------------------

    @staticmethod
    def write_frames(frames: list, path: Path) -> None:
        """Write a list of RawFrame objects to a new file (overwrites)."""
        with path.open("wb") as fh:
            for frame in frames:
                fh.write(encode(frame))

    @staticmethod
    def archive(path: Path) -> Path:
        """
        Rename `path` to a timestamped archive copy and return the new path.
        Used by the "Clear" operation.
        """
        ts = time.strftime("%Y%m%d_%H%M%S")
        archive_path = path.with_name(f"{path.stem}_{ts}{path.suffix}")
        path.rename(archive_path)
        return archive_path

    @staticmethod
    def default_path() -> Path:
        """Returns the canonical path for the active session log."""
        from canboat_explorer.core.paths import DATA_DIR
        return DATA_DIR / "session.canlog"
