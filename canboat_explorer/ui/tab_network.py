from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from canboat_explorer.core.filter import FilterSpec
from canboat_explorer.core.pgn_catalogue import pgn_name
from canboat_explorer.core.store import DataStore, DeviceInfo

_STALE_S    = 60.0
_SECTION_FG = QColor("#aaaaaa")
_STALE_FG   = QColor("#888888")
_SEEN_FG    = QColor("#aaaaaa")
_ROLE_SA      = Qt.ItemDataRole.UserRole
_ROLE_FG0     = Qt.ItemDataRole.UserRole + 1   # cached foreground state, column 0
_ROLE_FG1     = Qt.ItemDataRole.UserRole + 2   # cached foreground state, column 1
_ROLE_SECTION = Qt.ItemDataRole.UserRole + 3   # section id on a header item
_ROLE_PGN     = Qt.ItemDataRole.UserRole + 4   # pgn on a leaf item

# Canonical ordering of section headers under an SA node.
_SECTION_ORDER = {"tx": 0, "rx": 1, "ua": 2, "obs": 0, "ps": 3}

# PGNs that every N2K device sends as normal bookkeeping — split out of the
# Observed lists into a collapsed "Protocol Specific" section so they don't
# drown the device's own data PGNs.
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
        self._filter_spec      = FilterSpec()
        self._filter_active    = False      # filter currently restricting the tree
        self._filter_vis       = 0          # visible PGN leaves under the active filter

        # Persistent item registries — the tree is updated in place, never
        # cleared, so selection / expansion / scroll / tooltips survive a
        # refresh even under a flood of incoming messages.
        self._sa_items:      dict[int, QTreeWidgetItem]              = {}
        self._info_items:    dict[int, QTreeWidgetItem]              = {}
        self._section_items: dict[tuple[int, str], QTreeWidgetItem]  = {}
        self._pgn_items:     dict[tuple[int, str, int], QTreeWidgetItem] = {}

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

        self._count_label = QLabel("")
        top = QHBoxLayout()
        top.setContentsMargins(4, 2, 6, 2)
        top.addStretch()
        top.addWidget(self._count_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)   # splitter takes all spare height; count row stays thin
        self._update_count()

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
        self._reconcile()
        self._append_log()
        if self._filter_active:        # hide any newly-added nodes that don't match
            self._apply_filter_tree()
        self._update_count()

    def reset(self) -> None:
        self._tree.clear()
        self._log.clear()
        self._sa_items.clear()
        self._info_items.clear()
        self._section_items.clear()
        self._pgn_items.clear()
        self._last_n2k_count   = 0
        self._last_decoded_len = 0
        self._log_processed    = 0
        self._filter_vis       = 0
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
        total = len(self._pgn_items)
        if not self._filter_active:
            return total, total
        return self._filter_vis, total

    def _update_count(self) -> None:
        visible, total = self.filter_counts()
        if visible != total:
            self._count_label.setText(f"showing {visible:,} of {total:,} PGNs")
        else:
            self._count_label.setText(f"{total:,} PGNs")

    def _leaf_matches_filter(self, sa: int, dev_label: str, leaf: QTreeWidgetItem, pgn: int) -> bool:
        flag = self._store.get_user_flag_by_pgn(pgn)
        return self._filter_spec.matches(
            pgn=pgn, sa=sa,
            text=f"{dev_label} {leaf.text(0)}",   # fold device label in for name search
            is_highlighted=(flag == "highlight"),
            is_ignored=(flag == "ignore"),
        )

    def _apply_filter_tree(self) -> None:
        spec  = self._filter_spec
        empty = spec.is_empty
        vis   = 0
        for sa, sa_item in self._sa_items.items():
            dev_label   = sa_item.text(0)
            sa_leaf_vis = 0
            for i in range(sa_item.childCount()):
                section = sa_item.child(i)
                if section.data(0, _ROLE_SECTION) is None:
                    continue   # product-info child — follows the SA node's visibility
                sec_vis = 0
                for j in range(section.childCount()):
                    leaf = section.child(j)
                    pgn  = leaf.data(0, _ROLE_PGN)
                    ok   = empty or self._leaf_matches_filter(sa, dev_label, leaf, pgn)
                    leaf.setHidden(not ok)
                    if ok:
                        sec_vis += 1
                section.setHidden(not (empty or sec_vis))
                sa_leaf_vis += sec_vis
            # Childless SA still findable by device-name text search.
            name_hit = (bool(spec.text) and not spec.pgns
                        and not (spec.warnings or spec.errors or spec.highlighted)
                        and spec.text in dev_label.lower())
            sa_show = empty or sa_leaf_vis > 0 or name_hit
            sa_item.setHidden(not sa_show)
            info = self._info_items.get(sa)
            if info is not None:
                info.setHidden(not sa_show)
            vis += sa_leaf_vis
        self._filter_vis = vis

    def has_warnings(self) -> bool:
        return bool(self._data_collisions())

    def warning_text(self) -> str:
        cols = self._data_collisions()
        if not cols:
            return ""
        pgns = ", ".join(str(p) for p in sorted(cols))
        return ("Same PGN transmitted from multiple source addresses "
                f"(marked ⚠ in the tree): {pgns}.")

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _data_collisions(self) -> set[int]:
        # Management PGNs (heartbeat, address claim, …) are emitted by every
        # device, so "same PGN from multiple source addresses" is expected for
        # them — not a real collision. Keep them out of the ⚠ warning.
        return self._store.sa_pgn_collisions - _MGMT_PGNS

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
    # Tree reconcile (in-place — never clears the tree)
    # ------------------------------------------------------------------

    def _reconcile(self) -> None:
        now        = time.time()
        collisions = self._data_collisions()
        for sa in sorted(self._store.devices):
            self._reconcile_sa(sa, self._store.devices[sa], collisions, now)

    def _reconcile_sa(
        self, sa: int, dev: DeviceInfo, collisions: set[int], now: float,
    ) -> None:
        has_ac = dev.has_address_claim

        if dev.model:
            label = f"SA:{sa}  —  {dev.model}"
        elif has_ac:
            label = f"SA:{sa}  —  (address claimed)"
        else:
            label = f"SA:{sa}  —  (seen only)"

        sa_item = self._sa_items.get(sa)
        if sa_item is None:
            sa_item = QTreeWidgetItem([label, ""])
            sa_item.setData(0, _ROLE_SA, sa)
            bold = QFont()
            bold.setBold(True)
            sa_item.setFont(0, bold)
            self._tree.insertTopLevelItem(self._sa_insert_index(sa), sa_item)
            sa_item.setExpanded(True)
            self._sa_items[sa] = sa_item
        elif sa_item.text(0) != label:
            sa_item.setText(0, label)

        self._apply_sa_visual(sa_item, dev, now)

        # ---- product info child (always first child) ----
        info_line = dev.product_info_line
        info_item = self._info_items.get(sa)
        if info_line:
            if info_item is None:
                info_item = QTreeWidgetItem([info_line, ""])
                italic = QFont()
                italic.setItalic(True)
                info_item.setFont(0, italic)
                info_item.setFlags(info_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                sa_item.insertChild(0, info_item)
                self._info_items[sa] = info_item
            elif info_item.text(0) != info_line:
                info_item.setText(0, info_line)
        elif info_item is not None:
            sa_item.removeChild(info_item)
            del self._info_items[sa]

        # ---- sections ----
        present: set[str] = set()
        if has_ac:
            tx_pgns      = sorted(dev.tx_pgns)
            rx_pgns      = sorted(dev.rx_pgns)
            unadvertised = sorted(dev.seen_pgns - dev.tx_pgns - dev.rx_pgns - _MGMT_PGNS)

            if tx_pgns:
                hdr = self._ensure_section(sa_item, sa, "tx", f"TX PGNs ({len(tx_pgns)})")
                self._reconcile_pgns(hdr, sa, "tx", tx_pgns, dev, collisions)
                present.add("tx")
            if rx_pgns:
                hdr = self._ensure_section(sa_item, sa, "rx", f"RX PGNs ({len(rx_pgns)})")
                self._reconcile_pgns(hdr, sa, "rx", rx_pgns, dev, collisions)
                present.add("rx")
            if unadvertised:
                ua_label = "Observed / not advertised" + (
                    "  (no PGN list received)" if not tx_pgns and not rx_pgns else ""
                )
                hdr = self._ensure_section(sa_item, sa, "ua", ua_label)
                self._reconcile_pgns(hdr, sa, "ua", unadvertised, dev, collisions)
                present.add("ua")
        else:
            visible = sorted(dev.seen_pgns - _MGMT_PGNS)
            if visible:
                hdr = self._ensure_section(sa_item, sa, "obs", f"Observed PGNs ({len(visible)})")
                self._reconcile_pgns(hdr, sa, "obs", visible, dev, collisions)
                present.add("obs")

        # ---- protocol-specific (management) PGNs, collapsed by default ----
        protocol = sorted((dev.seen_pgns - dev.tx_pgns - dev.rx_pgns) & _MGMT_PGNS)
        if protocol:
            hdr = self._ensure_section(
                sa_item, sa, "ps", f"Protocol Specific ({len(protocol)})",
                expanded=False,
            )
            self._reconcile_pgns(hdr, sa, "ps", protocol, dev, collisions)
            present.add("ps")

        self._remove_absent_sections(sa, present)

    def _reconcile_pgns(
        self, hdr: QTreeWidgetItem, sa: int, section_id: str,
        pgns: list[int], dev: DeviceInfo, collisions: set[int],
    ) -> None:
        wanted = set(pgns)

        # Drop leaves whose PGN migrated out of this section.
        for i in range(hdr.childCount() - 1, -1, -1):
            cpgn = hdr.child(i).data(0, _ROLE_PGN)
            if cpgn not in wanted:
                hdr.takeChild(i)
                self._pgn_items.pop((sa, section_id, cpgn), None)

        for pgn in pgns:
            self._ensure_pgn_leaf(hdr, sa, section_id, pgn, dev, collisions)

    def _ensure_pgn_leaf(
        self, hdr: QTreeWidgetItem, sa: int, section_id: str,
        pgn: int, dev: DeviceInfo, collisions: set[int],
    ) -> None:
        is_coll = pgn in collisions
        col0    = ("⚠ " if is_coll else "    ") + _pgn_label(pgn)
        col1    = "" if section_id == "rx" else self._obs_status(pgn, sa)

        leaf = self._pgn_items.get((sa, section_id, pgn))
        if leaf is None:
            leaf = QTreeWidgetItem([col0, col1])
            leaf.setData(0, _ROLE_PGN, pgn)
            hdr.insertChild(self._leaf_insert_index(hdr, pgn), leaf)
            self._pgn_items[(sa, section_id, pgn)] = leaf
        else:
            if leaf.text(0) != col0:
                leaf.setText(0, col0)
            if leaf.text(1) != col1:
                leaf.setText(1, col1)

        # TX PGNs that were never actually observed are dimmed in column 1.
        if section_id == "tx" and pgn not in dev.seen_pgns:
            self._apply_fg(leaf, 1, _STALE_FG, 1)
        else:
            self._apply_fg(leaf, 1, None, 0)

        tip = self._collision_tooltip(pgn) if is_coll else ""
        if leaf.toolTip(0) != tip:
            leaf.setToolTip(0, tip)

    def _ensure_section(
        self, sa_item: QTreeWidgetItem, sa: int, section_id: str, label: str,
        expanded: bool = True,
    ) -> QTreeWidgetItem:
        hdr = self._section_items.get((sa, section_id))
        if hdr is None:
            hdr = self._section_item(label)
            hdr.setData(0, _ROLE_SECTION, section_id)
            sa_item.insertChild(self._section_insert_index(sa_item, sa, section_id), hdr)
            # Only the initial expansion is forced; a later reconcile leaves the
            # user's manual expand/collapse of the section untouched.
            hdr.setExpanded(expanded)
            self._section_items[(sa, section_id)] = hdr
        elif hdr.text(0) != label:
            hdr.setText(0, label)
        return hdr

    def _remove_absent_sections(self, sa: int, present: set[str]) -> None:
        for section_id in [s for (a, s) in self._section_items if a == sa and s not in present]:
            hdr     = self._section_items.pop((sa, section_id))
            sa_item = self._sa_items.get(sa)
            if sa_item is not None:
                sa_item.removeChild(hdr)
            for i in range(hdr.childCount()):
                self._pgn_items.pop((sa, section_id, hdr.child(i).data(0, _ROLE_PGN)), None)

    # ------------------------------------------------------------------
    # Insertion-order helpers
    # ------------------------------------------------------------------

    def _sa_insert_index(self, sa: int) -> int:
        for i in range(self._tree.topLevelItemCount()):
            other = self._tree.topLevelItem(i).data(0, _ROLE_SA)
            if other is not None and other > sa:
                return i
        return self._tree.topLevelItemCount()

    def _section_insert_index(
        self, sa_item: QTreeWidgetItem, sa: int, section_id: str,
    ) -> int:
        rank = _SECTION_ORDER[section_id]
        idx  = 1 if sa in self._info_items else 0
        for i in range(idx, sa_item.childCount()):
            cid = sa_item.child(i).data(0, _ROLE_SECTION)
            if cid is not None and _SECTION_ORDER[cid] < rank:
                idx = i + 1
        return idx

    def _leaf_insert_index(self, hdr: QTreeWidgetItem, pgn: int) -> int:
        for i in range(hdr.childCount()):
            if hdr.child(i).data(0, _ROLE_PGN) > pgn:
                return i
        return hdr.childCount()

    def _apply_fg(self, item: QTreeWidgetItem, col: int, color: QColor | None, state: int) -> None:
        # color=None means "reset to the theme default" — a default-constructed
        # QBrush has NoBrush style, which the view treats as no override. Passing
        # QBrush(QColor()) instead would paint the text solid black.
        role = _ROLE_FG0 if col == 0 else _ROLE_FG1
        if item.data(0, role) != state:
            item.setForeground(col, QBrush(color) if color is not None else QBrush())
            item.setData(0, role, state)

    def _apply_sa_visual(self, sa_item: QTreeWidgetItem, dev: DeviceInfo, now: float) -> None:
        last_ts = dev.last_seen_ts
        stale   = last_ts > 0 and (now - last_ts) > _STALE_S
        has_ac  = dev.has_address_claim
        status  = f"silent {int(now - last_ts)}s" if stale else ""
        if sa_item.text(1) != status:
            sa_item.setText(1, status)
        if stale:
            self._apply_fg(sa_item, 0, _STALE_FG, 2)
            self._apply_fg(sa_item, 1, _STALE_FG, 2)
        elif not has_ac:
            self._apply_fg(sa_item, 0, _SEEN_FG, 1)
            self._apply_fg(sa_item, 1, _SEEN_FG, 1)
        else:
            self._apply_fg(sa_item, 0, None, 0)
            self._apply_fg(sa_item, 1, None, 0)

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
        for sa, sa_item in self._sa_items.items():
            dev = self._store.devices.get(sa)
            if dev is not None:
                self._apply_sa_visual(sa_item, dev, now)

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
