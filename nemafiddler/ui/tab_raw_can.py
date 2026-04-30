from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import (
    QAbstractTableModel, QModelIndex, Qt,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget,
    QHeaderView,
)

from nemafiddler.core.n2k import parse as n2k_parse, pgn_name
from nemafiddler.core.store import DataStore, Priority

# (header label, tooltip)
_COLS_TIME: list[tuple[str, str]] = [
    ("Time",    "Timestamp when the frame was received by the PC."),
    ("Arb ID",  "Full CAN arbitration ID (hex). 11 bits for standard frames, 29 bits for extended frames."),
    ("Type",    "Frame format: Std = standard (11-bit ID), Ext = extended (29-bit ID)."),
    ("SRR",     "Substitute Remote Request bit.\nOnly present in extended frames — occupies the RTR bit position of the base 11-bit ID.\nAlways 1 in extended frames; not present (—) in standard frames."),
    ("IDE",     "ID Extension bit.\n1 = extended frame (29-bit ID), 0 = standard frame (11-bit ID)."),
    ("RTR",     "Remote Transmission Request bit.\n1 = remote frame (requests data without sending any), 0 = data frame.\nAlways 0 on NMEA 2000."),
    ("DLC",     "Data Length Code (0–8). Values 9–15 are valid on the wire in classic CAN but still mean 8 data bytes."),
    ("Data",    "Payload bytes (hex). Up to 8 bytes for classic CAN.\nUnused bytes are 0xFF per NMEA 2000 convention."),
    ("CRC",     "Cyclic Redundancy Check result.\nNot reported by the Waveshare adapter — only frames that passed hardware CRC are forwarded.\nOther adapters may report this field."),
    (" ",       "< CAN | NMEA >"),
    ("PGN",     "Parameter Group Number — NMEA 2000 message type.\nOnly present on extended-ID frames.\nDerived from bits 24–8 of the arbitration ID."),
    ("SA",      "Source Address — NMEA 2000 device address (0–253).\nBits 7–0 of the arbitration ID.\nNot a CAN field; part of the NMEA 2000 addressing layer."),
    ("Priority","NMEA 2000 message priority (0 = highest, 7 = lowest).\nBits 28–26 of the arbitration ID.\nNot a CAN field; part of the NMEA 2000 addressing layer."),
]

_COLS_ACCUM: list[tuple[str, str]] = [
    ("Last Time", "Timestamp of the most recent frame."),
    ("Arb ID",    "Full CAN arbitration ID (hex). 11 bits for standard frames, 29 bits for extended frames."),
    ("Type",      "Frame format: Std = standard (11-bit ID), Ext = extended (29-bit ID)."),
    ("SRR",       "Substitute Remote Request bit.\nAlways 1 in extended frames; not present (—) in standard frames."),
    ("IDE",       "ID Extension bit. 1 = extended frame, 0 = standard frame."),
    ("RTR",       "Remote Transmission Request bit. 1 = remote frame, 0 = data frame.\nAlways 0 on NMEA 2000."),
    ("DLC",       "Data Length Code of the most recent frame."),
    ("Last Data", "Payload bytes of the most recent frame (hex)."),
    ("CRC",       "Cyclic Redundancy Check. Not reported by the Waveshare adapter."),
    (" ",         "< CAN | NMEA >"),
    ("PGN",       "Parameter Group Number (NMEA 2000 message type). Empty for non-N2K frames."),
    ("SA",        "NMEA 2000 Source Address (0–253). Empty for non-N2K frames."),
    ("Priority",  "NMEA 2000 message priority (0 = highest, 7 = lowest). Empty for non-N2K frames."),
    ("Count",     "Number of frames received with this arbitration ID since the session started."),
    ("Interval",  "Average interval between frames (ms) = (last − first timestamp) / (count − 1).\nRequires at least 2 frames. Converges over time — early readings may be inaccurate."),
]

_SEP_COL_ACCUM = 9

_SEP_COL = 9   # index of the "|" separator column in _COLS_TIME

