from __future__ import annotations

import time
from collections import deque

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QSizePolicy, QSplitter,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from nmea2000 import FieldTypes

from canboat_explorer.core.filter import FilterSpec
from canboat_explorer.core.store import DataStore, RATE_WINDOW

_MAX_HISTORY = 20
_SKIP_TYPES  = {FieldTypes.RESERVED, FieldTypes.SPARE}

_ROLE_KEY        = Qt.ItemDataRole.UserRole
_ROLE_HISTORICAL = Qt.ItemDataRole.UserRole + 1
_ROLE_MSG        = Qt.ItemDataRole.UserRole + 2
_ROLE_TYPE       = Qt.ItemDataRole.UserRole + 3   # "pgn" | "sa" | "leaf"
_ROLE_PGN        = Qt.ItemDataRole.UserRole + 4
_ROLE_SA_KEY     = Qt.ItemDataRole.UserRole + 5   # (pgn, sa)

_SECTION_BG = QColor("#747474")


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


def _dst_suffix(decoded_msg) -> str:
    """'  →  SA:y' for an addressed (PDU1) message; '' for broadcast (255 / none)."""
    dst = getattr(decoded_msg, "destination", None)
    if dst is None:
        return ""
    if dst == 255:
        return " (broadcast)"
    return f"  →  SA:{dst}"


def _rate_label(dq: deque) -> str:
    n = min(len(dq), RATE_WINDOW)
    if n < 2:
        return ""
    try:
        # dq[0] is newest (appendleft); window over the most recent RATE_WINDOW.
        # The difference is a timedelta for datetime timestamps, a float for
        # float timestamps — test the difference, not the timestamp itself.
        delta   = dq[0].timestamp - dq[n - 1].timestamp
        elapsed = delta.total_seconds() if hasattr(delta, 'total_seconds') else float(delta)
        if elapsed <= 0:
            return ""
        return f"{elapsed / (n - 1) * 1000:.0f} ms"
    except Exception:
        return ""


