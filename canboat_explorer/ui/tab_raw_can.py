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

from canboat_explorer.core.filter import FilterSpec
from canboat_explorer.core.n2k_frame_info import n2k_frame_info
from canboat_explorer.core.store import DataStore, Priority

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
    ("EDP",     "Extended Data Page (bit 25 of the arbitration ID).\nAlways 0 for NMEA 2000 — a 1 marks a non-N2K extended frame (ISO 15765 / J1939 extended data page)."),
    ("DP",      "Data Page (bit 24 of the arbitration ID).\nThe high bit of the PGN; 1 selects the proprietary data page."),
    ("PF",      "PDU Format (bits 23–16 of the arbitration ID).\n< 240 = PDU1 (addressed: PS is the destination address); ≥ 240 = PDU2 (broadcast: PS is the group extension, folded into the PGN)."),
    ("PS",      "PDU Specific (bits 15–8 of the arbitration ID).\nDestination address when PDU1 (PF < 240); group extension when PDU2 (PF ≥ 240)."),
    ("SA",      "Source Address — NMEA 2000 device address (0–253).\nBits 7–0 of the arbitration ID.\nNot a CAN field; part of the NMEA 2000 addressing layer."),
    ("Dst",     "Destination Address (bits 15–8 of the arbitration ID).\nOnly present on addressed (PDU1) PGNs; blank for broadcast (PDU2) and non-N2K frames."),
    ("Priority","NMEA 2000 message priority (0 = highest, 7 = lowest).\nBits 28–26 of the arbitration ID.\nNot a CAN field; part of the NMEA 2000 addressing layer."),
    ("Seq",     "Fast-packet sequence counter (bits 7–5 of byte 0).\nTies CAN frames of the same logical multi-frame message together.\nEmpty for non-N2K frames."),
    ("Frame",   "Fast-packet frame index (bits 4–0 of byte 0).\n0 = first frame, 1+ = continuation frames.\nEmpty for non-N2K frames."),
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
    ("EDP",       "Extended Data Page (arbitration bit 25). 0 for NMEA 2000; 1 = non-N2K extended frame."),
    ("DP",        "Data Page (arbitration bit 24) — high bit of the PGN."),
    ("PF",        "PDU Format (arbitration bits 23–16). < 240 = PDU1 (addressed); ≥ 240 = PDU2 (broadcast)."),
    ("PS",        "PDU Specific (arbitration bits 15–8). Destination address if PDU1; group extension if PDU2."),
    ("SA",        "NMEA 2000 Source Address (0–253). Empty for non-N2K frames."),
    ("Dst",       "NMEA 2000 Destination Address for addressed (PDU1) PGNs. Empty for broadcast / non-N2K frames."),
    ("Priority",  "NMEA 2000 message priority (0 = highest, 7 = lowest). Empty for non-N2K frames."),
    ("Seq",       "Fast-packet sequence counter (bits 7–5 of byte 0). Empty for non-N2K frames."),
    ("Frame",     "Fast-packet frame index (bits 4–0 of byte 0). Empty for non-N2K frames."),
    ("Count",     "Number of frames received with this arbitration ID since the session started."),
    ("Interval",  "Average interval between frames (ms), over the last 25 frames.\nRequires at least 2 frames."),
]

_SEP_COL       = 9    # index of the "|" separator column in _COLS_TIME
_SEP_COL_ACCUM = 9

_COLOR_IGNORE    = QColor(160, 160, 160)
_COLOR_HIGHLIGHT = QColor(240, 190,  50)
_COLOR_WARNING   = QColor(210, 150,  60)
_COLOR_ERROR     = QColor(185,  75,  75)
_COLOR_ERROR_FG  = QColor(255, 255, 255)
_COLOR_SEP_BG    = QColor(210, 210, 210)


def _fmt_time(ts: float) -> str:
    t = time.localtime(ts)
    ms = int((ts % 1) * 1000)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"


