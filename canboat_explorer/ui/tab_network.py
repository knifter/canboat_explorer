from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QHeaderView, QLabel, QListWidget, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from canboat_explorer.core.pgn_catalogue import pgn_name
from canboat_explorer.core.store import DataStore, DeviceInfo

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

    def has_warnings(self) -> bool:
        return bool(self._store.sa_pgn_collisions)

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _collision_tooltip(self, pgn: int) -> str:
        name     = pgn_name(pgn)
        pgn_ref  = f"PGN {pgn} ({name})" if name else f"PGN {pgn}"
        observed = sorted(s for (p, s) in self._store.n2k_by_pgn_sa if p == pgn)
        variants = sorted({
            desc for (p, _), desc in self._store.pgn_sa_names.items() if p == pgn
        })

        if len(variants) > 1:
            sa_list = ", ".join(f"SA:{s}" for s in observed)
            return (f"{pgn_ref}: SA address collision — different device variants "
                    f"across {sa_list}: {', '.join(variants)}")

        if len(observed) >= 2:
            sa_list = ", ".join(f"SA:{s}" for s in observed)
            return f"{pgn_ref}: transmitted by multiple source addresses ({sa_list})"

        advertised = self._store.sa_tx_by_pgn.get(pgn, set())
        union      = sorted(set(observed) | advertised)
        sa_list    = ", ".join(f"SA:{s}" for s in union)
        return f"{pgn_ref}: might be transmitted by multiple source addresses ({sa_list})"

    # ------------------------------------------------------------------
    # Tree rebuild
    # ------------------------------------------------------------------

    def _rebuild_tree(self) -> None:
        self._tree.clear()
        now        = time.time()
        all_sa     = sorted(self._store.devices)
        collisions = self._store.sa_pgn_collisions

        for sa in all_sa:
            dev     = self._store.devices[sa]
            has_ac  = dev.has_address_claim
            last_ts = dev.last_seen_ts
            stale   = last_ts > 0 and (now - last_ts) > _STALE_S

            if dev.model:
                label = f"SA:{sa}  —  {dev.model}"
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

            info_line = dev.product_info_line
            if info_line:
                info_item = QTreeWidgetItem([info_line, ""])
                italic = QFont()
                italic.setItalic(True)
                info_item.setFont(0, italic)
                info_item.setFlags(info_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                sa_item.addChild(info_item)

            if has_ac:
                self._build_identified_children(sa_item, sa, dev, collisions)
            else:
                self._build_seen_only_children(sa_item, sa, dev, collisions)

            sa_item.setExpanded(True)

    def _build_identified_children(
        self, sa_item: QTreeWidgetItem, sa: int,
        dev: DeviceInfo, collisions: set[int],
    ) -> None:
        tx_pgns = sorted(dev.tx_pgns)
        rx_pgns = sorted(dev.rx_pgns)

        if tx_pgns:
            tx_hdr = self._section_item(f"TX PGNs ({len(tx_pgns)})")
            sa_item.addChild(tx_hdr)
            for pgn in tx_pgns:
                prefix = "⚠ " if pgn in collisions else "    "
                child  = QTreeWidgetItem(
                    [prefix + _pgn_label(pgn), self._obs_status(pgn, sa)]
                )
                if pgn not in dev.seen_pgns:
                    child.setForeground(1, QBrush(_STALE_FG))
                if pgn in collisions:
                    child.setToolTip(0, self._collision_tooltip(pgn))
                tx_hdr.addChild(child)
            tx_hdr.setExpanded(True)

        if rx_pgns:
            rx_hdr = self._section_item(f"RX PGNs ({len(rx_pgns)})")
            sa_item.addChild(rx_hdr)
            for pgn in rx_pgns:
                child = QTreeWidgetItem(["    " + _pgn_label(pgn), ""])
                rx_hdr.addChild(child)
            rx_hdr.setExpanded(True)

        unadvertised = dev.seen_pgns - dev.tx_pgns - dev.rx_pgns - _MGMT_PGNS
        if unadvertised:
            ua_label = "Observed / not advertised" + (
                "  (no PGN list received)" if not tx_pgns and not rx_pgns else ""
            )
            ua_hdr = self._section_item(ua_label)
            sa_item.addChild(ua_hdr)
            for pgn in sorted(unadvertised):
                prefix = "⚠ " if pgn in collisions else "    "
                child = QTreeWidgetItem(
                    [prefix + _pgn_label(pgn), self._obs_status(pgn, sa)]
                )
                if pgn in collisions:
                    child.setToolTip(0, self._collision_tooltip(pgn))
                ua_hdr.addChild(child)
            ua_hdr.setExpanded(True)

    def _build_seen_only_children(
        self, sa_item: QTreeWidgetItem, sa: int,
        dev: DeviceInfo, collisions: set[int],
    ) -> None:
        visible = dev.seen_pgns - _MGMT_PGNS
        if not visible:
            return
        hdr = self._section_item(f"Observed PGNs ({len(visible)})")
        sa_item.addChild(hdr)
        for pgn in sorted(visible):
            prefix = "⚠ " if pgn in collisions else "    "
            child = QTreeWidgetItem([prefix + _pgn_label(pgn), self._obs_status(pgn, sa)])
            if pgn in collisions:
                child.setToolTip(0, self._collision_tooltip(pgn))
            hdr.addChild(child)
        hdr.setExpanded(True)

    def _obs_status(self, pgn: int, sa: int) -> str:
        entry = self._store.n2k_by_pgn_sa.get((pgn, sa))
        if entry is None:
            return "(not observed)"
        interval = ""
        if entry.interval_ms is not None:
            interval = f"  {entry.interval_ms:.0f}ms"
        return f"✓ {entry.count}×{interval}"

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
            dev     = self._store.devices.get(sa)
            if dev is None:
                continue
            last_ts = dev.last_seen_ts
            stale   = last_ts > 0 and (now - last_ts) > _STALE_S
            has_ac  = dev.has_address_claim
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
            if m.pgn == 60928:  # ISO Address Claim
                self._log.addItem(f"{_fmt_time(m.timestamp)}  SA:{m.sa}  claimed address")
        self._log_processed = len(msgs)
        if self._log.count():
            self._log.scrollToBottom()
