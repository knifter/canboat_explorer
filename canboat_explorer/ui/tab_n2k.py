from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QTableView, QVBoxLayout, QWidget,
    QHeaderView,
)

from canboat_explorer.core.fast_packet import N2KMessage
from canboat_explorer.core.store import DataStore, Priority

_COLOR_IGNORE    = QColor(160, 160, 160)
_COLOR_HIGHLIGHT = QColor(240, 190,  50)
_COLOR_WARNING   = QColor(210, 150,  60)
_COLOR_ERROR     = QColor(185,  75,  75)
_COLOR_ERROR_FG  = QColor(255, 255, 255)

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

_GROUP_OPTIONS = ["None", "PGN + SA"]
# Index 0 = time view (no grouping); 1 = accumulated by PGN + SA


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
        if not index.isValid():
            return None
        row = len(self._store.n2k_messages) - 1 - index.row()
        if row < 0 or row >= len(self._store.n2k_messages):
            return None
        msg = self._store.n2k_messages[row]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0: return _fmt_time(msg.timestamp)
            if col == 1: return str(msg.pgn)
            if col == 2: return self._store.pgn_sa_name(msg.pgn, msg.sa)
            if col == 3: return str(msg.sa)
            if col == 4: return str(msg.priority)
            if col == 5: return str(len(msg.payload))
            if col == 6: return msg.payload.hex(" ").upper()
            if col == 7: return str(msg.frame_count)
            return None

        flag = _flag_for_message(self._store, msg)

        if role == Qt.ItemDataRole.ToolTipRole:
            reason = _reason_for_message(msg)
            if reason:
                return reason

        if role == Qt.ItemDataRole.ForegroundRole:
            if flag == "ignore":    return _COLOR_IGNORE
            if flag == "highlight": return QColor(0, 0, 0)
            if flag == "error":     return _COLOR_ERROR_FG

        if role == Qt.ItemDataRole.BackgroundRole:
            if flag == "highlight": return _COLOR_HIGHLIGHT
            if flag == "warning":   return _COLOR_WARNING
            if flag == "error":     return _COLOR_ERROR

        return None

    def refresh(self) -> None:
        self.layoutChanged.emit()


def _flag_for_message(store: DataStore, msg: N2KMessage) -> Priority:
    f = store.get_user_flag_by_pgn(msg.pgn)
    if f:
        return f
    if not msg.is_complete:
        return "error"
    if not msg.decoded:
        return "warning"
    return None


def _reason_for_message(msg: N2KMessage) -> str:
    if not msg.is_complete:
        return f"PGN {msg.pgn}: incomplete fast-packet sequence (timed out before all frames arrived)"
    if not msg.decoded:
        return f"PGN {msg.pgn}: nmea2000 decoder could not parse the payload"
    return ""


class N2KAccumViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._keys_cache: list | None = None

    def _keys(self) -> list:
        if self._keys_cache is None:
            self._keys_cache = list(self._store.n2k_by_pgn_sa.keys())
        return self._keys_cache

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._store.n2k_by_pgn_sa)

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
        if not index.isValid():
            return None
        keys = self._keys()
        if index.row() >= len(keys):
            return None
        entry = self._store.n2k_by_pgn_sa[keys[index.row()]]
        msg   = entry.last_msg
        col   = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0: return _fmt_time(msg.timestamp)
            if col == 1: return str(msg.pgn)
            if col == 2: return self._store.pgn_sa_name(msg.pgn, msg.sa)
            if col == 3: return str(msg.sa)
            if col == 4: return str(msg.priority)
            if col == 5: return str(len(msg.payload))
            if col == 6: return msg.payload.hex(" ").upper()
            if col == 7: return str(entry.count)
            if col == 8:
                ms = entry.interval_ms
                return f"{ms:.0f} ms" if ms is not None else "—"
            return None

        flag = _flag_for_message(self._store, msg)

        if role == Qt.ItemDataRole.ToolTipRole:
            reason = _reason_for_message(msg)
            if reason:
                return reason

        if role == Qt.ItemDataRole.ForegroundRole:
            if flag == "ignore":    return _COLOR_IGNORE
            if flag == "highlight": return QColor(0, 0, 0)
            if flag == "error":     return _COLOR_ERROR_FG

        if role == Qt.ItemDataRole.BackgroundRole:
            if flag == "highlight": return _COLOR_HIGHLIGHT
            if flag == "warning":   return _COLOR_WARNING
            if flag == "error":     return _COLOR_ERROR

        return None

    def refresh(self) -> None:
        self._keys_cache = None
        self.layoutChanged.emit()

    def reset(self) -> None:
        self._keys_cache = None
        self.layoutChanged.emit()


class N2KTab(QWidget):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store       = store
        self._time_model  = N2KTimeViewModel(store)
        self._accum_model = N2KAccumViewModel(store)
        self._held: int | None = None   # frames-in-queue held back while paused

        self._view = QTableView()
        self._view.setModel(self._time_model)
        hdr = self._view.horizontalHeader()
        hdr.setResizeContentsPrecision(50)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        QTimer.singleShot(0, self._enable_autosize)
        self._view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._view.verticalHeader().setVisible(False)

        self._group_combo = QComboBox()
        self._group_combo.addItems(_GROUP_OPTIONS)
        self._group_combo.currentIndexChanged.connect(self._on_group_selected)

        self._count_label = QLabel("0 messages")

        top = QHBoxLayout()
        top.addWidget(QLabel("Group by:"))
        top.addWidget(self._group_combo)
        top.addStretch()
        top.addWidget(self._count_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._view)

        # Default selection: group-by first option. Fires _on_group_selected,
        # which swaps the view model to accum.
        self._group_combo.setCurrentIndex(1)

    def _enable_autosize(self) -> None:
        hdr = self._view.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def _on_group_selected(self, index: int) -> None:
        if index == 0:
            self._view.setModel(self._time_model)
        else:
            self._accum_model.refresh()
            self._view.setModel(self._accum_model)
        self._update_count()

    def _update_count(self) -> None:
        total = len(self._store.n2k_messages)
        rate  = self._store.n2k_msg_rate()
        rate_str = f"{rate:,.0f}/s" if rate is not None else "—/s"
        held_str = f"  [{self._held:,} frames held]" if self._held is not None else ""
        if self._view.model() is self._time_model:
            self._count_label.setText(f"{total:,} messages  ({rate_str}){held_str}")
        else:
            rows = self._accum_model.rowCount()
            self._count_label.setText(f"{rows:,} rows  ({total:,} messages, {rate_str}){held_str}")

    def set_held(self, held: int | None) -> None:
        """Tell the tab how many frames are held in the input queue (paused)."""
        if held == self._held:
            return
        self._held = held
        self._update_count()

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

    def has_warnings(self) -> bool:
        return (self._store.incomplete_msg_count + self._store.undecoded_msg_count) > 0