def _row_matches_filter(spec: FilterSpec, store: DataStore, frame, n2k, flag: Priority) -> bool:
    """Test a Raw-CAN row against the global filter. The flag booleans are
    derived from the already-computed effective flag so they match what's
    painted; non-N2K frames pass PGN/SA/text through (None / "")."""
    if n2k is not None:
        pgn, sa = n2k.pgn, n2k.sa
        text    = store.pgn_sa_name(pgn, sa)
    else:
        pgn = sa = None
        text = ""
    return spec.matches(
        pgn=pgn, sa=sa, text=text,
        is_warning=(flag == "warning"),
        is_error=(flag == "error" or frame.is_error_frame),
        is_highlighted=(flag == "highlight"),
        is_ignored=(flag == "ignore"),
    )


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


class TimeViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._filter_spec    = FilterSpec()
        self._filter_active  = False               # False = no filter; use direct mapping
        self._filter_visible: list[int] = []       # store indices passing the filter (ascending)
        self._filter_processed = 0                 # store frames already classified

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._filter_visible) if self._filter_active else len(self._store.frames)

    def unfiltered_count(self) -> int:
        return len(self._store.frames)

    def _store_index(self, view_row: int) -> int:
        """Map a display row (0 = newest) to an index into store.frames."""
        if self._filter_active:
            return self._filter_visible[len(self._filter_visible) - 1 - view_row]
        return len(self._store.frames) - 1 - view_row

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

        row = self._store_index(index.row())
        if row < 0 or row >= len(self._store.frames):
            return None
        frame = self._store.frames[row]
        n2k   = n2k_frame_info(frame)

        if role == Qt.ItemDataRole.DisplayRole:
            if frame.is_error_frame:
                if col == 0: return _fmt_time(frame.timestamp)
                if col == 1: return "ERROR"
                if col == 7: return bytes(frame.data).rstrip(b"\x00").decode("ascii", errors="replace")
                return ""
            if col == 0:  return _fmt_time(frame.timestamp)
            if col == 1:  return f"{frame.arbitration_id:08X}"
            if col == 2:  return "Ext" if frame.is_extended_id else "Std"
            if col == 3:  return "1" if frame.is_extended_id else "—"
            if col == 4:  return "1" if frame.is_extended_id else "0"
            if col == 5:  return "1" if frame.is_remote_frame else "0"
            if col == 6:  return str(frame.dlc)
            if col == 7:  return bytes(frame.data[:frame.dlc]).hex(" ").upper()
            if col == 8:  return "N/A"
            if col == 10: return f"{n2k.pgn}  {self._store.pgn_sa_name(n2k.pgn, n2k.sa)}" if n2k else ""
            if col == 11: return str(n2k.edp) if n2k else ""
            if col == 12: return str(n2k.dp)  if n2k else ""
            if col == 13: return str(n2k.pf)  if n2k else ""
            if col == 14: return str(n2k.ps)  if n2k else ""
            if col == 15: return str(n2k.sa) if n2k else ""
            if col == 16: return str(n2k.dst) if (n2k and n2k.dst is not None) else ""
            if col == 17: return str(n2k.priority) if n2k else ""
            if col == 18: return str(n2k.seq)       if (n2k and n2k.seq       is not None) else ""
            if col == 19: return str(n2k.frame_idx) if (n2k and n2k.frame_idx is not None) else ""

        if frame.is_error_frame:
            if role == Qt.ItemDataRole.BackgroundRole:
                return _COLOR_ERROR
            if role == Qt.ItemDataRole.ForegroundRole:
                return _COLOR_ERROR_FG
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            msg = frame.n2k_msg
            if msg is None:
                return "Frame did not participate in any assembled NMEA 2000 message (out-of-band)"
            if not msg.is_complete:
                return "Fast-packet sequence timed out before all frames arrived (incomplete)"
            if not msg.decoded:
                return "Assembled NMEA 2000 message — the nmea2000 library could not decode the payload"

        if role == Qt.ItemDataRole.ForegroundRole:
            flag = self._flag_for(frame, n2k)
            if flag == "ignore":    return _COLOR_IGNORE
            if flag == "highlight": return QColor(0, 0, 0)
            if flag == "error":     return _COLOR_ERROR_FG

        if role == Qt.ItemDataRole.BackgroundRole:
            flag = self._flag_for(frame, n2k)
            if flag == "highlight": return _COLOR_HIGHLIGHT
            if flag == "warning":   return _COLOR_WARNING
            if flag == "error":     return _COLOR_ERROR

        return None

    def _flag_for(self, frame, n2k) -> Priority:
        if n2k is not None:
            f = self._store.get_user_flag_by_pgn(n2k.pgn)
            if f:
                return f
        f = self._store.get_user_flag_by_arb(frame.arbitration_id)
        if f:
            return f
        msg = frame.n2k_msg
        if msg is None:
            return "warning"          # out-of-band: non-ext / short — never reached the library
        if not msg.is_complete:
            return "error"            # fast-packet timed out before completing
        if not msg.decoded:
            return "warning"          # assembled but the nmea2000 library could not decode it
        return None

    def _passes_filter(self, i: int) -> bool:
        frame = self._store.frames[i]
        n2k   = n2k_frame_info(frame)
        flag  = self._flag_for(frame, n2k)
        return _row_matches_filter(self._filter_spec, self._store, frame, n2k, flag)

    def set_filter(self, spec: FilterSpec) -> None:
        self._filter_spec   = spec
        self._filter_active = not spec.is_empty
        self._rebuild_filter()
        self.layoutChanged.emit()

    def _rebuild_filter(self) -> None:
        n = len(self._store.frames)
        self._filter_visible   = [i for i in range(n) if self._passes_filter(i)] if self._filter_active else []
        self._filter_processed = n

    def refresh(self) -> None:
        n = len(self._store.frames)
        if self._filter_processed > n:               # store reset / shrunk under us
            self._rebuild_filter()
        elif self._filter_active:                    # incrementally classify new frames
            for i in range(self._filter_processed, n):
                if self._passes_filter(i):
                    self._filter_visible.append(i)
            self._filter_processed = n
        else:
            self._filter_processed = n
        self.layoutChanged.emit()

    def reset(self) -> None:
        """Force a full reclassify (store contents replaced — old indices stale)."""
        self._rebuild_filter()
        self.layoutChanged.emit()