class DecodedTab(QWidget):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store            = store
        self._pgn_items:  dict[int,         QTreeWidgetItem] = {}
        self._sa_items:   dict[tuple,       QTreeWidgetItem] = {}
        self._leaf_items: dict[tuple,       QTreeWidgetItem] = {}
        self._leaf_last_ts: dict[tuple, float] = {}   # per-key timestamp of last shown dq[0]
        self._selected_item: QTreeWidgetItem | None = None
        self._filter_spec      = FilterSpec()
        self._filter_active    = False    # filter currently restricting the tree
        self._filter_vis       = 0        # visible leaves under the active filter

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- left: signal tree; an empty strip above it mirrors the right
        # pane's header height so the two pane tops line up into one bar ----
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)

        spacer = QLabel("")
        spacer.setStyleSheet("padding: 2px;")
        ll.addWidget(spacer)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Signal", "PGN", "Rate"])
        # Signal stretches to fill spare width (capped in _fit_left_pane so it
        # never gets ugly-long); PGN and Rate are sized to ~130% of their content
        # there (Interactive, so the explicit width sticks) for some breathing room.
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self._tree.header().setStretchLastSection(False)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        ll.addWidget(self._tree)
        self._left_pane = left
        splitter.addWidget(left)

        # ---- right: field table, with breadcrumb (left) + count (right) above ----
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)

        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)

        self._hdr_label = QLabel("")
        self._hdr_label.setStyleSheet("font-weight: bold; padding: 2px;")
        # A long breadcrumb should clip, not widen (and so resize) this strip;
        # the Ignored policy also lets it fill the slack and push the count right.
        self._hdr_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        hdr_row.addWidget(self._hdr_label, 1)   # stretch 1 → eats the slack, pins count hard right

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("padding: 2px;")
        self._count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        hdr_row.addWidget(self._count_label)

        rl.addLayout(hdr_row)

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
        # The signal tree is width-capped (see _fit_left_pane); surplus width
        # goes to the field table on the right rather than widening the tree.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self._update_count()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_messages_added(self) -> None:
        any_changed = False
        created     = False
        for key, dq in self._store.decoded_by_key.items():
            if not dq:
                continue
            head_ts = dq[0].timestamp
            if self._leaf_last_ts.get(key) == head_ts:
                continue   # this stream got no new message since the last drain
            if key not in self._leaf_items:
                self._create_entry(key, dq[0])
                created = True
            leaf = self._leaf_items[key]
            leaf.setText(2, _rate_label(dq))
            if leaf.isExpanded():
                self._rebuild_history(leaf, dq)
            self._leaf_last_ts[key] = head_ts
            any_changed = True
        if created:
            self._fit_left_pane()
            if self._filter_active:       # hide any newly-added nodes that don't match
                self._apply_filter_tree()
            self._update_count()
        if any_changed:
            self._refresh_selection()

    def reset(self) -> None:
        self._tree.clear()
        self._pgn_items.clear()
        self._sa_items.clear()
        self._leaf_items.clear()
        self._leaf_last_ts.clear()
        self._selected_item = None
        self._table.clearSpans()
        self._table.setRowCount(0)
        self._hdr_label.setText("")
        self._filter_vis = 0
        self._update_count()

    # ------------------------------------------------------------------
    # Global filter (tree pruning)
    # ------------------------------------------------------------------

    def apply_filter(self, spec: FilterSpec) -> None:
        self._filter_spec   = spec
        self._filter_active = not spec.is_empty
        self._apply_filter_tree()
        self._update_count()

    def filter_counts(self) -> tuple[int, int]:
        total = len(self._leaf_items)
        if not self._filter_active:
            return total, total
        return self._filter_vis, total

    def _update_count(self) -> None:
        visible, total = self.filter_counts()
        if visible != total:
            self._count_label.setText(f"showing {visible:,} of {total:,} signals")
        else:
            self._count_label.setText(f"{total:,} signals")

    def _leaf_dst(self, key: tuple) -> int | None:
        """Destination of the latest decoded message for this leaf, matching the
        Tabs 1–2 convention: a broadcast (decoder reports 255, or no destination)
        is None; an addressed message is its 0–253 address. None when the leaf has
        no decoded message yet. The dst filter treats None as '(empty)'."""
        dq  = self._store.decoded_by_key.get(key)
        if not dq:
            return None
        dst = getattr(dq[0], "destination", None)
        return None if dst == 255 else dst

    def _leaf_matches_filter(self, pgn: int, sa: int, dst: int | None, leaf: QTreeWidgetItem) -> bool:
        flag     = self._store.get_user_flag_by_pgn(pgn)
        pgn_item = self._pgn_items.get(pgn)
        desc     = pgn_item.text(0) if pgn_item is not None else ""
        return self._filter_spec.matches(
            pgn=pgn, sa=sa, dst=dst,
            text=f"{desc} {leaf.text(0)}",   # fold PGN description in for name search
            is_highlighted=(flag == "highlight"),
            is_ignored=(flag == "ignore"),
        )

    def _apply_filter_tree(self) -> None:
        empty = self._filter_spec.is_empty
        vis   = 0
        for key, leaf in self._leaf_items.items():
            pgn, sa, _qualifier = key
            ok = empty or self._leaf_matches_filter(pgn, sa, self._leaf_dst(key), leaf)
            leaf.setHidden(not ok)
            if ok:
                vis += 1
        # Intermediate SA nodes, then PGN groups: visible if any child is visible.
        for sa_item in self._sa_items.values():
            any_vis = empty or any(not sa_item.child(i).isHidden()
                                   for i in range(sa_item.childCount()))
            sa_item.setHidden(not any_vis)
        for pgn_item in self._pgn_items.values():
            any_vis = empty or any(not pgn_item.child(i).isHidden()
                                   for i in range(pgn_item.childCount()))
            pgn_item.setHidden(not any_vis)
        self._filter_vis = vis

    def has_warnings(self) -> bool:
        return False

    def warning_text(self) -> str:
        return ""

    # ------------------------------------------------------------------
    # Tree management
    # ------------------------------------------------------------------

    def _create_entry(self, key: tuple, decoded_msg) -> None:
        pgn, sa, qualifier_tuple = key

        # Level 1: PGN group
        pgn_item = self._pgn_items.get(pgn)
        if pgn_item is None:
            pgn_item = QTreeWidgetItem([decoded_msg.description or f"PGN {pgn}", str(pgn), ""])
            bold = QFont()
            bold.setBold(True)
            pgn_item.setFont(0, bold)
            pgn_item.setData(0, _ROLE_TYPE, "pgn")
            pgn_item.setData(0, _ROLE_PGN,  pgn)
            self._tree.addTopLevelItem(pgn_item)
            self._pgn_items[pgn] = pgn_item

        if not qualifier_tuple:
            # No qualifier — SA node is the leaf
            leaf = QTreeWidgetItem([f"SA:{sa}", "", ""])
            leaf.setData(0, _ROLE_TYPE,       "leaf")
            leaf.setData(0, _ROLE_KEY,        key)
            leaf.setData(0, _ROLE_HISTORICAL, False)
            leaf.addChild(QTreeWidgetItem(["…", ""]))
            pgn_item.addChild(leaf)
            pgn_item.setExpanded(True)
            self._leaf_items[key] = leaf
        else:
            # Level 2: SA intermediate node
            sa_key   = (pgn, sa)
            sa_item  = self._sa_items.get(sa_key)
            if sa_item is None:
                sa_item = QTreeWidgetItem([f"SA:{sa}", "", ""])
                sa_item.setData(0, _ROLE_TYPE,   "sa")
                sa_item.setData(0, _ROLE_PGN,    pgn)
                sa_item.setData(0, _ROLE_SA_KEY, sa_key)
                pgn_item.addChild(sa_item)
                pgn_item.setExpanded(True)
                self._sa_items[sa_key] = sa_item

            # Level 3: qualifier leaf
            qualifier = _qualifier_label(decoded_msg) or str(qualifier_tuple)
            leaf = QTreeWidgetItem([qualifier, "", ""])
            leaf.setData(0, _ROLE_TYPE,       "leaf")
            leaf.setData(0, _ROLE_KEY,        key)
            leaf.setData(0, _ROLE_HISTORICAL, False)
            leaf.addChild(QTreeWidgetItem(["…", ""]))
            sa_item.addChild(leaf)
            sa_item.setExpanded(True)
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
        if item.data(0, _ROLE_TYPE) == "leaf":
            key = item.data(0, _ROLE_KEY)
            dq  = self._store.decoded_by_key.get(key)
            if dq:
                self._rebuild_history(item, dq)
        # Newly-revealed rows can be wider/deeper than what was visible before.
        self._fit_left_pane()

    def _fit_left_pane(self) -> None:
        # Give every column ~130-140% of its content for breathing room: Signal
        # stretches up to its cap, PGN and Rate get an explicit width. The tree
        # is capped at their sum so it never gets ugly-long and surplus width
        # goes to the field table on the right.
        sig  = int(self._tree.sizeHintForColumn(0) * 1.4)
        pgn  = int(self._tree.sizeHintForColumn(1) * 1.3)
        rate = int(self._tree.sizeHintForColumn(2) * 1.3)
        self._tree.setColumnWidth(1, pgn)
        self._tree.setColumnWidth(2, rate)
        # + fixed allowance for the vertical scrollbar, frame, padding and the
        # left pane's content margins (cap the container so the count strip above
        # the tree spans the same width as the tree).
        self._left_pane.setMaximumWidth(
            sig + pgn + rate + 2 * self._tree.frameWidth() + 32 + 8
        )

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        item       = items[0]
        node_type  = item.data(0, _ROLE_TYPE)
        historical = item.data(0, _ROLE_HISTORICAL)

        if historical:
            msg = item.data(0, _ROLE_MSG)
            if msg is not None:
                base  = self._leaf_breadcrumb(item.parent(), msg)
                try:
                    ts_str = _fmt_time(msg.timestamp)
                except Exception:
                    ts_str = "snapshot"
                self._selected_item = item
                self._show_message(msg, title=f"{base}  —  {ts_str}")

        elif node_type in ("pgn", "sa"):
            msgs = self._collect_latest_msgs_under(item)
            if msgs:
                self._selected_item = item
                self._show_stacked(self._stacked_title(item, msgs[0]), msgs)

        elif node_type == "leaf":
            key = item.data(0, _ROLE_KEY)
            dq  = self._store.decoded_by_key.get(key)
            if dq:
                self._selected_item = item
                self._show_message(dq[0], title=self._leaf_breadcrumb(item, dq[0]))

    def _leaf_breadcrumb(self, leaf: QTreeWidgetItem | None, decoded_msg) -> str:
        if leaf is None:
            return decoded_msg.description or f"PGN {decoded_msg.PGN}"
        parent = leaf.parent()
        parent_type = parent.data(0, _ROLE_TYPE) if parent else None
        pgn  = (parent or leaf).data(0, _ROLE_PGN) or decoded_msg.PGN
        desc = decoded_msg.description or f"PGN {pgn}"
        base = f"{desc}  (PGN {pgn})"
        if parent_type == "sa":
            sa        = parent.data(0, _ROLE_SA_KEY)[1]
            qualifier = _qualifier_label(decoded_msg) or leaf.text(0)
            return f"{base}  —  SA:{sa}{_dst_suffix(decoded_msg)}  —  {qualifier}"
        if parent_type == "pgn":
            sa = leaf.data(0, _ROLE_KEY)[1]
            return f"{base}  —  SA:{sa}{_dst_suffix(decoded_msg)}"
        return base

    def _stacked_title(self, item: QTreeWidgetItem, sample_msg) -> str:
        node_type = item.data(0, _ROLE_TYPE)
        pgn  = item.data(0, _ROLE_PGN)
        desc = sample_msg.description or f"PGN {pgn}"
        base = f"{desc}  (PGN {pgn})"
        if node_type == "sa":
            sa = item.data(0, _ROLE_SA_KEY)[1]
            return f"{base}  —  SA:{sa}"
        return base

    def _collect_latest_msgs_under(self, item: QTreeWidgetItem) -> list:
        msgs = []
        for i in range(item.childCount()):
            child      = item.child(i)
            child_type = child.data(0, _ROLE_TYPE)
            if child_type == "leaf":
                key = child.data(0, _ROLE_KEY)
                dq  = self._store.decoded_by_key.get(key)
                if dq:
                    msgs.append(dq[0])
            elif child_type == "sa":
                msgs.extend(self._collect_latest_msgs_under(child))
        return msgs

    def _refresh_selection(self) -> None:
        item = self._selected_item
        if item is None:
            return
        node_type  = item.data(0, _ROLE_TYPE)
        historical = item.data(0, _ROLE_HISTORICAL)
        if node_type in ("pgn", "sa"):
            msgs = self._collect_latest_msgs_under(item)
            if msgs:
                self._show_stacked(self._stacked_title(item, msgs[0]), msgs)
        elif node_type == "leaf" and not historical:
            key = item.data(0, _ROLE_KEY)
            dq  = self._store.decoded_by_key.get(key)
            if dq:
                self._show_message(dq[0], title=self._leaf_breadcrumb(item, dq[0]))

    # ------------------------------------------------------------------
    # Right-panel field table
    # ------------------------------------------------------------------

    def _show_message(self, decoded_msg, title: str = "") -> None:
        self._table.clearSpans()
        decoded_msg.apply_preferred_units({})
        self._hdr_label.setText(title)
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

    def _show_stacked(self, title: str, msgs: list) -> None:
        self._table.clearSpans()
        self._hdr_label.setText(title)
        self._hdr_label.setStyleSheet("font-weight: bold; padding: 2px;")

        # Build row list: (is_section, section_label, field)
        rows: list[tuple] = []
        for msg in msgs:
            msg.apply_preferred_units({})
            section = _qualifier_label(msg)
            if section:
                rows.append((True, section, None))
            for field in msg.fields:
                if field.type not in _SKIP_TYPES:
                    rows.append((False, None, field))

        self._table.setRowCount(len(rows))
        section_font = QFont()
        section_font.setBold(True)
        italic = QFont()
        italic.setItalic(True)

        for row_idx, (is_section, label, field) in enumerate(rows):
            if is_section:
                hdr = QTableWidgetItem(label)
                hdr.setFont(section_font)
                hdr.setBackground(QBrush(_SECTION_BG))
                self._table.setItem(row_idx, 0, hdr)
                self._table.setItem(row_idx, 1, QTableWidgetItem(""))
                self._table.setItem(row_idx, 2, QTableWidgetItem(""))
                self._table.setSpan(row_idx, 0, 1, 3)
            else:
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

                self._table.setItem(row_idx, 0, name_item)
                self._table.setItem(row_idx, 1, val_item)
                self._table.setItem(row_idx, 2, unit_item)
