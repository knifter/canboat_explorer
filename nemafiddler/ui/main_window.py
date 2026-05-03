from __future__ import annotations

import queue
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFileDialog, QLabel, QMainWindow, QMessageBox,
    QPushButton, QStatusBar, QStyle, QTabWidget, QToolBar, QWidget,
)

import can
from nemafiddler.bus.can_reader import CanReader
from nemafiddler.core.paths import data_dir
from nemafiddler.core.session_log import SessionLog
from nemafiddler.core.settings import settings
from nemafiddler.core.store import DataStore
from nemafiddler.ui.settings_dialog import SettingsDialog
from nemafiddler.ui.tab_n2k import N2KTab
from nemafiddler.ui.tab_raw_can import RawCanTab

_DRAIN_INTERVAL_MS = 50
_CANLOG_FILTER     = "CAN Log (*.canlog);;All files (*)"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1200, 700)

        self._reader: CanReader | None = None
        self._frame_queue: queue.Queue[can.Message] = queue.Queue()
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
        self._act_clear    = QAction(sp(QStyle.StandardPixmap.SP_TrashIcon),            "Clear",     self)
        self._act_settings = QAction(sp(QStyle.StandardPixmap.SP_FileDialogDetailedView), "Settings…", self)
        self._act_exit     = QAction("Exit", self)
        self._act_open.setShortcut("Ctrl+O")
        self._act_save.setShortcut("Ctrl+S")
        self._act_clear.setShortcut("Ctrl+L")
        self._act_exit.setShortcut("Ctrl+Q")
        self._act_open.triggered.connect(self._action_open)
        self._act_save.triggered.connect(self._action_save)
        self._act_clear.triggered.connect(self._action_clear)
        self._act_settings.triggered.connect(self._action_settings)
        self._act_exit.triggered.connect(self.close)

    def _build_menubar(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        file_menu.addAction(self._act_open)
        file_menu.addAction(self._act_save)
        file_menu.addSeparator()
        file_menu.addAction(self._act_clear)
        file_menu.addSeparator()
        file_menu.addAction(self._act_settings)
        file_menu.addSeparator()
        file_menu.addAction(self._act_exit)
        about_menu = mb.addMenu("About")
        act = QAction("About NemaFiddler", self)
        act.triggered.connect(self._show_about)
        about_menu.addAction(act)

    def _show_about(self) -> None:
        QMessageBox.about(self, "About NemaFiddler",
            "<b>NemaFiddler</b><br>"
            "NMEA 2000 / CAN bus explorer<br><br>"
            "Reads live CAN traffic via Waveshare USB-CAN-A and other adapters,<br>"
            "decodes NMEA 2000 frames, and lets you inspect, filter, and save sessions.")

    # ------------------------------------------------------------------
    # Toolbars
    # ------------------------------------------------------------------

    def _build_file_toolbar(self) -> None:
        tb = QToolBar("File")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(self._act_open)
        tb.addAction(self._act_save)
        tb.addAction(self._act_clear)
        tb.addSeparator()
        tb.addAction(self._act_settings)

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

    def _build_tabs(self) -> None:
        self._tabs = QTabWidget()
        self._tab_raw = RawCanTab(self._store)
        self._tab_n2k = N2KTab(self._store)
        self._tabs.addTab(self._tab_raw, "Raw CAN")
        self._tabs.addTab(self._tab_n2k, "NMEA 2000")
        for name in ("Network", "Decoded Values"):
            self._tabs.addTab(QLabel(f"[{name} — not yet implemented]"), name)
        self.setCentralWidget(self._tabs)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_label = QLabel("Disconnected")
        sb.addWidget(self._status_label)

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
        if self._frame_queue.empty():
            return
        batch: list[can.Message] = []
        try:
            while True:
                batch.append(self._frame_queue.get_nowait())
        except queue.Empty:
            pass
        for frame in batch:
            self._store.ingest(frame)
        self._tab_raw.on_frames_added()
        self._tab_n2k.on_messages_added()

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
        self._tab_raw.on_frames_added()
        self._tab_n2k.reset()

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
        if self._store.frames:
            reply = QMessageBox.question(
                self, "Clear", "Discard all frames in the current session?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        active_path = self._store.log.path
        self._store.log.close()
        # Truncate: reopen in write mode then switch back to append
        active_path.write_bytes(b"")
        self._store.log = SessionLog(active_path, append=True)
        self._store.reset_memory()
        self._tab_raw.on_frames_added()
        self._tab_n2k.reset()

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
            self.setWindowTitle("NemaFiddler — session")
        else:
            self.setWindowTitle(f"NemaFiddler — {path.name}")

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
        super().closeEvent(event)