_GROUP_OPTIONS = ["None", "Arb ID", "PGN + SA", "PGN"]
# Index 0 = time view (no grouping); 1+ map to AccumViewModel.set_mode(index-1)


class AccumViewModel(QAbstractTableModel):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._mode  = 0   # index into _GROUP_MODES
        self._keys_cache: list | None = None
        self._filter_spec = FilterSpec()

    def set_mode(self, mode_index: int) -> None:
        self._mode = mode_index
        self._keys_cache = None
        self.layoutChanged.emit()

    def set_filter(self, spec: FilterSpec) -> None:
        self._filter_spec = spec
        self._keys_cache = None
        self.layoutChanged.emit()

    def _active_dict(self) -> dict:
        if self._mode == 1: return self._store.by_pgn_sa
        if self._mode == 2: return self._store.by_pgn
        return self._store.by_arb_id

    def _passes_filter_entry(self, entry) -> bool:
        frame = entry.last_frame
        n2k   = n2k_frame_info(frame)
        flag  = self._flag_for(frame, n2k)
        return _row_matches_filter(self._filter_spec, self._store, frame, n2k, flag)

    def _keys(self) -> list:
        if self._keys_cache is None:
            d = self._active_dict()
            if self._filter_spec.is_empty:
                self._keys_cache = list(d.keys())
            else:
                self._keys_cache = [k for k, e in d.items() if self._passes_filter_entry(e)]
        return self._keys_cache

    def unfiltered_count(self) -> int:
        return len(self._active_dict())

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
        if role == Qt.ItemDataRole.ToolTipRole and tip:
            return tip
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        d = self._active_dict()
        keys = self._keys()
        if index.row() >= len(keys):
            return None
        entry = d[keys[index.row()]]
        f     = entry.last_frame
        n2k   = n2k_frame_info(f)
        col   = index.column()

        if col == _SEP_COL_ACCUM:
            if role == Qt.ItemDataRole.BackgroundRole:
                return _COLOR_SEP_BG
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:  return _fmt_time(f.timestamp)
            if col == 1:  return f"{f.arbitration_id:08X}"
            if col == 2:  return "Ext" if f.is_extended_id else "Std"
            if col == 3:  return "1" if f.is_extended_id else "—"
            if col == 4:  return "1" if f.is_extended_id else "0"
            if col == 5:  return "1" if f.is_remote_frame else "0"
            if col == 6:  return str(f.dlc)
            if col == 7:  return bytes(f.data[:f.dlc]).hex(" ").upper()
            if col == 8:  return "N/A"
            if col == 10: return f"{n2k.pgn}  {self._store.pgn_sa_name(n2k.pgn, n2k.sa)}" if n2k else ""
            if col == 11: return str(n2k.edp) if n2k else ""
            if col == 12: return str(n2k.dp)  if n2k else ""
            if col == 13: return str(n2k.pf)  if n2k else ""
            if col == 14: return str(n2k.ps)  if n2k else ""
            if col == 15: return str(n2k.sa) if n2k else ""
            if col == 16: return str(n2k.dst) if (n2k and n2k.dst is not None) else ""
            if col == 17: return str(n2k.priority) if n2k else ""
            if col == 18: return str(n2k.seq)       if (n2k and n2k.seq       is not None) else ""
            if col == 19: return str(n2k.frame_idx) if (n2k and n2k.frame_idx is not None) else ""
            if col == 20: return str(entry.count)
            if col == 21:
                ms = entry.interval_ms
                return f"{ms:.0f} ms" if ms is not None else "—"

        if role == Qt.ItemDataRole.ToolTipRole:
            msg = f.n2k_msg
            if msg is None:
                return "Last frame did not participate in any assembled NMEA 2000 message (out-of-band)"
            if not msg.is_complete:
                return "Fast-packet sequence timed out before all frames arrived (incomplete)"
            if not msg.decoded:
                return "Assembled NMEA 2000 message — the nmea2000 library could not decode the payload"

        if role == Qt.ItemDataRole.ForegroundRole:
            flag = self._flag_for(f, n2k)
            if flag == "ignore":  return _COLOR_IGNORE
            if flag == "error":   return _COLOR_ERROR_FG

        if role == Qt.ItemDataRole.BackgroundRole:
            flag = self._flag_for(f, n2k)
            if flag == "highlight": return _COLOR_HIGHLIGHT
            if flag == "warning":   return _COLOR_WARNING
            if flag == "error":     return _COLOR_ERROR

        return None

    def _flag_for(self, frame, n2k) -> Priority:
        if n2k is not None:
            f = self._store.get_user_flag_by_pgn(n2k.pgn)
            if f:
                return f
        f = self._store.get_user_flag_by_arb(frame.arbitration_id)
        if f:
            return f
        msg = frame.n2k_msg
        if msg is None:
            return "warning"          # out-of-band: non-ext / short — never reached the library
        if not msg.is_complete:
            return "error"            # fast-packet timed out before completing
        if not msg.decoded:
            return "warning"          # assembled but the nmea2000 library could not decode it
        return None

    def refresh(self) -> None:
        self._keys_cache = None
        self.layoutChanged.emit()

    def reset(self) -> None:
        self._keys_cache = None
        self.layoutChanged.emit()


