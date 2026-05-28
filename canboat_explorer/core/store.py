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

from nmea2000.decoder import NMEA2000Decoder

from canboat_explorer.core.fast_packet import FastPacketReassembler, N2KMessage
from canboat_explorer.core.n2k_frame_info import n2k_frame_info
from canboat_explorer.core.pgn_catalogue import pgn_name
from canboat_explorer.core.raw_frame import RawFrame
from canboat_explorer.core.session_log import SessionLog

Priority = Literal["ignore", "highlight", "warning", "error"] | None


@dataclass
class AccumEntry:
    last_frame: RawFrame
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

        self.frames: list[RawFrame] = []
        self.by_arb_id: dict[int, AccumEntry] = {}
        self.by_pgn_sa: dict[tuple[int, int], AccumEntry] = {}
        self.by_pgn: dict[int, AccumEntry] = {}
        self.n2k_messages: list[N2KMessage] = []
        self.decoded_by_key: dict[tuple[int, int, tuple], deque] = {}
        self._fp = FastPacketReassembler()
        self._n2k_decoder = NMEA2000Decoder()
        self.pgn_sa_names: dict[tuple[int, int], str] = {}

        # Tab-header warning counters (auto-flag totals, not user flags)
        self.oob_frame_count:      int = 0  # out-of-band: frames that won't link to any N2KMessage
        self.incomplete_msg_count: int = 0  # N2KMessages emitted with is_complete=False
        self.undecoded_msg_count:  int = 0  # complete N2KMessages whose decode returned None

        # JSON-serialisable flag dict; keys are "a:<arb_id>" or "p:<pgn>"
        self._flags: dict[str, str] = {}
        self._load_sidecar()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(self, raw: RawFrame) -> None:
        """Append one frame from live capture: write to log + update memory."""
        self.log.append(raw)
        self._add(raw)

    def bulk_load(self, frames: list[RawFrame]) -> None:
        """Load existing frames at startup without writing to the log."""
        for f in frames:
            self._add(f)

    def _add(self, msg: RawFrame) -> None:
        self.frames.append(msg)

        if msg.is_error_frame:
            return   # error frames are stored in time list only

        n2k = n2k_frame_info(msg)
        if n2k is None:
            self.oob_frame_count += 1
            decoded = None
        else:
            decoded = self._n2k_decoder.decode(msg)

        for n2k_msg in self._fp.feed(msg):
            self.n2k_messages.append(n2k_msg)
            if not n2k_msg.is_complete:
                self.incomplete_msg_count += 1
                # n2k_msg.decoded stays False — current `decoded` is for `msg`,
                # not for this stale-flushed message from a prior sequence.
            else:
                n2k_msg.decoded = decoded is not None
                if not n2k_msg.decoded:
                    self.undecoded_msg_count += 1
            for frame in n2k_msg.frames:
                frame.n2k_msg = n2k_msg

        if decoded is not None:
            qualifier = tuple(sorted(
                (f.id, f.raw_value)
                for f in decoded.fields
                if f.part_of_primary_key
            ))
            key = (decoded.PGN, decoded.source, qualifier)
            if key not in self.decoded_by_key:
                self.decoded_by_key[key] = deque(maxlen=200)
            self.decoded_by_key[key].appendleft(decoded)

            sa_key = (decoded.PGN, decoded.source)
            self.pgn_sa_names[sa_key] = decoded.description

        entry = self.by_arb_id.get(msg.arbitration_id)
        if entry is None:
            self.by_arb_id[msg.arbitration_id] = AccumEntry(msg, 1)
        else:
            entry.last_frame = msg
            entry.count += 1

        if n2k is not None:
            key = (n2k.pgn, n2k.sa)
            entry = self.by_pgn_sa.get(key)
            if entry is None:
                self.by_pgn_sa[key] = AccumEntry(msg, 1)
            else:
                entry.last_frame = msg
                entry.count += 1

            entry = self.by_pgn.get(n2k.pgn)
            if entry is None:
                self.by_pgn[n2k.pgn] = AccumEntry(msg, 1)
            else:
                entry.last_frame = msg
                entry.count += 1

    # ------------------------------------------------------------------
    # Flag / highlight state
    # ------------------------------------------------------------------

    def pgn_sa_name(self, pgn: int, sa: int) -> str:
        return self.pgn_sa_names.get((pgn, sa)) or pgn_name(pgn)

    def get_user_flag_by_arb(self, arb_id: int) -> Priority:
        return self._flags.get(f"a:{arb_id}")  # type: ignore[return-value]

    def get_user_flag_by_pgn(self, pgn: int) -> Priority:
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
        self.n2k_messages.clear()
        self.decoded_by_key.clear()
        self._fp = FastPacketReassembler()
        self._n2k_decoder = NMEA2000Decoder()
        self.pgn_sa_names.clear()
        self.oob_frame_count = 0
        self.incomplete_msg_count = 0
        self.undecoded_msg_count = 0

    def clear(self) -> None:
        """Archive the current log, reset in-memory state, start fresh."""
        self.log.close()
        SessionLog.archive(self.log.path)
        self.log = SessionLog(self.log.path, append=True)
        self.reset_memory()
