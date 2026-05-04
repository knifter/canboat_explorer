from __future__ import annotations

import time
from collections import deque

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QSplitter,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from nmea2000 import FieldTypes

from nemafiddler.core.store import DataStore

_MAX_HISTORY = 20
_SKIP_TYPES  = {FieldTypes.RESERVED, FieldTypes.SPARE}

_ROLE_KEY        = Qt.ItemDataRole.UserRole
_ROLE_HISTORICAL = Qt.ItemDataRole.UserRole + 1
_ROLE_MSG        = Qt.ItemDataRole.UserRole + 2


def _fmt_time(ts) -> str:
    if hasattr(ts, 'hour'):
        ms = ts.microsecond // 1000
        return f"{ts.hour:02d}:{ts.minute:02d}:{ts.second:02d}.{ms:03d}"
    t   = time.localtime(float(ts))
    ms  = int((float(ts) % 1) * 1000)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"


def _qualifier_label(decoded_msg) -> str:
    parts = [str(f.value) for f in decoded_msg.fields if f.part_of_primary_key]
    return " / ".join(parts)


def _rate_label(dq: deque) -> str:
    if len(dq) < 2:
        return ""
    try:
        newest = dq[0].timestamp
        oldest = dq[-1].timestamp
        if hasattr(newest, 'total_seconds'):
            elapsed = (newest - oldest).total_seconds()
        else:
            elapsed = float(newest) - float(oldest)
        if elapsed <= 0:
            return ""
        return f"{elapsed / (len(dq) - 1) * 1000:.0f} ms"
    except Exception:
        return ""


class DecodedTab(QWidget):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store            = store
        self._pgn_items:  dict[int,   QTreeWidgetItem] = {}
        self._leaf_items: dict[tuple, QTreeWidgetItem] = {}
        self._selected_key:        tuple | None = None
        self._selected_historical: bool         = False

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- left: signal tree ----
        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Signal", "Rate"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        splitter.addWidget(self._tree)

        # ---- right: field table ----
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)

        self._hdr_label = QLabel("")
        self._hdr_label.setStyleSheet("font-weight: bold; padding: 2px;")
        rl.addWidget(self._hdr_label)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Field", "Value", "Unit"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        rl.addWidget(self._table)

        splitter.addWidget(right)
        splitter.setSizes([350, 650])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_messages_added(self) -> None:
        for key, dq in self._store.decoded_by_key.items():
            if not dq:
                continue
            if key not in self._leaf_items:
                self._create_leaf(key, dq[0])
            leaf = self._leaf_items[key]
            leaf.setText(1, _rate_label(dq))
            if leaf.isExpanded():
                self._rebuild_history(leaf, dq)

        if self._selected_key is not None and not self._selected_historical:
            dq = self._store.decoded_by_key.get(self._selected_key)
            if dq:
                self._show_message(dq[0], historical=False)

    def reset(self) -> None:
        self._tree.clear()
        self._pgn_items.clear()
        self._leaf_items.clear()
        self._selected_key        = None
        self._selected_historical = False
        self._table.setRowCount(0)
        self._hdr_label.setText("")

    # ------------------------------------------------------------------
    # Tree management
    # ------------------------------------------------------------------

    def _create_leaf(self, key: tuple, decoded_msg) -> None:
        pgn, sa, _ = key

        pgn_item = self._pgn_items.get(pgn)
        if pgn_item is None:
            pgn_item = QTreeWidgetItem([decoded_msg.description or f"PGN {pgn}", ""])
            bold = QFont()
            bold.setBold(True)
            pgn_item.setFont(0, bold)
            pgn_item.setFlags(pgn_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._tree.addTopLevelItem(pgn_item)
            self._pgn_items[pgn] = pgn_item

        qualifier = _qualifier_label(decoded_msg)
        label     = f"SA:{sa}  {qualifier}" if qualifier else f"SA:{sa}"
        leaf = QTreeWidgetItem([label, ""])
        leaf.setData(0, _ROLE_KEY,        key)
        leaf.setData(0, _ROLE_HISTORICAL, False)
        leaf.addChild(QTreeWidgetItem(["…", ""]))   # placeholder so arrow appears
        pgn_item.addChild(leaf)
        pgn_item.setExpanded(True)
        self._leaf_items[key] = leaf

    def _rebuild_history(self, leaf: QTreeWidgetItem, dq: deque) -> None:
        leaf.takeChildren()
        for msg in list(dq)[1: _MAX_HISTORY + 1]:
            try:
                ts_str = _fmt_time(msg.timestamp)
            except Exception:
                ts_str = "—"
            child = QTreeWidgetItem([ts_str, ""])
            child.setData(0, _ROLE_KEY,        leaf.data(0, _ROLE_KEY))
            child.setData(0, _ROLE_HISTORICAL, True)
            child.setData(0, _ROLE_MSG,        msg)
            leaf.addChild(child)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        key = item.data(0, _ROLE_KEY)
        if key is None:
            return
        dq = self._store.decoded_by_key.get(key)
        if dq:
            self._rebuild_history(item, dq)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        item       = items[0]
        key        = item.data(0, _ROLE_KEY)
        historical = item.data(0, _ROLE_HISTORICAL)
        if key is None or historical is None:
            return
        if historical:
            msg = item.data(0, _ROLE_MSG)
            if msg is not None:
                self._selected_key        = key
                self._selected_historical = True
                self._show_message(msg, historical=True)
        else:
            self._selected_key        = key
            self._selected_historical = False
            dq = self._store.decoded_by_key.get(key)
            if dq:
                self._show_message(dq[0], historical=False)

    # ------------------------------------------------------------------
    # Right-panel field table
    # ------------------------------------------------------------------

    def _show_message(self, decoded_msg, historical: bool) -> None:
        decoded_msg.apply_preferred_units({})

        desc = decoded_msg.description or f"PGN {decoded_msg.PGN}"
        if historical:
            try:
                ts_str = _fmt_time(decoded_msg.timestamp)
            except Exception:
                ts_str = "snapshot"
            self._hdr_label.setText(f"{desc}  —  snapshot {ts_str}")
            self._hdr_label.setStyleSheet(
                "font-weight: bold; padding: 2px; background: #ffffc8;")
        else:
            self._hdr_label.setText(desc)
            self._hdr_label.setStyleSheet("font-weight: bold; padding: 2px;")

        fields = [f for f in decoded_msg.fields if f.type not in _SKIP_TYPES]
        self._table.setRowCount(len(fields))

        italic = QFont()
        italic.setItalic(True)

        for row, field in enumerate(fields):
            val = field.value
            if isinstance(val, list):
                val_str = ", ".join(str(v) for v in val)
            elif val is None:
                val_str = "—"
            else:
                val_str = str(val)

            name_item = QTableWidgetItem(field.name or "")
            val_item  = QTableWidgetItem(val_str)
            unit_item = QTableWidgetItem(field.unit_of_measurement or "")

            if field.description:
                name_item.setToolTip(field.description)

            if field.part_of_primary_key:
                name_item.setFont(italic)
                val_item.setFont(italic)
                unit_item.setFont(italic)

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, val_item)
            self._table.setItem(row, 2, unit_item)