class RawCanTab(QWidget):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store       = store
        self._time_model  = TimeViewModel(store)
        self._accum_model = AccumViewModel(store)

        self._min_col_widths: dict[int, int] = {}
        self._held: int | None = None   # frames-in-queue held back while paused; None = not paused
        self._displayed_count = 0       # time-view row count at last refresh (scroll anchor)

        self._view = QTableView()
        self._view.setModel(self._time_model)
        hdr = self._view.horizontalHeader()
        hdr.setResizeContentsPrecision(50)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_SEP_COL, QHeaderView.ResizeMode.Fixed)
        hdr.sectionResized.connect(self._on_section_resized)
        self._view.setColumnWidth(_SEP_COL, 8)
        QTimer.singleShot(0, self._enable_autosize)
        self._view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._view.verticalHeader().setVisible(False)
        _apply_selection_band(self._view)
        self._view.doubleClicked.connect(self._copy_cell)

        self._group_combo = QComboBox()
        self._group_combo.addItems(_GROUP_OPTIONS)
        self._group_combo.currentIndexChanged.connect(self._on_group_selected)

        self._count_label = QLabel("0 frames")

        top = QHBoxLayout()
        top.addWidget(QLabel("Group by:"))
        top.addWidget(self._group_combo)
        top.addStretch()
        top.addWidget(self._count_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._view)

    def _on_group_selected(self, index: int) -> None:
        self._min_col_widths.clear()
        if index == 0:
            self._view.setModel(self._time_model)
            self._pin_sep(_SEP_COL)
        else:
            self._accum_model.set_mode(index - 1)
            self._view.setModel(self._accum_model)
            self._pin_sep(_SEP_COL_ACCUM)
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

    def _pin_sep(self, col: int) -> None:
        hdr = self._view.horizontalHeader()
        hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self._view.setColumnWidth(col, 8)

    def _update_count(self) -> None:
        model = self._view.model()
        rate  = self._store.frame_rate()
        rate_str = f"{rate:,.0f}/s" if rate is not None else "—/s"
        held_str = f"  [{self._held:,} held]" if self._held is not None else ""
        if model is self._time_model:
            shown = self._time_model.rowCount()
            total = len(self._store.frames)
            frames_str = (f"{shown:,} of {total:,} frames" if shown != total
                          else f"{total:,} frames")
            self._count_label.setText(f"{frames_str}  ({rate_str}){held_str}")
        else:
            shown      = self._accum_model.rowCount()
            total      = len(self._store.frames)
            rows_total = self._accum_model.unfiltered_count()
            rows_str = (f"{shown:,} of {rows_total:,} rows" if shown != rows_total
                        else f"{shown:,} rows")
            self._count_label.setText(
                f"{rows_str}  ({total:,} frames, {rate_str}){held_str}"
            )

    def set_held(self, held: int | None) -> None:
        """Tell the tab how many frames are held in the input queue (paused)."""
        if held == self._held:
            return
        self._held = held
        self._update_count()

    def _enable_autosize(self) -> None:
        hdr = self._view.horizontalHeader()
        sep = _SEP_COL_ACCUM if self._view.model() is self._accum_model else _SEP_COL
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(sep, QHeaderView.ResizeMode.Fixed)
        self._view.setColumnWidth(sep, 8)

    def _copy_cell(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text is None:
            return
        QApplication.clipboard().setText(str(text))

    def on_frames_added(self) -> None:
        model = self._view.model()
        if model is self._time_model:
            at_top   = self._view.verticalScrollBar().value() == 0
            top_row  = self._view.rowAt(0)
            prev     = self._displayed_count
            sel_rows = [ix.row() for ix in self._view.selectionModel().selectedRows()]
            cur_row  = self._view.currentIndex().row()
            model.refresh()
            self._displayed_count = model.rowCount()
            added = self._displayed_count - prev
            if added > 0:
                # New rows are prepended at the top; shift the selection and the
                # scroll anchor down by that many rows so both keep tracking the
                # same frame as it moves down.
                self._reanchor_selection(model, sel_rows, cur_row, added)
            if at_top or top_row < 0:
                self._view.scrollToTop()
            elif added > 0:
                self._view.scrollTo(
                    model.index(top_row + added, 0),
                    QTableView.ScrollHint.PositionAtTop,
                )
        else:
            model.refresh()  # type: ignore[attr-defined]
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
        """Reclassify both models over the current (reloaded) store contents,
        keeping the active filter. Called after Open / New."""
        self._time_model.reset()
        self._accum_model.reset()
        self._displayed_count = self._time_model.rowCount()
        self._update_count()

    def has_warnings(self) -> bool:
        return (self._store.oob_frame_count
                + self._store.incomplete_msg_count
                + self._store.undecoded_msg_count) > 0

    def warning_text(self) -> str:
        oob = self._store.oob_frame_count
        inc = self._store.incomplete_msg_count
        und = self._store.undecoded_msg_count
        if oob + inc + und == 0:
            return ""
        parts = []
        if oob:
            parts.append(f"{oob:,} frame(s) not linked to any NMEA 2000 message "
                         "(out-of-band / non-N2K traffic)")
        if inc:
            parts.append(f"{inc:,} incomplete fast-packet sequence(s)")
        if und:
            parts.append(f"{und:,} message(s) the decoder could not parse")
        return ("Since capture started: " + "; ".join(parts) + ".\n"
                "Counts are cumulative for the session.")
