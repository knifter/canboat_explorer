"""
Fixed-size binary session log.

Record layout (21 bytes per frame):
  [0:8]   timestamp  — float64, seconds since epoch (big-endian)
  [8:12]  arb_id     — uint32 (big-endian)
  [12]    dlc        — uint8
  [13:21] data       — 8 bytes (zero-padded)
"""
from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Iterator

from nemafiddler.can_reader import RawFrame

RECORD_SIZE = 21
_PACK = struct.Struct(">dIB8s")   # float64, uint32, uint8, 8s  → 21 bytes


def encode(frame: RawFrame) -> bytes:
    return _PACK.pack(frame.timestamp, frame.arbitration_id, frame.dlc, frame.data)


def decode(raw: bytes) -> RawFrame:
    ts, arb_id, dlc, data = _PACK.unpack(raw)
    return RawFrame(timestamp=ts, arbitration_id=arb_id, dlc=dlc, data=data)


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

    def append(self, frame: RawFrame) -> None:
        if self._fh is None:
            return
        self._fh.write(encode(frame))
        self._fh.flush()        # crash-safe: each frame on disk before UI

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    def load(self) -> list[RawFrame]:
        """Read all records from disk into memory."""
        frames: list[RawFrame] = []
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

    def iter_records(self) -> Iterator[RawFrame]:
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
        from nemafiddler.paths import DATA_DIR
        return DATA_DIR / "session.canlog"
