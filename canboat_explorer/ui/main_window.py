from __future__ import annotations

import queue
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QAction, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QFileDialog, QLabel, QMainWindow, QMessageBox,
    QPushButton, QStatusBar, QStyle, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from canboat_explorer.bus.can_reader import CanReader
from canboat_explorer.core.paths import data_dir
from canboat_explorer.core.raw_frame import RawFrame
from canboat_explorer.core.session_log import SessionLog
from canboat_explorer.core.settings import settings
from canboat_explorer.core.store import DataStore
from canboat_explorer.ui.filter_bar import FilterBar
from canboat_explorer.ui.settings_dialog import SettingsDialog
from canboat_explorer.ui.tab_decoded import DecodedTab
from canboat_explorer.ui.tab_n2k import N2KTab
from canboat_explorer.ui.tab_network import NetworkTab
from canboat_explorer.ui.tab_raw_can import RawCanTab

_DRAIN_INTERVAL_MS = 50
_CANLOG_FILTER     = "CAN Log (*.canlog);;All files (*)"

# Soft amber on the filter-bar toggle while the filter is actively hiding rows —
# matches the in-bar On/Off tint, so it stays visible even when the bar is hidden.
_FILTER_ACTIVE_STYLE = "QToolButton { background-color: #e6b24a; color: black; }"


