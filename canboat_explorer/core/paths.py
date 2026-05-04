"""Central location for application data paths."""
from __future__ import annotations

from canboat_explorer.core.settings import settings


def data_dir():
    d = settings.data_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


# Keep DATA_DIR as a module-level convenience; callers that need the live
# value (e.g. after a settings change) should call data_dir() directly.
DATA_DIR = data_dir()
