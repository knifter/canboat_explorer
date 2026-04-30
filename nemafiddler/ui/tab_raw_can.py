from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import (
    QAbstractTableModel, QModelIndex, Qt, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget,
    QHeaderView,
)

from nemafiddler.n2k import parse as n2k_parse, pgn_name
from nemafiddler.store import DataStore, Priority

_COLS_TIME   = ["Time", "Arb ID", "PGN", "SA", "Prio", "DLC", "Data"]
_COLS_ACCUM  = ["Arb ID", "PGN", "SA", "Count", "DLC", "Last Data", "Last Time"]

_COLOR_IGNORE    = QColor(160, 160, 160)
_COLOR_HIGHLIGHT = QColor(255, 200,  60)
_COLOR_NON_N2K   = QColor(255, 140,  40)
_COLOR_ERROR_BG  = QColor(200,  40,  40)
_COLOR_ERROR_FG  = QColor(255, 255, 255)


def _fmt_time(ts: float) -> str:
    t = time.localtime(ts)
    ms = int((ts % 1) * 1000)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"


class TimeViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._store.frames)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLS_TIME)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLS_TIME[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        # Newest frame first
        row = len(self._store.frames) - 1 - index.row()
        if row < 0 or row >= len(self._store.frames):
            return None
        frame = self._store.frames[row]
        n2k   = n2k_parse(frame)
        col   = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if frame.is_error:
                if col == 0:
                    return _fmt_time(frame.timestamp)
                if col == 1:
                    return "ERROR"
                if col == 6:
                    return frame.data.rstrip(b"\x00").decode("ascii", errors="replace")
                return ""
            if col == 0:
                return _fmt_time(frame.timestamp)
            if col == 1:
                return f"{frame.arbitration_id:08X}"
            if col == 2:
                return str(n2k.pgn) if n2k else ""
            if col == 3:
                return str(n2k.sa)  if n2k else ""
            if col == 4:
                return str(n2k.priority) if n2k else ""
            if col == 5:
                return str(frame.dlc)
            if col == 6:
                return frame.data[:frame.dlc].hex(" ").upper()

        if frame.is_error:
            if role == Qt.ItemDataRole.BackgroundRole:
                return _COLOR_ERROR_BG
            if role == Qt.ItemDataRole.ForegroundRole:
                return _COLOR_ERROR_FG
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            flag = self._flag_for(frame, n2k)
            if flag == "ignore":
                return _COLOR_IGNORE
            if flag == "highlight":
                return QColor(0, 0, 0)

        if role == Qt.ItemDataRole.BackgroundRole:
            flag = self._flag_for(frame, n2k)
            if flag == "highlight":
                return _COLOR_HIGHLIGHT
            if n2k is None:
                return _COLOR_NON_N2K

        return None

    def _flag_for(self, frame, n2k) -> Priority:
        if n2k is not None:
            f = self._store.get_flag_by_pgn(n2k.pgn)
            if f:
                return f
        return self._store.get_flag_by_arb(frame.arbitration_id)

    def refresh(self) -> None:
        self.layoutChanged.emit()


class AccumViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._keys: list[int] = []   # arb_id list, stable order

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._store.by_arb_id)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLS_ACCUM)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLS_ACCUM[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        keys = list(self._store.by_arb_id.keys())
        if index.row() >= len(keys):
            return None
        arb_id = keys[index.row()]
        entry  = self._store.by_arb_id[arb_id]
        n2k    = entry.last_n2k
        col    = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return f"{arb_id:08X}"
            if col == 1:
                return f"{n2k.pgn} {pgn_name(n2k.pgn)}" if n2k else ""
            if col == 2:
                return str(n2k.sa) if n2k else ""
            if col == 3:
                return str(entry.count)
            if col == 4:
                return str(entry.last_frame.dlc)
            if col == 5:
                f = entry.last_frame
                return f.data[:f.dlc].hex(" ").upper()
            if col == 6:
                return _fmt_time(entry.last_frame.timestamp)

        if role == Qt.ItemDataRole.ForegroundRole:
            flag = self._flag_for(arb_id, n2k)
            if flag == "ignore":
                return _COLOR_IGNORE

        if role == Qt.ItemDataRole.BackgroundRole:
            flag = self._flag_for(arb_id, n2k)
            if flag == "highlight":
                return _COLOR_HIGHLIGHT
            if n2k is None:
                return _COLOR_NON_N2K

        return None

    def _flag_for(self, arb_id, n2k) -> Priority:
        if n2k is not None:
            f = self._store.get_flag_by_pgn(n2k.pgn)
            if f:
                return f
        return self._store.get_flag_by_arb(arb_id)

    def refresh(self) -> None:
        self.layoutChanged.emit()


class RawCanTab(QWidget):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store       = store
        self._time_model  = TimeViewModel(store)
        self._accum_model = AccumViewModel(store)

        self._view = QTableView()
        self._view.setModel(self._time_model)
        self._view.horizontalHeader().setStretchLastSection(True)
        self._view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._view.verticalHeader().setVisible(False)

        self._btn = QPushButton("Accumulated view")
        self._btn.setCheckable(True)
        self._btn.toggled.connect(self._toggle_mode)

        self._count_label = QLabel("0 frames")

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
            self._view.setModel(self._accum_model)
            self._btn.setText("Time view")
        else:
            self._view.setModel(self._time_model)
            self._btn.setText("Accumulated view")

    def on_frames_added(self) -> None:
        model = self._view.model()
        model.refresh()  # type: ignore[attr-defined]
        n = len(self._store.frames)
        self._count_label.setText(f"{n:,} frames")
        # Auto-scroll to top only in time view
        if model is self._time_model:
            self._view.scrollToTop()
