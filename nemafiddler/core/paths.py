"""Central location for application data paths."""
from pathlib import Path
import sys

if sys.platform == "win32":
    _base = Path.home() / "AppData" / "Local" / "NemaFiddler"
else:
    _base = Path.home() / ".local" / "share" / "NemaFiddler"

DATA_DIR = _base
DATA_DIR.mkdir(parents=True, exist_ok=True)
