import importlib.resources
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from canboat_explorer.ui.main_window import MainWindow


def _icon_path() -> str:
    # Resolves in both source and frozen (PyInstaller) builds — package data is
    # collected under canboat_explorer/, same as canboat.json.
    return str(importlib.resources.files("canboat_explorer") / "icon.ico")


def main() -> None:
    # Run via python.exe the taskbar button inherits the interpreter's
    # AppUserModelID (and its icon), ignoring our window icon — so give the
    # process its own identity. Skip this for the frozen .exe: there the exe is
    # already its own identity with an embedded icon, and a bare AppID would
    # break the pinned-to-taskbar icon. Must happen before the first window.
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "canboat_explorer")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(_icon_path()))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
