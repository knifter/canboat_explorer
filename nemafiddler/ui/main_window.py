from __future__ import annotations

import queue

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox, QLabel, QLineEdit, QMainWindow, QPushButton,
    QStatusBar, QTabWidget, QToolBar, QWidget,
)

from nemafiddler.bus.can_reader import CanReader, RawFrame
from nemafiddler.core.paths import DATA_DIR
from nemafiddler.core.session_log import SessionLog
from nemafiddler.core.store import DataStore
from nemafiddler.ui.tab_raw_can import RawCanTab

_INTERFACES = ["waveshare", "slcan", "gs_usb", "pcan", "socketcan"]
_DRAIN_INTERVAL_MS = 50   # drain queue at 20 Hz


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NemaFiddler")
        self.resize(1200, 700)

        self._reader: CanReader | None = None
        self._frame_queue: queue.Queue[RawFrame] = queue.Queue()

        self._init_store()
        self._build_toolbar()
        self._build_tabs()
        self._build_statusbar()
        self._build_timer()
        self._tab_raw.on_frames_added()

    # ------------------------------------------------------------------
    # Store / session log
    # ------------------------------------------------------------------

    def _init_store(self) -> None:
        log_path = DATA_DIR / "session.canlog"
        self._log = SessionLog(log_path, append=True)
        sidecar   = log_path.with_suffix(".json")
        self._store = DataStore(self._log, sidecar)

        existing = self._log.load()
        if existing:
            self._store.bulk_load(existing)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Connection")
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel("Interface: "))
        self._iface_combo = QComboBox()
        self._iface_combo.addItems(_INTERFACES)
        tb.addWidget(self._iface_combo)

        tb.addWidget(QLabel("  Channel: "))
        self._channel_edit = QLineEdit("COM7")
        self._channel_edit.setFixedWidth(100)
        tb.addWidget(self._channel_edit)

        tb.addSeparator()

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        tb.addWidget(self._connect_btn)

        tb.addSeparator()

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        tb.addWidget(self._pause_btn)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _build_tabs(self) -> None:
        self._tabs = QTabWidget()

        self._tab_raw = RawCanTab(self._store)
        self._tabs.addTab(self._tab_raw, "Raw CAN")

        for name in ("NMEA 2000", "Network", "Decoded Values"):
            self._tabs.addTab(
                QLabel(f"[{name} — not yet implemented]"), name
            )

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

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _on_connect_clicked(self) -> None:
        if self._reader is not None:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        iface   = self._iface_combo.currentText()
        channel = self._channel_edit.text().strip()

        self._reader = CanReader(iface, channel, self._frame_queue)
        self._reader.start()

        self._connect_btn.setText("Disconnect")
        self._pause_btn.setEnabled(True)
        self._status_label.setText(f"Connected — {iface} / {channel}")
        self._iface_combo.setEnabled(False)
        self._channel_edit.setEnabled(False)

        # Check shortly if the reader failed to open the bus
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

        self._connect_btn.setText("Connect")
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("Pause")
        self._status_label.setText("Disconnected")
        self._iface_combo.setEnabled(True)
        self._channel_edit.setEnabled(True)

    # ------------------------------------------------------------------
    # Pause / Continue
    # ------------------------------------------------------------------

    def _on_pause_clicked(self) -> None:
        if self._reader is None:
            return
        if self._reader.is_paused:
            self._reader.resume()
            self._pause_btn.setText("Pause")
            self._status_label.setText(
                f"Connected — {self._iface_combo.currentText()} / {self._channel_edit.text()}"
            )
        else:
            self._reader.pause()
            self._pause_btn.setText("Continue")
            self._status_label.setText("Paused")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._reader:
            self._reader.stop()
        self._log.close()
        super().closeEvent(event)
