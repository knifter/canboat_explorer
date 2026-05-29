"""
In-memory data store.

All mutations happen on the UI thread (drained from the queue by a QTimer),
so no locking is needed for reads that also happen on the UI thread.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
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
class FrameAccumEntry:
    """Per-(grouping-key) tally of raw CAN frames."""
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


@dataclass
class MessageAccumEntry:
    """Per-(pgn, sa) tally of assembled NMEA 2000 messages."""
    last_msg: N2KMessage
    count: int
    first_ts: float = 0.0

    def __post_init__(self) -> None:
        if self.first_ts == 0.0:
            self.first_ts = self.last_msg.timestamp

    @property
    def interval_ms(self) -> float | None:
        if self.count < 2:
            return None
        elapsed = self.last_msg.timestamp - self.first_ts
        if elapsed <= 0:
            return None
        return elapsed / (self.count - 1) * 1000


@dataclass
class DeviceInfo:
    """Per-SA device identity and observed activity."""
    sa:                int
    has_address_claim: bool      = False           # PGN 60928 has decoded for this SA
    model:             str | None = None           # PGN 126996
    manufacturer:      str | None = None           # PGN 126996
    firmware:          str | None = None           # PGN 126996
    serial:            str | None = None           # PGN 126996
    tx_pgns:           set[int]  = field(default_factory=set)  # advertised via PGN 126464
    rx_pgns:           set[int]  = field(default_factory=set)  # advertised via PGN 126464
    seen_pgns:         set[int]  = field(default_factory=set)  # actually observed on the bus
    last_seen_ts:      float     = 0.0                          # timestamp of last N2KMessage from this SA

    @property
    def product_info_line(self) -> str:
        parts: list[str] = []
        if self.model:        parts.append(f"Model: {self.model}")
        if self.firmware:     parts.append(f"FW: {self.firmware}")
        if self.serial:       parts.append(f"S/N: {self.serial}")
        if self.manufacturer: parts.append(f"Mfr: {self.manufacturer}")
        return "  |  ".join(parts)


# PGNs that update DeviceInfo when decoded.
_DEVICE_INFO_PGNS = {
    60928,   # ISO Address Claim
    126464,  # PGN List — Transmit/Receive
    126996,  # Product Information
}


class DataStore:
    def __init__(self, log: SessionLog, sidecar_path: Path) -> None:
        self.log = log
        self.sidecar_path = sidecar_path

        self.frames: list[RawFrame] = []
        self.by_arb_id: dict[int, FrameAccumEntry] = {}
        self.by_pgn_sa: dict[tuple[int, int], FrameAccumEntry] = {}
        self.by_pgn: dict[int, FrameAccumEntry] = {}
        self.n2k_messages: list[N2KMessage] = []
        self.n2k_by_pgn_sa: dict[tuple[int, int], MessageAccumEntry] = {}
        self.devices: dict[int, DeviceInfo] = {}
        self.decoded_by_key: dict[tuple[int, int, tuple], deque] = {}
        self._fp = FastPacketReassembler()
        self._n2k_decoder = NMEA2000Decoder()
        self.pgn_sa_names: dict[tuple[int, int], str] = {}

        # Tab-header warning counters (auto-flag totals, not user flags)
        self.oob_frame_count:      int = 0  # out-of-band: frames that won't link to any N2KMessage
        self.incomplete_msg_count: int = 0  # N2KMessages emitted with is_complete=False
        self.undecoded_msg_count:  int = 0  # complete N2KMessages whose decode returned None

        # Lazy caches for derived properties — dropped (set to None) on relevant
        # mutations, rebuilt on next read. See sa_tx_by_pgn / sa_pgn_collisions.
        self._sa_tx_by_pgn_cache:      dict[int, set[int]] | None = None
        self._sa_pgn_collisions_cache: set[int]            | None = None

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

            # Ensure DeviceInfo exists for every SA that emits an N2KMessage,
            # then keep last_seen_ts current. Identity fields (model, tx_pgns,
            # …) still populate only when their identification PGNs decode.
            dev = self.devices.get(n2k_msg.sa)
            if dev is None:
                dev = DeviceInfo(sa=n2k_msg.sa)
                self.devices[n2k_msg.sa] = dev
            dev.last_seen_ts = n2k_msg.timestamp

            msg_key = (n2k_msg.pgn, n2k_msg.sa)
            entry = self.n2k_by_pgn_sa.get(msg_key)
            if entry is None:
                self.n2k_by_pgn_sa[msg_key] = MessageAccumEntry(n2k_msg, 1)
                dev.seen_pgns.add(n2k_msg.pgn)
                self._sa_pgn_collisions_cache = None
            else:
                entry.last_msg = n2k_msg
                entry.count += 1

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

            if decoded.PGN in _DEVICE_INFO_PGNS:
                self._update_device(decoded)

        entry = self.by_arb_id.get(msg.arbitration_id)
        if entry is None:
            self.by_arb_id[msg.arbitration_id] = FrameAccumEntry(msg, 1)
        else:
            entry.last_frame = msg
            entry.count += 1

        if n2k is not None:
            key = (n2k.pgn, n2k.sa)
            entry = self.by_pgn_sa.get(key)
            if entry is None:
                self.by_pgn_sa[key] = FrameAccumEntry(msg, 1)
            else:
                entry.last_frame = msg
                entry.count += 1

            entry = self.by_pgn.get(n2k.pgn)
            if entry is None:
                self.by_pgn[n2k.pgn] = FrameAccumEntry(msg, 1)
            else:
                entry.last_frame = msg
                entry.count += 1

    # ------------------------------------------------------------------
    # Device registry (PGN 60928 / 126464 / 126996)
    # ------------------------------------------------------------------

    def _update_device(self, decoded) -> None:
        """Update DeviceInfo for decoded.source based on which PGN this is."""
        sa  = decoded.source
        dev = self.devices.get(sa)
        if dev is None:
            dev = DeviceInfo(sa=sa)
            self.devices[sa] = dev

        if decoded.PGN == 60928:   # ISO Address Claim
            dev.has_address_claim = True
            return

        if decoded.PGN == 126996:  # Product Information
            for f in decoded.fields:
                name = (f.name or "").lower()
                val  = str(f.value or "").strip()
                if not val:
                    continue
                if "model id" in name:
                    dev.model = val
                elif "manufacturer" in name and "code" in name:
                    dev.manufacturer = val
                elif "software" in name or (
                    "version" in name and "database" not in name
                ):
                    dev.firmware = val
                elif "serial" in name:
                    dev.serial = val
            return

        if decoded.PGN == 126464:  # PGN List — Transmit/Receive
            func_code: int | None = None
            collected: list[int]  = []
            for f in decoded.fields:
                name = (f.name or "").lower()
                if "function" in name:
                    try:
                        func_code = int(f.raw_value)
                    except (TypeError, ValueError):
                        pass
                elif "pgn" in name:
                    v = f.value
                    if isinstance(v, list):
                        for x in v:
                            try:
                                collected.append(int(x))
                            except (TypeError, ValueError):
                                pass
                    elif v is not None:
                        try:
                            collected.append(int(v))
                        except (TypeError, ValueError):
                            pass
            if func_code == 0:
                dev.tx_pgns = set(collected)
                self._sa_tx_by_pgn_cache      = None
                self._sa_pgn_collisions_cache = None
            elif func_code == 1:
                dev.rx_pgns = set(collected)

    @property
    def sa_tx_by_pgn(self) -> dict[int, set[int]]:
        """For each PGN, the set of SAs that advertise it in their TX list.
        Lazy-cached; invalidated when a device's tx_pgns change (PGN 126464 decode)."""
        if self._sa_tx_by_pgn_cache is None:
            result: dict[int, set[int]] = {}
            for sa, dev in self.devices.items():
                for pgn in dev.tx_pgns:
                    result.setdefault(pgn, set()).add(sa)
            self._sa_tx_by_pgn_cache = result
        return self._sa_tx_by_pgn_cache

    @property
    def sa_pgn_collisions(self) -> set[int]:
        """PGNs where union of (observed SAs) ∪ (TX-advertised SAs) ≥ 2.
        Lazy-cached; invalidated on a new (pgn, sa) in n2k_by_pgn_sa or a
        PGN 126464 decode (tx_pgns change)."""
        if self._sa_pgn_collisions_cache is None:
            obs_by_pgn: dict[int, set[int]] = {}
            for pgn, sa in self.n2k_by_pgn_sa:
                obs_by_pgn.setdefault(pgn, set()).add(sa)
            tx = self.sa_tx_by_pgn
            out: set[int] = set()
            for pgn in set(obs_by_pgn) | set(tx):
                if len(obs_by_pgn.get(pgn, set()) | tx.get(pgn, set())) >= 2:
                    out.add(pgn)
            self._sa_pgn_collisions_cache = out
        return self._sa_pgn_collisions_cache

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
        self.n2k_by_pgn_sa.clear()
        self.devices.clear()
        self.decoded_by_key.clear()
        self._fp = FastPacketReassembler()
        self._n2k_decoder = NMEA2000Decoder()
        self.pgn_sa_names.clear()
        self.oob_frame_count = 0
        self.incomplete_msg_count = 0
        self.undecoded_msg_count = 0
        self._sa_tx_by_pgn_cache      = None
        self._sa_pgn_collisions_cache = None

    def clear(self) -> None:
        """Archive the current log, reset in-memory state, start fresh."""
        self.log.close()
        SessionLog.archive(self.log.path)
        self.log = SessionLog(self.log.path, append=True)
        self.reset_memory()
