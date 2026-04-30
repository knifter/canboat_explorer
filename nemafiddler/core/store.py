"""
In-memory data store.

All mutations happen on the UI thread (drained from the queue by a QTimer),
so no locking is needed for reads that also happen on the UI thread.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from nemafiddler.bus.can_reader import RawFrame
from nemafiddler.core.n2k import N2KFrame, parse as n2k_parse
from nemafiddler.core.session_log import SessionLog

Priority = Literal["ignore", "highlight"] | None


@dataclass
class AccumEntry:
    last_frame: RawFrame
    count: int
    last_n2k: N2KFrame | None = None
    first_ts: float = 0.0   # timestamp of the first frame seen for this key

    def __post_init__(self) -> None:
        if self.first_ts == 0.0:
            self.first_ts = self.last_frame.timestamp

    @property
    def interval_ms(self) -> float | None:
        if self.count < 2:
            return None
        elapsed = self.last_frame.timestamp - self.first_ts
        if elapsed <= 0:
            return None
        return elapsed / (self.count - 1) * 1000


class DataStore:
    def __init__(self, log: SessionLog, sidecar_path: Path) -> None:
        self.log = log
        self.sidecar_path = sidecar_path

        self.frames: list[RawFrame] = []
        self.by_arb_id: dict[int, AccumEntry] = {}
        self.by_pgn_sa: dict[tuple[int, int], AccumEntry] = {}
        self.by_pgn: dict[int, AccumEntry] = {}

        # JSON-serialisable flag dict; keys are "a:<arb_id>" or "p:<pgn>"
        self._flags: dict[str, str] = {}
        self._load_sidecar()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(self, frame: RawFrame) -> None:
        """Append one frame from live capture: write to log + update memory."""
        self.log.append(frame)
        self._add(frame)

    def bulk_load(self, frames: list[RawFrame]) -> None:
        """Load existing frames at startup without writing to the log."""
        for f in frames:
            self._add(f)

    def _add(self, frame: RawFrame) -> None:
        self.frames.append(frame)

        if frame.is_error:
            return   # error frames are stored in time list only

        n2k = n2k_parse(frame)

        entry = self.by_arb_id.get(frame.arbitration_id)
        if entry is None:
            self.by_arb_id[frame.arbitration_id] = AccumEntry(frame, 1, n2k)
        else:
            entry.last_frame = frame
            entry.last_n2k   = n2k
            entry.count += 1

        if n2k is not None:
            key = (n2k.pgn, n2k.sa)
            entry = self.by_pgn_sa.get(key)
            if entry is None:
                self.by_pgn_sa[key] = AccumEntry(frame, 1, n2k)
            else:
                entry.last_frame = frame
                entry.count += 1
                entry.last_n2k = n2k

            entry = self.by_pgn.get(n2k.pgn)
            if entry is None:
                self.by_pgn[n2k.pgn] = AccumEntry(frame, 1, n2k)
            else:
                entry.last_frame = frame
                entry.count += 1
                entry.last_n2k = n2k

    # ------------------------------------------------------------------
    # Flag / highlight state
    # ------------------------------------------------------------------

    def get_flag_by_arb(self, arb_id: int) -> Priority:
        return self._flags.get(f"a:{arb_id}")  # type: ignore[return-value]

    def get_flag_by_pgn(self, pgn: int) -> Priority:
        return self._flags.get(f"p:{pgn}")  # type: ignore[return-value]

    def set_flag_by_arb(self, arb_id: int, value: Priority) -> None:
        self._set(f"a:{arb_id}", value)

    def set_flag_by_pgn(self, pgn: int, value: Priority) -> None:
        self._set(f"p:{pgn}", value)

    def _set(self, key: str, value: Priority) -> None:
        if value is None:
            self._flags.pop(key, None)
        else:
            self._flags[key] = value
        self._save_sidecar()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_sidecar(self) -> None:
        if self.sidecar_path.exists():
            try:
                self._flags = json.loads(self.sidecar_path.read_text(encoding="utf-8"))
            except Exception:
                self._flags = {}

    def _save_sidecar(self) -> None:
        self.sidecar_path.write_text(
            json.dumps(self._flags, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Session clear
    # ------------------------------------------------------------------

    def reset_memory(self) -> None:
        """Clear all in-memory state without touching the log."""
        self.frames.clear()
        self.by_arb_id.clear()
        self.by_pgn_sa.clear()
        self.by_pgn.clear()

    def clear(self) -> None:
        """Archive the current log, reset in-memory state, start fresh."""
        self.log.close()
        SessionLog.archive(self.log.path)
        self.log = SessionLog(self.log.path, append=True)
        self.reset_memory()
