from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QHeaderView, QLabel, QListWidget, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from canboat_explorer.core.n2k import pgn_name
from canboat_explorer.core.store import DataStore

_STALE_S    = 60.0
_SECTION_FG = QColor("#aaaaaa")
_STALE_FG   = QColor("#888888")
_SEEN_FG    = QColor("#aaaaaa")
_ROLE_SA    = Qt.ItemDataRole.UserRole

# PGNs that every N2K device sends as normal bookkeeping — excluded from
# "Observed / not advertised" noise.
_MGMT_PGNS = {59904, 60928, 126208, 126464, 126992, 126993, 126996, 126998, 65240}


def _fmt_time(ts: float) -> str:
    t  = time.localtime(ts)
    ms = int((ts % 1) * 1000)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"


def _pgn_label(pgn: int) -> str:
    name = pgn_name(pgn)
    return f"{pgn}  {name}" if name else str(pgn)


class NetworkTab(QWidget):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store            = store
        self._last_n2k_count   = 0
        self._last_decoded_len = 0
        self._log_processed    = 0

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Device / PGN", "Status"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self._tree)

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(4, 4, 4, 4)
        log_layout.addWidget(QLabel("Address Negotiation Events"))
        self._log = QListWidget()
        log_layout.addWidget(self._log)
        splitter.addWidget(log_widget)
        splitter.setSizes([600, 150])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(10_000)
        self._stale_timer.timeout.connect(self._update_stale)
        self._stale_timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_messages_added(self) -> None:
        n_n2k = len(self._store.n2k_messages)
        n_dec = len(self._store.decoded_by_key)
        if n_n2k == self._last_n2k_count and n_dec == self._last_decoded_len:
            return
        self._last_n2k_count   = n_n2k
        self._last_decoded_len = n_dec
        self._rebuild_tree()
        self._append_log()

    def reset(self) -> None:
        self._tree.clear()
        self._log.clear()
        self._last_n2k_count   = 0
        self._last_decoded_len = 0
        self._log_processed    = 0

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _all_sa(self) -> list[int]:
        seen: set[int] = set()
        for _, sa in self._store.by_pgn_sa:
            seen.add(sa)
        return sorted(seen)

    def _last_seen_ts(self, sa: int) -> float:
        ts = 0.0
        for (_, s), entry in self._store.by_pgn_sa.items():
            if s == sa:
                t = entry.last_frame.timestamp
                if t > ts:
                    ts = t
        return ts

    def _observed_pgns(self, sa: int) -> dict[int, int]:
        return {
            pgn: entry.count
            for (pgn, s), entry in self._store.by_pgn_sa.items()
            if s == sa
        }

    def _collision_pgns(self) -> set[int]:
        pgn_sas: dict[int, set[int]] = {}
        for pgn, sa in self._store.by_pgn_sa:
            pgn_sas.setdefault(pgn, set()).add(sa)
        return {pgn for pgn, sas in pgn_sas.items() if len(sas) > 1}

    def _get_model_name(self, sa: int) -> str | None:
        dq = self._store.decoded_by_key.get((126996, sa, ()))
        if not dq:
            return None
        for field in dq[0].fields:
            if "model id" in (field.name or "").lower():
                val = str(field.value or "").strip()
                if val:
                    return val
        return None

    def _get_product_info_line(self, sa: int) -> str:
        dq = self._store.decoded_by_key.get((126996, sa, ()))
        if not dq:
            return ""
        parts: list[str] = []
        for field in dq[0].fields:
            name_lower = (field.name or "").lower()
            val        = str(field.value or "").strip()
            if not val:
                continue
            if "model id" in name_lower:
                parts.append(f"Model: {val}")
            elif "software" in name_lower or (
                "version" in name_lower and "database" not in name_lower
            ):
                parts.append(f"FW: {val}")
            elif "serial" in name_lower:
                parts.append(f"S/N: {val}")
            elif "manufacturer" in name_lower and "code" in name_lower:
                parts.append(f"Mfr: {val}")
        return "  |  ".join(parts)

    def _get_pgn_lists(self, sa: int) -> tuple[list[int], list[int]]:
        tx: list[int] = []
        rx: list[int] = []
        for (pgn_k, sa_k, _), dq in self._store.decoded_by_key.items():
            if pgn_k != 126464 or sa_k != sa or not dq:
                continue
            func_code: int | None = None
            collected: list[int]  = []
            for field in dq[0].fields:
                name_lower = (field.name or "").lower()
                if "function" in name_lower:
                    try:
                        func_code = int(field.raw_value)
                    except (TypeError, ValueError):
                        pass
                elif "pgn" in name_lower:
                    val = field.value
                    if isinstance(val, list):
                        for v in val:
                            try:
                                collected.append(int(v))
                            except (TypeError, ValueError):
                                pass
                    elif val is not None:
                        try:
                            collected.append(int(val))
                        except (TypeError, ValueError):
                            pass
            if func_code == 0:
                tx = collected
            elif func_code == 1:
                rx = collected
        return tx, rx

    # ------------------------------------------------------------------
    # Tree rebuild
    # ------------------------------------------------------------------

    def _rebuild_tree(self) -> None:
        self._tree.clear()
        now        = time.time()
        collisions = self._collision_pgns()

        for sa in self._all_sa():
            has_ac     = (60928, sa, ()) in self._store.decoded_by_key
            last_ts    = self._last_seen_ts(sa)
            stale      = last_ts > 0 and (now - last_ts) > _STALE_S
            model_name = self._get_model_name(sa)

            if model_name:
                label = f"SA:{sa}  —  {model_name}"
            elif has_ac:
                label = f"SA:{sa}  —  (address claimed)"
            else:
                label = f"SA:{sa}  —  (seen only)"

            status  = f"silent {int(now - last_ts)}s" if stale else ""
            sa_item = QTreeWidgetItem([label, status])
            sa_item.setData(0, _ROLE_SA, sa)
            bold = QFont()
            bold.setBold(True)
            sa_item.setFont(0, bold)
            if stale or not has_ac:
                fg = _STALE_FG if stale else _SEEN_FG
                sa_item.setForeground(0, QBrush(fg))
                sa_item.setForeground(1, QBrush(fg))
            self._tree.addTopLevelItem(sa_item)

            info_line = self._get_product_info_line(sa)
            if info_line:
                info_item = QTreeWidgetItem([info_line, ""])
                italic = QFont()
                italic.setItalic(True)
                info_item.setFont(0, italic)
                info_item.setFlags(info_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                sa_item.addChild(info_item)

            observed = self._observed_pgns(sa)
            if has_ac:
                self._build_identified_children(sa_item, sa, observed, collisions)
            else:
                self._build_seen_only_children(sa_item, sa, observed)

            sa_item.setExpanded(True)

    def _build_identified_children(
        self, sa_item: QTreeWidgetItem, sa: int,
        observed: dict[int, int], collisions: set[int],
    ) -> None:
        tx_pgns, rx_pgns = self._get_pgn_lists(sa)
        tx_set  = set(tx_pgns)
        rx_set  = set(rx_pgns)
        obs_set = set(observed.keys())

        if tx_pgns:
            tx_hdr = self._section_item(f"TX PGNs ({len(tx_pgns)})")
            sa_item.addChild(tx_hdr)
            for pgn in tx_pgns:
                prefix = "⚠ " if pgn in collisions else "    "
                child  = QTreeWidgetItem(
                    [prefix + _pgn_label(pgn), self._obs_status(pgn, sa, observed)]
                )
                if pgn not in observed:
                    child.setForeground(1, QBrush(_STALE_FG))
                tx_hdr.addChild(child)
            tx_hdr.setExpanded(True)

        if rx_pgns:
            rx_hdr = self._section_item(f"RX PGNs ({len(rx_pgns)})")
            sa_item.addChild(rx_hdr)
            for pgn in rx_pgns:
                child = QTreeWidgetItem(["    " + _pgn_label(pgn), ""])
                rx_hdr.addChild(child)
            rx_hdr.setExpanded(True)

        unadvertised = obs_set - tx_set - rx_set - _MGMT_PGNS
        if unadvertised:
            ua_label = "Observed / not advertised" + (
                "  (no PGN list received)" if not tx_pgns and not rx_pgns else ""
            )
            ua_hdr = self._section_item(ua_label)
            sa_item.addChild(ua_hdr)
            for pgn in sorted(unadvertised):
                child = QTreeWidgetItem(
                    ["    " + _pgn_label(pgn), self._obs_status(pgn, sa, observed)]
                )
                ua_hdr.addChild(child)
            ua_hdr.setExpanded(True)

    def _build_seen_only_children(
        self, sa_item: QTreeWidgetItem, sa: int, observed: dict[int, int]
    ) -> None:
        visible = {pgn: cnt for pgn, cnt in observed.items() if pgn not in _MGMT_PGNS}
        if not visible:
            return
        hdr = self._section_item(f"Observed PGNs ({len(visible)})")
        sa_item.addChild(hdr)
        for pgn in sorted(visible):
            child = QTreeWidgetItem(["    " + _pgn_label(pgn), self._obs_status(pgn, sa, observed)])
            hdr.addChild(child)
        hdr.setExpanded(True)

    def _obs_status(self, pgn: int, sa: int, observed: dict[int, int]) -> str:
        cnt = observed.get(pgn)
        if cnt is None:
            return "(not observed)"
        entry    = self._store.by_pgn_sa.get((pgn, sa))
        interval = ""
        if entry and entry.interval_ms is not None:
            interval = f"  {entry.interval_ms:.0f}ms"
        return f"✓ {cnt}×{interval}"

    def _section_item(self, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label, ""])
        bold = QFont()
        bold.setBold(True)
        item.setFont(0, bold)
        item.setForeground(0, QBrush(_SECTION_FG))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        return item

    # ------------------------------------------------------------------
    # Stale update (10 s timer — no full rebuild)
    # ------------------------------------------------------------------

    def _update_stale(self) -> None:
        now = time.time()
        for i in range(self._tree.topLevelItemCount()):
            sa_item = self._tree.topLevelItem(i)
            sa      = sa_item.data(0, _ROLE_SA)
            if sa is None:
                continue
            last_ts = self._last_seen_ts(sa)
            stale   = last_ts > 0 and (now - last_ts) > _STALE_S
            has_ac  = (60928, sa, ()) in self._store.decoded_by_key
            if stale:
                elapsed = int(now - last_ts)
                sa_item.setText(1, f"silent {elapsed}s")
                sa_item.setForeground(0, QBrush(_STALE_FG))
                sa_item.setForeground(1, QBrush(_STALE_FG))
            else:
                sa_item.setText(1, "")
                fg = _SEEN_FG if not has_ac else QColor()
                sa_item.setForeground(0, QBrush(fg))
                sa_item.setForeground(1, QBrush(QColor()))

    # ------------------------------------------------------------------
    # Address negotiation log
    # ------------------------------------------------------------------

    def _append_log(self) -> None:
        msgs = self._store.n2k_messages
        for m in msgs[self._log_processed:]:
            if m.pgn == 60928:
                self._log.addItem(f"{_fmt_time(m.timestamp)}  SA:{m.sa}  claimed address")
        self._log_processed = len(msgs)
        if self._log.count():
            self._log.scrollToBottom()
