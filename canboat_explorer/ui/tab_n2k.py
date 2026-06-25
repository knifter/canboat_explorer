from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import (
    QAbstractTableModel, QItemSelection, QItemSelectionModel, QModelIndex,
    Qt, QTimer,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QTableView, QVBoxLayout,
    QWidget, QHeaderView,
)

from canboat_explorer.core.fast_packet import N2KMessage
from canboat_explorer.core.filter import FilterSpec
from canboat_explorer.core.store import DataStore, Priority

_COLOR_IGNORE    = QColor(160, 160, 160)
_COLOR_HIGHLIGHT = QColor(240, 190,  50)
_COLOR_WARNING   = QColor(195, 130,  80)
_COLOR_ERROR     = QColor(185,  75,  75)
_COLOR_ERROR_FG  = QColor(255, 255, 255)

_COLS_TIME: list[tuple[str, str]] = [
    ("Time",         "Timestamp of the first CAN frame of this message."),
    ("PGN",          "Parameter Group Number — NMEA 2000 message type."),
    ("PGN Name",     "Human-readable name for this PGN, if known."),
    ("SA",           "Source Address — NMEA 2000 device address (0–253)."),
    ("Dst",          "Destination Address for addressed (PDU1) messages; blank for broadcast (PDU2)."),
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
    ("Dst",          "Destination Address for addressed (PDU1) messages; blank for broadcast (PDU2)."),
    ("Priority",     "NMEA 2000 message priority (0 = highest, 7 = lowest)."),
    ("Length",       "Payload length of the most recent message in bytes."),
    ("Last Payload", "Assembled payload bytes of the most recent message (hex)."),
    ("Count",        "Number of messages received for this PGN + SA combination."),
    ("Interval",     "Average interval between messages (ms), over the last 25 messages.\nRequires at least 2 messages."),
]

_GROUP_OPTIONS = ["None", "PGN + SA"]
# Index 0 = time view (no grouping); 1 = accumulated by PGN + SA

# The (Last) Payload column — same index in both _COLS_TIME and _COLS_ACCUM.
# It stretches to absorb spare width and is the first to give way when the
# window is too narrow, so the trailing columns (Count/Interval) stay visible.
_PAYLOAD_COL = 7


def _fmt_time(ts: float) -> str:
    t = time.localtime(ts)
    ms = int((ts % 1) * 1000)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"


def _apply_selection_band(view: QTableView) -> None:
    """Force a flat, continuous selection band over the whole row.

    The Qt 6 Windows 11 style draws each selected cell as a separate rounded
    pill — and it reads selection from the view's selection model, not from the
    delegate's ``option.state``, so a delegate cannot talk it out of it. The
    gaps between those pills are the vertical "bars". Overriding the selected
    item background via stylesheet replaces the pills with one flat fill. The
    alpha is kept high enough to cover the grid lines underneath (a more
    translucent fill lets them bleed through as bars again) while still tinting
    rather than fully hiding the row's own / flag colour.
    """
    hl = view.palette().highlight().color()
    view.setStyleSheet(
        f"QTableView::item:selected {{ background-color: "
        f"rgba({hl.red()}, {hl.green()}, {hl.blue()}, 180); }}"
    )


