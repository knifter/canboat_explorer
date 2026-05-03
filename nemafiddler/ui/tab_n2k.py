from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget,
    QHeaderView,
)

from nemafiddler.core.fast_packet import N2KMessage
from nemafiddler.core.n2k import pgn_name
from nemafiddler.core.store import DataStore

_COLS_TIME: list[tuple[str, str]] = [
    ("Time",         "Timestamp of the first CAN frame of this message."),
    ("PGN",          "Parameter Group Number — NMEA 2000 message type."),
    ("PGN Name",     "Human-readable name for this PGN, if known."),
    ("SA",           "Source Address — NMEA 2000 device address (0–253)."),
    ("Priority",     "NMEA 2000 message priority (0 = highest, 7 = lowest)."),
    ("Length",       "Total assembled payload length in bytes."),
    ("Payload",      "Assembled payload bytes (hex), without fast-packet control bytes."),
    ("Frames",       "Number of CAN frames that made up this message.\n1 = single-frame or fast-packet not detected."),
]

_COLS_ACCUM: list[tuple[str, str]] = [
    ("Last Time",    "Timestamp of the most recent message from this PGN + SA combination."),
    ("PGN",          "Parameter Group Number — NMEA 2000 message type."),
    ("PGN Name",     "Human-readable name for this PGN, if known."),
    ("SA",           "Source Address — NMEA 2000 device address (0–253)."),
    ("Priority",     "NMEA 2000 message priority (0 = highest, 7 = lowest)."),
    ("Length",       "Payload length of the most recent message in bytes."),
    ("Last Payload", "Assembled payload bytes of the most recent message (hex)."),
    ("Count",        "Number of messages received for this PGN + SA combination."),
    ("Interval",     "Average interval between messages (ms) = (last − first) / (count − 1).\nRequires at least 2 messages."),
]


def _fmt_time(ts: float) -> str:
    t = time.localtime(ts)
    ms = int((ts % 1) * 1000)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"


class N2KTimeViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._store.n2k_messages)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLS_TIME)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        label, tip = _COLS_TIME[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return label
        if role == Qt.ItemDataRole.ToolTipRole:
            return tip
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = len(self._store.n2k_messages) - 1 - index.row()
        if row < 0 or row >= len(self._store.n2k_messages):
            return None
        msg = self._store.n2k_messages[row]
        col = index.column()
        if col == 0: return _fmt_time(msg.timestamp)
        if col == 1: return str(msg.pgn)
        if col == 2: return pgn_name(msg.pgn)
        if col == 3: return str(msg.sa)
        if col == 4: return str(msg.priority)
        if col == 5: return str(len(msg.payload))
        if col == 6: return msg.payload.hex(" ").upper()
        if col == 7: return str(msg.frame_count)
        return None

    def refresh(self) -> None:
        self.layoutChanged.emit()


class N2KAccumViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._by_pgn_sa: dict[tuple[int, int], N2KMessage] = {}
        self._counts: dict[tuple[int, int], int] = {}
        self._first_ts: dict[tuple[int, int], float] = {}
        self._processed = 0

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._by_pgn_sa)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLS_ACCUM)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        label, tip = _COLS_ACCUM[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return label
        if role == Qt.ItemDataRole.ToolTipRole:
            return tip
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        keys = list(self._by_pgn_sa.keys())
        if index.row() >= len(keys):
            return None
        key = keys[index.row()]
        msg = self._by_pgn_sa[key]
        col = index.column()
        if col == 0: return _fmt_time(msg.timestamp)
        if col == 1: return str(msg.pgn)
        if col == 2: return pgn_name(msg.pgn)
        if col == 3: return str(msg.sa)
        if col == 4: return str(msg.priority)
        if col == 5: return str(len(msg.payload))
        if col == 6: return msg.payload.hex(" ").upper()
        if col == 7: return str(self._counts[key])
        if col == 8:
            count = self._counts[key]
            if count < 2:
                return "—"
            elapsed = msg.timestamp - self._first_ts[key]
            if elapsed <= 0:
                return "—"
            return f"{elapsed / (count - 1) * 1000:.0f} ms"
        return None

    def refresh(self) -> None:
        new_msgs = self._store.n2k_messages[self._processed:]
        for msg in new_msgs:
            key = (msg.pgn, msg.sa)
            if key not in self._by_pgn_sa:
                self._first_ts[key] = msg.timestamp
                self._counts[key] = 0
            self._by_pgn_sa[key] = msg
            self._counts[key] += 1
        self._processed = len(self._store.n2k_messages)
        self.layoutChanged.emit()

    def reset(self) -> None:
        self._by_pgn_sa.clear()
        self._counts.clear()
        self._first_ts.clear()
        self._processed = 0
        self.layoutChanged.emit()


class N2KTab(QWidget):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store       = store
        self._time_model  = N2KTimeViewModel(store)
        self._accum_model = N2KAccumViewModel(store)

        self._view = QTableView()
        self._view.setModel(self._time_model)
        hdr = self._view.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._view.verticalHeader().setVisible(False)

        self._btn = QPushButton("Accumulated view")
        self._btn.setCheckable(True)
        self._btn.toggled.connect(self._toggle_mode)

        self._count_label = QLabel("0 messages")

        top = QHBoxLayout()
        top.addWidget(self._btn)
        top.addStretch()
        top.addWidget(self._count_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._view)

    def _toggle_mode(self, checked: bool) -> None:
        if checked:
            self._accum_model.refresh()
            self._view.setModel(self._accum_model)
            self._btn.setText("Time view")
        else:
            self._view.setModel(self._time_model)
            self._btn.setText("Accumulated view")
        self._update_count()

    def _update_count(self) -> None:
        total = len(self._store.n2k_messages)
        if self._view.model() is self._time_model:
            self._count_label.setText(f"{total:,} messages")
        else:
            rows = self._accum_model.rowCount()
            self._count_label.setText(f"{rows:,} rows  ({total:,} messages)")

    def on_messages_added(self) -> None:
        model = self._view.model()
        if model is self._time_model:
            self._time_model.refresh()
            self._view.scrollToTop()
        else:
            self._accum_model.refresh()
        self._update_count()

    def reset(self) -> None:
        self._time_model.refresh()
        self._accum_model.reset()
        self._update_count()
