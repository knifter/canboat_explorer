"""
Application settings backed by an .ini file next to the application.

Settings are written back to disk on every change so they survive crashes.
Defaults are applied when the file does not exist or a key is missing.
"""
from __future__ import annotations

import configparser
import sys
from pathlib import Path

# Application directory:
# - PyInstaller frozen build: directory of the executable (sys.executable points
#   to the .exe; __file__ would point into the transient _MEIPASS extraction dir)
# - Running from source: two levels up from canboat_explorer/core/ → project root
if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = Path(__file__).resolve().parents[2]

_INI_PATH = _APP_DIR / "canboat_explorer.ini"

_DEFAULTS: dict[str, dict[str, str]] = {
    "data": {
        "data_dir": str(_APP_DIR / "data"),
    },
    "connection": {
        "last_interface":  "waveshare",
        "last_port":       "COM7",
        "last_serial_baud": "2000000",
        "last_can_baud":    "250000",
    },
    "window": {
        "width":  "1200",
        "height": "700",
        "x":      "-1",   # -1 = not yet saved, let the OS place
        "y":      "-1",
    },
}


class Settings:
    def __init__(self) -> None:
        self._cfg = configparser.ConfigParser()
        for section, values in _DEFAULTS.items():
            self._cfg[section] = values
        self._cfg.read(_INI_PATH, encoding="utf-8")

    # ------------------------------------------------------------------
    # [data]
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        return Path(self._cfg["data"]["data_dir"])

    @data_dir.setter
    def data_dir(self, value: Path) -> None:
        self._cfg["data"]["data_dir"] = str(value)
        self._save()

    # ------------------------------------------------------------------
    # [connection]
    # ------------------------------------------------------------------

    @property
    def last_interface(self) -> str:
        return self._cfg["connection"]["last_interface"]

    @last_interface.setter
    def last_interface(self, value: str) -> None:
        self._cfg["connection"]["last_interface"] = value
        self._save()

    @property
    def last_port(self) -> str:
        return self._cfg["connection"]["last_port"]

    @last_port.setter
    def last_port(self, value: str) -> None:
        self._cfg["connection"]["last_port"] = value
        self._save()

    @property
    def last_serial_baud(self) -> int:
        return int(self._cfg["connection"]["last_serial_baud"])

    @last_serial_baud.setter
    def last_serial_baud(self, value: int) -> None:
        self._cfg["connection"]["last_serial_baud"] = str(value)
        self._save()

    @property
    def last_can_baud(self) -> int:
        return int(self._cfg["connection"]["last_can_baud"])

    @last_can_baud.setter
    def last_can_baud(self, value: int) -> None:
        self._cfg["connection"]["last_can_baud"] = str(value)
        self._save()

    # ------------------------------------------------------------------
    # [window]
    # ------------------------------------------------------------------

    @property
    def window_width(self) -> int:
        return int(self._cfg["window"]["width"])

    @window_width.setter
    def window_width(self, value: int) -> None:
        self._cfg["window"]["width"] = str(value)
        self._save()

    @property
    def window_height(self) -> int:
        return int(self._cfg["window"]["height"])

    @window_height.setter
    def window_height(self, value: int) -> None:
        self._cfg["window"]["height"] = str(value)
        self._save()

    @property
    def window_x(self) -> int:
        return int(self._cfg["window"]["x"])

    @window_x.setter
    def window_x(self, value: int) -> None:
        self._cfg["window"]["x"] = str(value)
        self._save()

    @property
    def window_y(self) -> int:
        return int(self._cfg["window"]["y"])

    @window_y.setter
    def window_y(self, value: int) -> None:
        self._cfg["window"]["y"] = str(value)
        self._save()

    # ------------------------------------------------------------------

    def _save(self) -> None:
        with _INI_PATH.open("w", encoding="utf-8") as fh:
            self._cfg.write(fh)


settings = Settings()