class N2KTimeViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._filter_spec    = FilterSpec()
        self._filter_active  = False
        self._filter_visible: list[int] = []       # store indices passing the filter (ascending)
        self._filter_processed = 0

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._filter_visible) if self._filter_active else len(self._store.n2k_messages)

    def unfiltered_count(self) -> int:
        return len(self._store.n2k_messages)

    def _store_index(self, view_row: int) -> int:
        if self._filter_active:
            return self._filter_visible[len(self._filter_visible) - 1 - view_row]
        return len(self._store.n2k_messages) - 1 - view_row

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
        row = self._store_index(index.row())
        if row < 0 or row >= len(self._store.n2k_messages):
            return None
        msg = self._store.n2k_messages[row]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0: return _fmt_time(msg.timestamp)
            if col == 1: return str(msg.pgn)
            if col == 2: return self._store.pgn_sa_name(msg.pgn, msg.sa)
            if col == 3: return str(msg.sa)
            if col == 4: return str(msg.dst) if msg.dst is not None else ""
            if col == 5: return str(msg.priority)
            if col == 6: return str(len(msg.payload))
            if col == 7: return msg.payload.hex(" ").upper()
            if col == 8: return str(msg.frame_count)
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

    def _passes_filter(self, i: int) -> bool:
        msg  = self._store.n2k_messages[i]
        flag = _flag_for_message(self._store, msg)
        return _msg_matches_filter(self._filter_spec, self._store, msg, flag)

    def set_filter(self, spec: FilterSpec) -> None:
        self._filter_spec   = spec
        self._filter_active = not spec.is_empty
        self._rebuild_filter()
        self.layoutChanged.emit()

    def _rebuild_filter(self) -> None:
        n = len(self._store.n2k_messages)
        self._filter_visible   = [i for i in range(n) if self._passes_filter(i)] if self._filter_active else []
        self._filter_processed = n

    def refresh(self) -> None:
        n = len(self._store.n2k_messages)
        if self._filter_processed > n:               # store reset / shrunk under us
            self._rebuild_filter()
        elif self._filter_active:                    # incrementally classify new messages
            for i in range(self._filter_processed, n):
                if self._passes_filter(i):
                    self._filter_visible.append(i)
            self._filter_processed = n
        else:
            self._filter_processed = n
        self.layoutChanged.emit()

    def reset(self) -> None:
        self._rebuild_filter()
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
        return f"PGN {msg.pgn}: nmea2000 decoder could not parse this message"
    return ""


def _msg_matches_filter(spec: FilterSpec, store: DataStore, msg: N2KMessage, flag: Priority) -> bool:
    """Test an NMEA-2000 message row against the global filter; flag booleans
    come from the already-computed effective flag so they match what's painted."""
    return spec.matches(
        pgn=msg.pgn, sa=msg.sa,
        text=store.pgn_sa_name(msg.pgn, msg.sa),
        is_warning=(flag == "warning"),
        is_error=(flag == "error"),
        is_highlighted=(flag == "highlight"),
        is_ignored=(flag == "ignore"),
    )


