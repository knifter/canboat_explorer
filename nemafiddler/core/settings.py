"""
Application settings backed by an .ini file next to the project root.

Settings are written back to disk on every change so they survive crashes.
Defaults are applied when the file does not exist or a key is missing.
"""
from __future__ import annotations

import configparser
from pathlib import Path

# Two levels up from nemafiddler/core/ → project root
_APP_DIR  = Path(__file__).resolve().parents[2]
_INI_PATH = _APP_DIR / "nemafiddler.ini"

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

    def _save(self) -> None:
        with _INI_PATH.open("w", encoding="utf-8") as fh:
            self._cfg.write(fh)


settings = Settings()