_COLOR_IGNORE    = QColor(160, 160, 160)
_COLOR_HIGHLIGHT = QColor(255, 200,  60)
_COLOR_NON_N2K   = QColor(255, 140,  40)
_COLOR_ERROR_BG  = QColor(200,  40,  40)
_COLOR_ERROR_FG  = QColor(255, 255, 255)
_COLOR_SEP_BG    = QColor(210, 210, 210)


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
        if orientation != Qt.Orientation.Horizontal:
            return None
        label, tip = _COLS_TIME[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return label
        if role == Qt.ItemDataRole.ToolTipRole and tip:
            return tip
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        col = index.column()

        # Separator column
        if col == _SEP_COL:
            if role == Qt.ItemDataRole.BackgroundRole:
                return _COLOR_SEP_BG
            return None

        row = len(self._store.frames) - 1 - index.row()
        if row < 0 or row >= len(self._store.frames):
            return None
        frame = self._store.frames[row]
        n2k   = n2k_parse(frame)

        if role == Qt.ItemDataRole.DisplayRole:
            if frame.is_error:
                if col == 0:
                    return _fmt_time(frame.timestamp)
                if col == 1:
                    return "ERROR"
                if col == 7:
                    return frame.data.rstrip(b"\x00").decode("ascii", errors="replace")
                return ""
            if col == 0:  return _fmt_time(frame.timestamp)
            if col == 1:  return f"{frame.arbitration_id:08X}"
            if col == 2:  return "Ext" if frame.is_extended_id else "Std"
            if col == 3:  return "1" if frame.is_extended_id else "—"
            if col == 4:  return "1" if frame.is_extended_id else "0"
            if col == 5:  return "1" if frame.is_remote_frame else "0"
            if col == 6:  return str(frame.dlc)
            if col == 7:  return frame.data[:min(frame.dlc, 8)].hex(" ").upper()
            if col == 8:  return "N/A"
            if col == 10: return f"{n2k.pgn}  {pgn_name(n2k.pgn)}" if n2k else ""
            if col == 11: return str(n2k.sa) if n2k else ""
            if col == 12: return str(n2k.priority) if n2k else ""

        if frame.is_error:
            if role == Qt.ItemDataRole.BackgroundRole:
                return _COLOR_ERROR_BG
            if role == Qt.ItemDataRole.ForegroundRole:
                return _COLOR_ERROR_FG
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            flag = self._flag_for(frame, n2k)
            if flag == "ignore":    return _COLOR_IGNORE
            if flag == "highlight": return QColor(0, 0, 0)

        if role == Qt.ItemDataRole.BackgroundRole:
            flag = self._flag_for(frame, n2k)
            if flag == "highlight": return _COLOR_HIGHLIGHT
            if n2k is None:         return _COLOR_NON_N2K

        return None

    def _flag_for(self, frame, n2k) -> Priority:
        if n2k is not None:
            f = self._store.get_flag_by_pgn(n2k.pgn)
            if f:
                return f
        return self._store.get_flag_by_arb(frame.arbitration_id)

    def refresh(self) -> None:
        self.layoutChanged.emit()


_GROUP_MODES = ["by Arb ID", "by PGN + SA", "by PGN"]


class AccumViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._mode  = 0   # index into _GROUP_MODES

    def set_mode(self, mode_index: int) -> None:
        self._mode = mode_index
        self.layoutChanged.emit()

    def _active_dict(self) -> dict:
        if self._mode == 1: return self._store.by_pgn_sa
        if self._mode == 2: return self._store.by_pgn
        return self._store.by_arb_id

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._active_dict())

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLS_ACCUM)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        label, tip = _COLS_ACCUM[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return label
        if role == Qt.ItemDataRole.ToolTipRole and tip:
            return tip
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        d = self._active_dict()
        keys = list(d.keys())
        if index.row() >= len(keys):
            return None
        entry = d[keys[index.row()]]
        n2k   = entry.last_n2k
        col   = index.column()

        if col == _SEP_COL_ACCUM:
            if role == Qt.ItemDataRole.BackgroundRole:
                return _COLOR_SEP_BG
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            f = entry.last_frame
            if col == 0:  return _fmt_time(f.timestamp)
            if col == 1:  return f"{f.arbitration_id:08X}"
            if col == 2:  return "Ext" if f.is_extended_id else "Std"
            if col == 3:  return "1" if f.is_extended_id else "—"
            if col == 4:  return "1" if f.is_extended_id else "0"
            if col == 5:  return "1" if f.is_remote_frame else "0"
            if col == 6:  return str(f.dlc)
            if col == 7:  return f.data[:min(f.dlc, 8)].hex(" ").upper()
            if col == 8:  return "N/A"
            if col == 10: return f"{n2k.pgn}  {pgn_name(n2k.pgn)}" if n2k else ""
            if col == 11: return str(n2k.sa) if n2k else ""
            if col == 12: return str(n2k.priority) if n2k else ""
            if col == 13: return str(entry.count)
            if col == 14:
                ms = entry.interval_ms
                return f"{ms:.0f} ms" if ms is not None else "—"

        if role == Qt.ItemDataRole.ForegroundRole:
            flag = self._flag_for(entry, n2k)
            if flag == "ignore": return _COLOR_IGNORE

        if role == Qt.ItemDataRole.BackgroundRole:
            flag = self._flag_for(entry, n2k)
            if flag == "highlight": return _COLOR_HIGHLIGHT
            if n2k is None:         return _COLOR_NON_N2K

        return None

    def _flag_for(self, entry, n2k) -> Priority:
        if n2k is not None:
            f = self._store.get_flag_by_pgn(n2k.pgn)
            if f:
                return f
        return self._store.get_flag_by_arb(entry.last_frame.arbitration_id)

    def refresh(self) -> None:
        self.layoutChanged.emit()


class RawCanTab(QWidget):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store       = store
        self._time_model  = TimeViewModel(store)
        self._accum_model = AccumViewModel(store)

        self._min_col_widths: dict[int, int] = {}

        self._view = QTableView()
        self._view.setModel(self._time_model)
        hdr = self._view.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_SEP_COL, QHeaderView.ResizeMode.Fixed)
        hdr.sectionResized.connect(self._on_section_resized)
        self._view.setColumnWidth(_SEP_COL, 8)
        self._view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._view.verticalHeader().setVisible(False)

        self._btn = QPushButton("Accumulated view")
        self._btn.setCheckable(True)
        self._btn.toggled.connect(self._toggle_mode)

        self._group_combo = QComboBox()
        self._group_combo.addItems(_GROUP_MODES)
        self._group_combo.setEnabled(False)
        self._group_combo.currentIndexChanged.connect(self._on_group_changed)

        self._count_label = QLabel("0 frames")

        top = QHBoxLayout()
        top.addWidget(self._btn)
        top.addWidget(self._group_combo)
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
            self._group_combo.setEnabled(True)
            self._pin_sep(_SEP_COL_ACCUM)
        else:
            self._view.setModel(self._time_model)
            self._btn.setText("Accumulated view")
            self._group_combo.setEnabled(False)
            self._pin_sep(_SEP_COL)
        self._update_count()

    def _on_section_resized(self, col: int, _old_size: int, new_size: int) -> None:
        minimum = self._min_col_widths.get(col, 0)
        if new_size >= minimum:
            self._min_col_widths[col] = new_size
        else:
            hdr = self._view.horizontalHeader()
            hdr.blockSignals(True)
            self._view.setColumnWidth(col, minimum)
            hdr.blockSignals(False)

    def _on_group_changed(self, index: int) -> None:
        self._min_col_widths.clear()
        self._accum_model.set_mode(index)
        self._pin_sep(_SEP_COL_ACCUM)
        self._update_count()

    def _pin_sep(self, col: int) -> None:
        hdr = self._view.horizontalHeader()
        hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self._view.setColumnWidth(col, 8)

    def _update_count(self) -> None:
        model = self._view.model()
        if model is self._time_model:
            self._count_label.setText(f"{len(self._store.frames):,} frames")
        else:
            rows = self._accum_model.rowCount()
            total = len(self._store.frames)
            self._count_label.setText(f"{rows:,} rows  ({total:,} frames)")

    def on_frames_added(self) -> None:
        model = self._view.model()
        model.refresh()  # type: ignore[attr-defined]
        self._update_count()
        if model is self._time_model:
            self._view.scrollToTop()
