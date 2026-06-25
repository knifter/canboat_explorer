"""
Global filter bar — discrete inputs + flag toggles, shown above the tab widget.

Holds the single shared `FilterSpec` (built on demand from the widgets) and
emits a debounced `changed` signal that each tab subscribes to. Text edits are
debounced; the toggle buttons and Clear fire immediately. The filter is
transient view state — never persisted.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QWidget,
)

from canboat_explorer.core.filter import FilterSpec, parse_int_list

_DEBOUNCE_MS = 200

# Soft amber on the On/Off button while the filter is actively hiding rows.
_ACTIVE_BTN_STYLE = "QPushButton { background-color: #e6b24a; color: black; }"

# Returns the observed values to offer in the double-click picker, as
# (value, label) pairs already in display order.
ValueSource = Callable[[], list[tuple[int, str]]]


class _PickerLineEdit(QLineEdit):
    """Line edit whose double-click opens a menu of observed values.

    Clicking an entry toggles that integer in the field's comma list. The
    values come live from a source callable (the store), so nothing is cached
    here — the menu always reflects what has been seen so far.
    """

    def __init__(self) -> None:
        super().__init__()
        self._source: ValueSource | None = None

    def set_source(self, source: ValueSource) -> None:
        self._source = source

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        values = self._source() if self._source is not None else []
        if not values:
            super().mouseDoubleClickEvent(event)
            return
        current = parse_int_list(self.text())
        menu = QMenu(self)
        for val, label in values:
            act = menu.addAction(("✓ " if val in current else "    ") + (label or str(val)))
            act.setData(val)
        chosen = menu.exec(self.mapToGlobal(event.position().toPoint()))
        if chosen is not None:
            self._toggle_value(int(chosen.data()))

    def _toggle_value(self, val: int) -> None:
        vals = list(parse_int_list(self.text()))
        if val in vals:
            vals.remove(val)
        else:
            vals.append(val)
        self.setText(",".join(str(v) for v in sorted(vals)))


class FilterBar(QWidget):
    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()

        self._pgn_edit  = _PickerLineEdit()
        self._sa_edit   = _PickerLineEdit()
        self._dst_edit  = _PickerLineEdit()
        self._text_edit = QLineEdit()
        self._pgn_edit.setPlaceholderText("e.g. 130306,129025")
        self._sa_edit.setPlaceholderText("e.g. 12,35")
        self._dst_edit.setPlaceholderText("e.g. 12,35")
        self._text_edit.setPlaceholderText("search name / text…")
        self._pgn_edit.setToolTip("Match one or more PGNs (comma list = OR).\nDouble-click to pick from observed PGNs.")
        self._sa_edit.setToolTip("Match one or more source addresses (comma list = OR).\nDouble-click to pick from observed source addresses.")
        self._dst_edit.setToolTip("Match one or more destination addresses (comma list = OR).\nDouble-click to pick from observed destinations, including (empty) for messages with no destination (PDU2 broadcast).")
        self._text_edit.setToolTip(
            "Case-insensitive substring against the row's text "
            "(PGN description, resolved variant name, manufacturer, device name).")
        self._pgn_edit.setMaximumWidth(160)
        self._sa_edit.setMaximumWidth(110)
        self._dst_edit.setMaximumWidth(110)
        self._text_edit.setMinimumWidth(160)

        self._warn_btn   = self._toggle("Warnings",
            "Restrict to rows flagged as a warning (Tabs 1–2). Combines with the "
            "other flag toggles as OR.")
        self._err_btn    = self._toggle("Errors",
            "Restrict to rows flagged as an error (Tabs 1–2). Combines with the "
            "other flag toggles as OR.")
        self._hl_btn     = self._toggle("Highlighted",
            "Restrict to rows the user has highlighted. Combines with the other "
            "flag toggles as OR.")
        self._ignore_btn = self._toggle("Hide ignored",
            "Hide rows the user has flagged as ignore. Independent suppressor — "
            "composes with every other condition.")

        self._enable_btn = QPushButton("Filter: On")
        self._enable_btn.setCheckable(True)
        self._enable_btn.setChecked(True)
        self._enable_btn.setToolTip(
            "Turn the whole filter on/off without losing the current settings.")
        self._enable_btn.toggled.connect(self._on_enable_toggled)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setToolTip("Reset every input and toggle.")
        self._clear_btn.clicked.connect(self._on_clear)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.changed)

        for edit in (self._pgn_edit, self._sa_edit, self._dst_edit, self._text_edit):
            edit.textChanged.connect(self._debounce.start)

        # Keep the On/Off button's "actively filtering" tint in sync with state.
        self.changed.connect(self._update_enable_style)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.addWidget(self._enable_btn)
        layout.addWidget(self._clear_btn)
        layout.addSpacing(8)
        layout.addWidget(self._vline())
        layout.addSpacing(8)
        layout.addWidget(QLabel("PGN:"))
        layout.addWidget(self._pgn_edit)
        layout.addWidget(QLabel("SA:"))
        layout.addWidget(self._sa_edit)
        layout.addWidget(QLabel("Dst:"))
        layout.addWidget(self._dst_edit)
        layout.addWidget(self._text_edit)
        layout.addSpacing(8)
        layout.addWidget(self._warn_btn)
        layout.addWidget(self._err_btn)
        # Highlighted / Hide-ignored not wired into the tabs yet — keep the
        # widgets (so spec/clear stay intact) but don't show them for now.
        # layout.addWidget(self._hl_btn)
        # layout.addWidget(self._ignore_btn)
        layout.addStretch()

        self._update_enable_style()

    def _toggle(self, label: str, tip: str) -> QCheckBox:
        box = QCheckBox(label)
        box.setToolTip(tip)
        # Toggles apply immediately (no debounce).
        box.toggled.connect(self.changed)
        return box

    def _vline(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def spec(self) -> FilterSpec:
        # Off-knob preserves the input widgets but reports an empty (pass-all)
        # spec, so the filter simply isn't applied.
        if not self._enable_btn.isChecked():
            return FilterSpec()
        return FilterSpec(
            pgns=parse_int_list(self._pgn_edit.text()),
            sas=parse_int_list(self._sa_edit.text()),
            dsts=parse_int_list(self._dst_edit.text()),
            text=self._text_edit.text().strip().lower(),
            warnings=self._warn_btn.isChecked(),
            errors=self._err_btn.isChecked(),
            highlighted=self._hl_btn.isChecked(),
            hide_ignored=self._ignore_btn.isChecked(),
        )

    def is_active_filter(self) -> bool:
        """True when the filter is on and actually narrowing results — i.e. the
        On/Off button is on and at least one condition is set. (Mirrors the
        amber tint logic so an external indicator can match it.)"""
        return not self.spec.is_empty

    def set_sources(self, pgn_source: ValueSource, sa_source: ValueSource,
                    dst_source: ValueSource) -> None:
        """Wire the double-click pickers to live observed-value providers."""
        self._pgn_edit.set_source(pgn_source)
        self._sa_edit.set_source(sa_source)
        self._dst_edit.set_source(dst_source)

    def focus_search(self) -> None:
        self._text_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._text_edit.selectAll()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_enable_toggled(self, on: bool) -> None:
        self._enable_btn.setText("Filter: On" if on else "Filter: Off")
        self.changed.emit()

    def _update_enable_style(self) -> None:
        # Tint the On/Off button while the filter is on AND has conditions set,
        # so it's obvious that rows are being hidden. (spec.is_empty is True both
        # when off and when on-but-blank.)
        active = not self.spec.is_empty
        self._enable_btn.setStyleSheet(_ACTIVE_BTN_STYLE if active else "")

    def _on_clear(self) -> None:
        widgets = (self._pgn_edit, self._sa_edit, self._dst_edit, self._text_edit,
                   self._warn_btn, self._err_btn, self._hl_btn, self._ignore_btn)
        for w in widgets:
            w.blockSignals(True)
        self._pgn_edit.clear()
        self._sa_edit.clear()
        self._dst_edit.clear()
        self._text_edit.clear()
        self._warn_btn.setChecked(False)
        self._err_btn.setChecked(False)
        self._hl_btn.setChecked(False)
        self._ignore_btn.setChecked(False)
        for w in widgets:
            w.blockSignals(False)
        self._debounce.stop()
        self.changed.emit()
