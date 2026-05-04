"""
In-memory data store.

All mutations happen on the UI thread (drained from the queue by a QTimer),
so no locking is needed for reads that also happen on the UI thread.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import can
from nmea2000.decoder import NMEA2000Decoder

from canboat_explorer.core.fast_packet import FastPacketReassembler, N2KMessage
from canboat_explorer.core.session_log import SessionLog

Priority = Literal["ignore", "highlight", "unknown"] | None


@dataclass
class AccumEntry:
    last_frame: can.Message
    count: int
    first_ts: float = 0.0

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

        self.frames: list[can.Message] = []
        self.by_arb_id: dict[int, AccumEntry] = {}
        self.by_pgn_sa: dict[tuple[int, int], AccumEntry] = {}
        self.by_pgn: dict[int, AccumEntry] = {}
        self.n2k_messages: list[N2KMessage] = []
        self.decoded_by_key: dict[tuple[int, int, tuple], deque] = {}
        self._fp = FastPacketReassembler()
        self._n2k_decoder = NMEA2000Decoder()
        self._unknown_pgns: set[int] = set()
        self._known_pgns:   set[int] = set()
        self._unknown_arbs: set[int] = set()

        # JSON-serialisable flag dict; keys are "a:<arb_id>" or "p:<pgn>"
        self._flags: dict[str, str] = {}
        self._load_sidecar()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(self, msg: can.Message) -> None:
        """Append one frame from live capture: write to log + update memory."""
        self.log.append(msg)
        self._add(msg)

    def bulk_load(self, frames: list[can.Message]) -> None:
        """Load existing frames at startup without writing to the log."""
        for f in frames:
            self._add(f)

    def _add(self, msg: can.Message) -> None:
        self.frames.append(msg)

        if msg.is_error_frame:
            return   # error frames are stored in time list only

        if msg.is_extended_id:
            arb      = msg.arbitration_id
            priority = (arb >> 26) & 0x07
            dp       = (arb >> 24) & 0x01
            pf       = (arb >> 16) & 0xFF
            ps       = (arb >>  8) & 0xFF
            sa       =  arb        & 0xFF
            pgn      = ((dp << 16) | (pf << 8) | ps) if pf >= 240 else ((dp << 16) | (pf << 8))
            is_n2k   = True
        else:
            pgn = sa = priority = 0
            is_n2k = False

        self.n2k_messages.extend(self._fp.feed(msg))

        if is_n2k:
            decoded = self._n2k_decoder.decode(msg)
            if decoded is None:
                if pgn not in self._unknown_pgns:
                    self._unknown_pgns.add(pgn)
            else:
                self._unknown_pgns.discard(pgn)
                self._known_pgns.add(pgn)
                qualifier = tuple(sorted(
                    (f.id, f.raw_value)
                    for f in decoded.fields
                    if f.part_of_primary_key
                ))
                key = (decoded.PGN, decoded.source, qualifier)
                if key not in self.decoded_by_key:
                    self.decoded_by_key[key] = deque(maxlen=200)
                self.decoded_by_key[key].appendleft(decoded)
        elif msg.arbitration_id not in self._unknown_arbs:
            self._unknown_arbs.add(msg.arbitration_id)

        entry = self.by_arb_id.get(msg.arbitration_id)
        if entry is None:
            self.by_arb_id[msg.arbitration_id] = AccumEntry(msg, 1)
        else:
            entry.last_frame = msg
            entry.count += 1

        if is_n2k:
            key = (pgn, sa)
            entry = self.by_pgn_sa.get(key)
            if entry is None:
                self.by_pgn_sa[key] = AccumEntry(msg, 1)
            else:
                entry.last_frame = msg
                entry.count += 1

            entry = self.by_pgn.get(pgn)
            if entry is None:
                self.by_pgn[pgn] = AccumEntry(msg, 1)
            else:
                entry.last_frame = msg
                entry.count += 1

    # ------------------------------------------------------------------
    # Flag / highlight state
    # ------------------------------------------------------------------

    def get_flag_by_arb(self, arb_id: int) -> Priority:
        user = self._flags.get(f"a:{arb_id}")
        if user:
            return user  # type: ignore[return-value]
        if arb_id in self._unknown_arbs:
            return "unknown"
        return None

    def get_flag_by_pgn(self, pgn: int) -> Priority:
        user = self._flags.get(f"p:{pgn}")
        if user:
            return user  # type: ignore[return-value]
        if pgn in self._unknown_pgns and pgn not in self._known_pgns:
            return "unknown"
        return None

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
        self.n2k_messages.clear()
        self.decoded_by_key.clear()
        self._fp = FastPacketReassembler()
        self._n2k_decoder = NMEA2000Decoder()
        self._unknown_pgns.clear()
        self._known_pgns.clear()
        self._unknown_arbs.clear()

    def clear(self) -> None:
        """Archive the current log, reset in-memory state, start fresh."""
        self.log.close()
        SessionLog.archive(self.log.path)
        self.log = SessionLog(self.log.path, append=True)
        self.reset_memory()
