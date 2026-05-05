"""NMEA 2000 frame parsing and PGN metadata loaded from bundled canboat.json."""
from __future__ import annotations

import importlib.resources
import json

import can


def _load_pgn_lookup() -> dict[int, dict]:
    path = importlib.resources.files("canboat_explorer") / "canboat.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    result: dict[int, dict] = {}
    for entry in data["PGNs"]:
        result.setdefault(entry["PGN"], entry)  # first occurrence wins
    return result


PGN_LOOKUP: dict[int, dict] = _load_pgn_lookup()


def pgn_name(pgn: int) -> str:
    entry = PGN_LOOKUP.get(pgn)
    return entry["Description"] if entry else ""


def pgn_from_id(arbitration_id: int) -> int:
    dp = (arbitration_id >> 24) & 0x01
    pf = (arbitration_id >> 16) & 0xFF
    ps = (arbitration_id >>  8) & 0xFF
    return ((dp << 16) | (pf << 8) | ps) if pf >= 240 else ((dp << 16) | (pf << 8))


def parse(msg: can.Message) -> tuple[int, int, int, int] | None:
    """Return (pgn, sa, priority, dst) for extended-ID frames; None for standard."""
    if not msg.is_extended_id:
        return None
    arb      = msg.arbitration_id
    priority = (arb >> 26) & 0x07
    dp       = (arb >> 24) & 0x01
    pf       = (arb >> 16) & 0xFF
    ps       = (arb >>  8) & 0xFF
    sa       =  arb        & 0xFF
    pgn      = ((dp << 16) | (pf << 8) | ps) if pf >= 240 else ((dp << 16) | (pf << 8))
    dst      = ps if pf < 240 else 255
    return (pgn, sa, priority, dst)
