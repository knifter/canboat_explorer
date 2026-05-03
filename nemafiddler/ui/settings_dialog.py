from __future__ import annotations

from serial.tools import list_ports

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QPushButton, QVBoxLayout,
)

from nemafiddler.core.settings import settings

_INTERFACES = ["waveshare", "slcan", "gs_usb", "pcan", "socketcan"]

_SERIAL_BAUDS = [9600, 19200, 38800, 115200, 1228800, 2000000]

_CAN_BAUDS   = [5000, 10000, 20000, 50000, 100000, 125000,
                250000, 400000, 500000, 800000, 1000000]
_CAN_LABELS  = ["5k", "10k", "20k", "50k", "100k", "125k",
                "250k", "400k", "500k", "800k", "1M"]


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connection Settings")
        self.setMinimumWidth(380)

        # Interface
        self._iface_combo = QComboBox()
        self._iface_combo.addItems(_INTERFACES)
        idx = self._iface_combo.findText(settings.last_interface)
        if idx >= 0:
            self._iface_combo.setCurrentIndex(idx)

        # Port + refresh
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(220)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_ports)
        self._refresh_ports()

        # COM baud
        self._serial_baud_combo = QComboBox()
        for b in _SERIAL_BAUDS:
            self._serial_baud_combo.addItem(str(b), b)
        idx = self._serial_baud_combo.findData(settings.last_serial_baud)
        if idx >= 0:
            self._serial_baud_combo.setCurrentIndex(idx)

        # CAN bitrate
        self._can_baud_combo = QComboBox()
        for b, label in zip(_CAN_BAUDS, _CAN_LABELS):
            self._can_baud_combo.addItem(label, b)
        idx = self._can_baud_combo.findData(settings.last_can_baud)
        if idx >= 0:
            self._can_baud_combo.setCurrentIndex(idx)

        # OK / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        port_row = QHBoxLayout()
        port_row.addWidget(self._port_combo, stretch=1)
        port_row.addWidget(self._refresh_btn)

        form = QFormLayout()
        form.addRow("Interface:", self._iface_combo)
        form.addRow("Port:", port_row)
        form.addRow("COM Baud:", self._serial_baud_combo)
        form.addRow("CAN Bitrate:", self._can_baud_combo)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------

    def _refresh_ports(self) -> None:
        current = self._port_combo.currentData()
        self._port_combo.clear()
        ports = sorted(list_ports.comports(), key=lambda p: p.device)
        for p in ports:
            label = (f"{p.device}  —  {p.description}"
                     if p.description and p.description != p.device
                     else p.device)
            self._port_combo.addItem(label, p.device)

        # Restore previous selection, fall back to saved setting
        for candidate in (current, settings.last_port):
            if candidate:
                idx = self._port_combo.findData(candidate)
                if idx >= 0:
                    self._port_combo.setCurrentIndex(idx)
                    break

    def _on_ok(self) -> None:
        settings.last_interface  = self._iface_combo.currentText()
        settings.last_port       = self._port_combo.currentData() or ""
        settings.last_serial_baud = self._serial_baud_combo.currentData()
        settings.last_can_baud   = self._can_baud_combo.currentData()
        self.accept()