def _glyph_icon(glyph: str, side: int) -> QIcon:
    """Render a text glyph (e.g. an emoji) into a square icon so it rides the
    same centered/sized icon pipeline as the style's standard toolbar icons —
    a text-only tool button positions the glyph by baseline and sits too low."""
    pm = QPixmap(side, side)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    font = painter.font()
    font.setPixelSize(int(side * 0.8))
    painter.setFont(font)
    painter.setPen(QColor("#dddddd"))   # visible fallback if rendered monochrome
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(settings.window_width, settings.window_height)
        if settings.window_x >= 0 and settings.window_y >= 0:
            self.move(settings.window_x, settings.window_y)

        self._reader: CanReader | None = None
        self._frame_queue: queue.Queue[RawFrame] = queue.Queue()
        self._session_path = data_dir() / "session.canlog"

        self._init_store()
        self._build_actions()
        self._build_menubar()
        self._build_file_toolbar()
        self._build_conn_toolbar()
        self._build_tabs()
        self._build_statusbar()
        self._build_timer()
        self._update_title()
        self._tab_raw.on_frames_added()
        self._tab_n2k.on_messages_added()
        self._tab_network.on_messages_added()
        self._tab_decoded.on_messages_added()
        self._update_tab_warnings()

    # ------------------------------------------------------------------
    # Store / session log
    # ------------------------------------------------------------------

    def _init_store(self) -> None:
        self._log = SessionLog(self._session_path, append=True)
        sidecar   = self._session_path.with_suffix(".json")
        self._store = DataStore(self._log, sidecar)
        existing = self._log.load()
        if existing:
            self._store.bulk_load(existing)

    # ------------------------------------------------------------------
    # Actions / menu bar
    # ------------------------------------------------------------------

    def _build_actions(self) -> None:
        sp = self.style().standardIcon
        self._act_open     = QAction(sp(QStyle.StandardPixmap.SP_DialogOpenButton),    "Open…",     self)
        self._act_save     = QAction(sp(QStyle.StandardPixmap.SP_DialogSaveButton),    "Save as…",  self)
        self._act_clear    = QAction(sp(QStyle.StandardPixmap.SP_FileIcon),               "New",       self)
        self._act_settings = QAction(sp(QStyle.StandardPixmap.SP_FileDialogDetailedView), "Settings…", self)
        self._act_exit     = QAction("Exit", self)
        self._act_open.setShortcut("Ctrl+O")
        self._act_save.setShortcut("Ctrl+S")
        self._act_clear.setShortcut("Ctrl+N")
        self._act_exit.setShortcut("Ctrl+Q")
        self._act_open.triggered.connect(self._action_open)
        self._act_save.triggered.connect(self._action_save)
        self._act_clear.triggered.connect(self._action_clear)
        self._act_settings.triggered.connect(self._action_settings)
        self._act_exit.triggered.connect(self.close)

    def _build_menubar(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        file_menu.addAction(self._act_clear)
        file_menu.addSeparator()
        file_menu.addAction(self._act_open)
        file_menu.addAction(self._act_save)
        file_menu.addSeparator()
        file_menu.addAction(self._act_settings)
        file_menu.addSeparator()
        file_menu.addAction(self._act_exit)
        about_menu = mb.addMenu("About")
        act = QAction("About Canboat Explorer", self)
        act.triggered.connect(self._show_about)
        about_menu.addAction(act)

    def _show_about(self) -> None:
        QMessageBox.about(self, "About Canboat Explorer",
            "<b>Canboat Explorer</b> — NMEA 2000 / CAN bus explorer<br>"
            "© Tijs van Roon — <a href='https://github.com/knifter'>github.com/knifter</a> — GPL-3.0<br><br>"
            "<b>Third-party components:</b><br>"
            "· <b>PyQt6</b> © Riverbank Computing — GPL-3.0<br>"
            "· <b>CANboat</b> PGN database © 2009-2025 Kees Verruijt — Apache 2.0<br>"
            "&nbsp;&nbsp;<a href='https://github.com/canboat/canboat'>github.com/canboat/canboat</a><br>"
            "· <b>nmea2000</b> © Tomer-w — Apache 2.0<br>"
            "&nbsp;&nbsp;<a href='https://github.com/tomer-w/nmea2000'>github.com/tomer-w/nmea2000</a><br>"
            "· <b>python-can</b> © python-can contributors — LGPL-3.0<br>"
            "&nbsp;&nbsp;<a href='https://github.com/hardbyte/python-can'>github.com/hardbyte/python-can</a>")

    # ------------------------------------------------------------------
    # Toolbars
    # ------------------------------------------------------------------

    def _build_file_toolbar(self) -> None:
        tb = QToolBar("File")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(self._act_clear)
        tb.addAction(self._act_open)
        tb.addAction(self._act_save)
        tb.addSeparator()
        tb.addAction(self._act_settings)
        tb.addSeparator()

        isz  = tb.iconSize()
        side = isz.width() if isz.width() > 0 else 24
        self._act_filter = QAction(_glyph_icon("🔍", side), "Filter bar", self)
        self._act_filter.setToolTip("Show/hide the filter bar")
        self._act_filter.triggered.connect(self._toggle_filter_bar)
        tb.addAction(self._act_filter)
        self._filter_toggle_btn = tb.widgetForAction(self._act_filter)

    def _build_conn_toolbar(self) -> None:
        tb = QToolBar("Connection")
        tb.setMovable(False)
        self.addToolBar(tb)

        sp = self.style().standardIcon
        self._connect_btn = QPushButton(sp(QStyle.StandardPixmap.SP_DriveNetIcon), "Connect")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        tb.addWidget(self._connect_btn)

        tb.addSeparator()

        self._pause_btn = QPushButton(sp(QStyle.StandardPixmap.SP_MediaPause), "Pause")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        tb.addWidget(self._pause_btn)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    _TAB_NAMES = ("Raw CAN", "NMEA 2000", "Network", "Decoded Values")

    def _build_tabs(self) -> None:
        self._tabs = QTabWidget()
        self._tab_raw     = RawCanTab(self._store)
        self._tab_n2k     = N2KTab(self._store)
        self._tab_network = NetworkTab(self._store)
        self._tab_decoded = DecodedTab(self._store)
        self._tabs.addTab(self._tab_raw,     self._TAB_NAMES[0])
        self._tabs.addTab(self._tab_n2k,     self._TAB_NAMES[1])
        self._tabs.addTab(self._tab_network, self._TAB_NAMES[2])
        self._tabs.addTab(self._tab_decoded, self._TAB_NAMES[3])

        self._filter_bar = FilterBar()
        self._filter_bar.set_sources(self._filter_pgn_values, self._filter_sa_values)
        self._filter_bar.changed.connect(self._apply_filter)
        self._filter_bar.changed.connect(self._update_filter_indicator)
        self._update_filter_indicator()
        self._filter_bar.setVisible(False)   # hidden until toggled via the toolbar 🔍

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self._filter_bar)
        cl.addWidget(self._tabs)
        self.setCentralWidget(central)

        sc = QShortcut(QKeySequence("Ctrl+F"), self)
        sc.activated.connect(self._filter_bar.focus_search)

    def _apply_filter(self, *_args) -> None:
        spec = self._filter_bar.spec
        self._tab_raw.apply_filter(spec)
        self._tab_n2k.apply_filter(spec)
        self._tab_network.apply_filter(spec)
        self._tab_decoded.apply_filter(spec)

    def _toggle_filter_bar(self, *_args) -> None:
        self._filter_bar.setVisible(not self._filter_bar.isVisible())

    def _update_filter_indicator(self, *_args) -> None:
        # Amber when the filter is actively narrowing results, so the toolbar
        # toggle signals it even while the bar (and its own On/Off tint) is hidden.
        if self._filter_toggle_btn is None:
            return
        active = self._filter_bar.is_active_filter()
        self._filter_toggle_btn.setStyleSheet(_FILTER_ACTIVE_STYLE if active else "")

    def _update_tab_warnings(self) -> None:
        tabs = (self._tab_raw, self._tab_n2k, self._tab_network, self._tab_decoded)
        bar  = self._tabs.tabBar()
        for idx, (tab, name) in enumerate(zip(tabs, self._TAB_NAMES)):
            warn  = tab.has_warnings()
            label = f"⚠ {name}" if warn else name
            if self._tabs.tabText(idx) != label:
                self._tabs.setTabText(idx, label)
            tip = tab.warning_text() if warn else ""
            if bar.tabToolTip(idx) != tip:
                bar.setTabToolTip(idx, tip)

    # ------------------------------------------------------------------
    # Filter-bar value pickers (observed PGNs / SAs, read live)
    # ------------------------------------------------------------------

    def _filter_pgn_values(self) -> list[tuple[int, str]]:
        from canboat_explorer.core.pgn_catalogue import pgn_name
        out: list[tuple[int, str]] = []
        for pgn in sorted(self._store.by_pgn):
            name = pgn_name(pgn)
            out.append((pgn, f"{pgn}  {name}" if name else str(pgn)))
        return out

    def _filter_sa_values(self) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        for sa in sorted(self._store.devices):
            dev = self._store.devices[sa]
            out.append((sa, f"{sa}  —  {dev.model}" if dev.model else str(sa)))
        return out

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        from canboat_explorer.core.pgn_catalogue import PGN_COUNT
        self._status_label = QLabel(f"Disconnected — {PGN_COUNT} PGNs loaded")
        sb.addWidget(self._status_label)

        self._bus_label = QLabel()
        sb.addPermanentWidget(self._bus_label)

        self._bus_timer = QTimer(self)
        self._bus_timer.setInterval(1000)
        self._bus_timer.timeout.connect(self._update_bus_indicator)
        self._bus_timer.start()
        self._update_bus_indicator()

    def _update_bus_indicator(self) -> None:
        # Propagate paused/held state to the tabs' count labels. Both show
        # the same number — frames in the queue. Tab 2 labels it as
        # "frames held" rather than "messages held" because frames only
        # become assembled messages after store._add runs (which is what's
        # paused).
        paused = self._reader is not None and self._reader.is_paused
        held   = self._frame_queue.qsize() if paused else None
        self._tab_raw.set_held(held)
        self._tab_n2k.set_held(held)

        bitrate  = settings.last_can_baud
        rate_str = f"{bitrate // 1000} kbps" if bitrate else "—"
        active   = self._reader is not None and not self._reader.is_paused
        if not active:
            self._bus_label.setText(f"Bus: {rate_str}")
            self._bus_label.setStyleSheet("")
            return
        util = self._store.bus_utilization(bitrate)
        if util is None:
            self._bus_label.setText(f"Bus: {rate_str}")
            self._bus_label.setStyleSheet("")
            return
        pct  = f"{util:.2f}" if util < 5.0 else f"{util:.1f}"
        high = util > 50.0
        prefix = "⚠ " if high else ""
        self._bus_label.setText(f"{prefix}Bus: {pct} % @ {rate_str}")
        self._bus_label.setStyleSheet("color: #d24a4a;" if high else "")

    # ------------------------------------------------------------------
    # Queue drain timer
    # ------------------------------------------------------------------

    def _build_timer(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(_DRAIN_INTERVAL_MS)
        self._timer.timeout.connect(self._drain_queue)
        self._timer.start()

    def _drain_queue(self) -> None:
        if self._reader and not self._reader.is_alive() and self._reader.error:
            err = self._reader.error
            self._disconnect()
            self._status_label.setText(f"Connection error: {err}")
            return
        if self._reader and self._reader.is_paused:
            return   # frames keep accumulating in the queue; resume drains them
        if self._frame_queue.empty():
            return
        batch: list[RawFrame] = []
        try:
            while True:
                batch.append(self._frame_queue.get_nowait())
        except queue.Empty:
            pass
        for frame in batch:
            self._store.ingest(frame)
        self._tab_raw.on_frames_added()
        self._tab_n2k.on_messages_added()
        self._tab_network.on_messages_added()
        self._tab_decoded.on_messages_added()
        self._update_tab_warnings()

    # ------------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------------

    def _action_open(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Open CAN log", str(data_dir()), _CANLOG_FILTER)
        if not path_str:
            return
        path = Path(path_str)
        frames = SessionLog(path, append=False).load()
        if self._reader:
            self._disconnect()
        self._redirect_log(path, write_frames=None)
        self._store.reset_memory()
        self._store.bulk_load(frames)
        self._tab_raw.reset()
        self._tab_n2k.reset()
        self._tab_n2k.on_messages_added()
        self._tab_network.reset()
        self._tab_network.on_messages_added()
        self._tab_decoded.reset()
        self._tab_decoded.on_messages_added()
        self._update_tab_warnings()

    def _action_save(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save CAN log", str(data_dir()), _CANLOG_FILTER)
        if not path_str:
            return
        path = Path(path_str)
        if not path.suffix:
            path = path.with_suffix(".canlog")
        self._redirect_log(path, write_frames=list(self._store.frames))

    def _action_clear(self) -> None:
        on_session = self._store.log.path == self._session_path
        if on_session and self._store.frames:
            reply = QMessageBox.question(
                self, "New", "Discard all frames in the current session?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._store.log.close()
        # Switch back to a fresh session.canlog; any previously-opened file is left untouched
        self._session_path.write_bytes(b"")
        self._store.log = SessionLog(self._session_path, append=True)
        self._store.reset_memory()
        self._tab_raw.reset()
        self._tab_n2k.reset()
        self._tab_network.reset()
        self._tab_decoded.reset()
        self._update_tab_warnings()
        self._update_title()

    def _action_settings(self) -> None:
        dlg = SettingsDialog(self)
        dlg.exec()

    def _redirect_log(self, new_path: Path, write_frames: list | None) -> None:
        """
        Close the current log, optionally write frames to new_path from scratch,
        open new_path in append mode, and clear session.canlog if we just
        moved away from it.
        """
        old_path = self._store.log.path
        self._store.log.close()

        if write_frames is not None:
            SessionLog.write_frames(write_frames, new_path)

        self._store.log = SessionLog(new_path, append=True)

        # Clear session.canlog so the next app start is fresh
        if old_path == self._session_path and new_path != self._session_path:
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass

        self._update_title()

    # ------------------------------------------------------------------
    # Title
    # ------------------------------------------------------------------

    def _update_title(self) -> None:
        path = self._store.log.path
        if path == self._session_path:
            self.setWindowTitle("Canboat Explorer — session")
        else:
            self.setWindowTitle(f"Canboat Explorer — {path.name}")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _on_connect_clicked(self) -> None:
        if self._reader is not None:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        iface   = settings.last_interface
        channel = settings.last_port
        self._reader = CanReader(
            iface, channel, self._frame_queue,
            serial_baud=settings.last_serial_baud,
            can_baud=settings.last_can_baud,
        )
        # Drop stale timestamps loaded from the session log so the live rate is
        # measured from this connection onward, not skewed by the session gap.
        self._store.reset_rate_windows()
        self._reader.start()

        self._connect_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))
        self._connect_btn.setText("Disconnect")
        self._pause_btn.setEnabled(True)
        self._status_label.setText(f"Connected — {iface} / {channel}")

        QTimer.singleShot(500, self._check_reader_error)

    def _check_reader_error(self) -> None:
        if self._reader and self._reader.error:
            err = self._reader.error
            self._disconnect()
            self._status_label.setText(f"Connection failed: {err}")

    def _disconnect(self) -> None:
        if self._reader:
            self._reader.stop()
            self._reader = None

        self._connect_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon))
        self._connect_btn.setText("Connect")
        self._pause_btn.setEnabled(False)
        self._pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self._pause_btn.setText("Pause")
        self._status_label.setText("Disconnected")

    # ------------------------------------------------------------------
    # Pause / Continue
    # ------------------------------------------------------------------

    def _on_pause_clicked(self) -> None:
        if self._reader is None:
            return
        if self._reader.is_paused:
            self._reader.resume()
            self._pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self._pause_btn.setText("Pause")
            self._status_label.setText(
                f"Connected — {settings.last_interface} / {settings.last_port}")
        else:
            self._reader.pause()
            self._pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self._pause_btn.setText("Continue")
            self._status_label.setText("Paused")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._reader:
            self._reader.stop()
        self._store.log.close()
        g = self.geometry()
        settings.window_width  = g.width()
        settings.window_height = g.height()
        settings.window_x      = g.x()
        settings.window_y      = g.y()
        super().closeEvent(event)