class N2KAccumViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._keys_cache: list | None = None
        self._filter_spec = FilterSpec()

    def set_filter(self, spec: FilterSpec) -> None:
        self._filter_spec = spec
        self._keys_cache = None
        self.layoutChanged.emit()

    def _passes_filter_entry(self, entry) -> bool:
        msg  = entry.last_msg
        flag = _flag_for_message(self._store, msg)
        return _msg_matches_filter(self._filter_spec, self._store, msg, flag)

    def _keys(self) -> list:
        if self._keys_cache is None:
            d = self._store.n2k_by_pgn_sa
            if self._filter_spec.is_empty:
                self._keys_cache = list(d.keys())
            else:
                self._keys_cache = [k for k, e in d.items() if self._passes_filter_entry(e)]
        return self._keys_cache

    def unfiltered_count(self) -> int:
        return len(self._store.n2k_by_pgn_sa)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._keys())

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
            if col == 4: return str(msg.dst) if msg.dst is not None else ""
            if col == 5: return str(msg.priority)
            if col == 6: return str(len(msg.payload))
            if col == 7: return msg.payload.hex(" ").upper()
            if col == 8: return str(entry.count)
            if col == 9:
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
        self._displayed_count = 0       # time-view row count at last refresh (scroll anchor)

        self._view = QTableView()
        self._view.setModel(self._time_model)
        hdr = self._view.horizontalHeader()
        hdr.setResizeContentsPrecision(50)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        QTimer.singleShot(0, self._enable_autosize)
        self._view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._view.verticalHeader().setVisible(False)
        _apply_selection_band(self._view)
        self._view.doubleClicked.connect(self._copy_cell)

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
        model = self._view.model()
        if model is None:
            return
        for c in range(model.columnCount()):
            mode = (QHeaderView.ResizeMode.Stretch if c == _PAYLOAD_COL
                    else QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(c, mode)
        hdr.setStretchLastSection(False)

    def _on_group_selected(self, index: int) -> None:
        if index == 0:
            self._view.setModel(self._time_model)
        else:
            self._accum_model.refresh()
            self._view.setModel(self._accum_model)
        self._enable_autosize()
        self._update_count()

    def _update_count(self) -> None:
        total = len(self._store.n2k_messages)
        rate  = self._store.n2k_msg_rate()
        rate_str = f"{rate:,.0f}/s" if rate is not None else "—/s"
        held_str = f"  [{self._held:,} frames held]" if self._held is not None else ""
        if self._view.model() is self._time_model:
            shown = self._time_model.rowCount()
            msgs_str = (f"{shown:,} of {total:,} messages" if shown != total
                        else f"{total:,} messages")
            self._count_label.setText(f"{msgs_str}  ({rate_str}){held_str}")
        else:
            shown      = self._accum_model.rowCount()
            rows_total = self._accum_model.unfiltered_count()
            rows_str = (f"{shown:,} of {rows_total:,} rows" if shown != rows_total
                        else f"{shown:,} rows")
            self._count_label.setText(f"{rows_str}  ({total:,} messages, {rate_str}){held_str}")

    def set_held(self, held: int | None) -> None:
        """Tell the tab how many frames are held in the input queue (paused)."""
        if held == self._held:
            return
        self._held = held
        self._update_count()

    def _copy_cell(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text is None:
            return
        QApplication.clipboard().setText(str(text))

    def on_messages_added(self) -> None:
        model = self._view.model()
        if model is self._time_model:
            at_top   = self._view.verticalScrollBar().value() == 0
            top_row  = self._view.rowAt(0)
            prev     = self._displayed_count
            sel_rows = [ix.row() for ix in self._view.selectionModel().selectedRows()]
            cur_row  = self._view.currentIndex().row()
            self._time_model.refresh()
            self._displayed_count = self._time_model.rowCount()
            added = self._displayed_count - prev
            if added > 0:
                # New rows are prepended at the top; shift the selection and the
                # scroll anchor down by that many rows so both keep tracking the
                # same message as it moves down.
                self._reanchor_selection(self._time_model, sel_rows, cur_row, added)
            if at_top or top_row < 0:
                self._view.scrollToTop()
            elif added > 0:
                self._view.scrollTo(
                    self._time_model.index(top_row + added, 0),
                    QTableView.ScrollHint.PositionAtTop,
                )
        else:
            self._accum_model.refresh()
        self._update_count()

    def _reanchor_selection(self, model, sel_rows, cur_row, added) -> None:
        if not sel_rows and cur_row < 0:
            return
        sm       = self._view.selectionModel()
        last_col = model.columnCount() - 1
        if sel_rows:
            new_sel = QItemSelection()
            for r in sel_rows:
                new_sel.select(model.index(r + added, 0),
                               model.index(r + added, last_col))
            sm.select(new_sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if cur_row >= 0:
            sm.setCurrentIndex(model.index(cur_row + added, 0),
                               QItemSelectionModel.SelectionFlag.NoUpdate)

    def apply_filter(self, spec: FilterSpec) -> None:
        self._time_model.set_filter(spec)
        self._accum_model.set_filter(spec)
        self._displayed_count = self._time_model.rowCount()
        self._view.scrollToTop()
        self._update_count()

    def filter_counts(self) -> tuple[int, int]:
        if self._view.model() is self._time_model:
            return self._time_model.rowCount(), self._time_model.unfiltered_count()
        return self._accum_model.rowCount(), self._accum_model.unfiltered_count()

    def reset(self) -> None:
        self._time_model.reset()
        self._accum_model.reset()
        self._displayed_count = self._time_model.rowCount()
        self._update_count()

    def has_warnings(self) -> bool:
        return (self._store.incomplete_msg_count + self._store.undecoded_msg_count) > 0

    def warning_text(self) -> str:
        inc = self._store.incomplete_msg_count
        und = self._store.undecoded_msg_count
        if inc + und == 0:
            return ""
        parts = []
        if inc:
            parts.append(f"{inc:,} incomplete fast-packet sequence(s)")
        if und:
            parts.append(f"{und:,} message(s) the decoder could not parse")
        return ("Since capture started: " + "; ".join(parts) + ".\n"
                "Counts are cumulative for the session. The grouped view shows "
                "only the latest message per PGN+SA, so a flagged message may not "
                "be visible — switch Group by to the time view to find it.")
