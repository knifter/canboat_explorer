from PyQt6.QtWidgets import QMainWindow, QTabWidget, QLabel


class MainWindow(QMainWindow):
    """Top-level window — stub until step 6."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NemaFiddler")
        self.resize(1200, 700)

        tabs = QTabWidget()
        for name in ("Raw CAN", "NMEA 2000", "Network", "Decoded Values"):
            tabs.addTab(QLabel(f"[{name} — not yet implemented]"), name)
        self.setCentralWidget(tabs)
