"""PGN catalogue loaded from bundled canboat.json."""
from __future__ import annotations

import importlib.resources
import json


def _load_pgn_lookup() -> tuple[dict[int, dict], int]:
    path = importlib.resources.files("canboat_explorer") / "canboat.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    result: dict[int, dict] = {}
    for entry in data["PGNs"]:
        result.setdefault(entry["PGN"], entry)  # first occurrence wins
    return result, len(data["PGNs"])


PGN_LOOKUP, PGN_COUNT = _load_pgn_lookup()


def pgn_name(pgn: int) -> str:
    entry = PGN_LOOKUP.get(pgn)
    return entry["Description"] if entry else ""
