"""NMEA 2000 frame parsing utilities."""
from __future__ import annotations

from dataclasses import dataclass

from nemafiddler.can_reader import RawFrame

PGN_NAMES: dict[int, str] = {
    59392:  "ISO Ack",
    59904:  "ISO Request",
    60928:  "Address Claim",
    65240:  "Commanded Address",
    126208: "NMEA Request/Command",
    126464: "PGN List",
    126992: "System Time",
    126996: "Product Info",
    127245: "Rudder",
    127250: "Vessel Heading",
    127251: "Rate of Turn",
    127257: "Attitude",
    128259: "Speed Through Water",
    128267: "Depth",
    129025: "Position Rapid Update",
    129026: "COG & SOG",
    129029: "GNSS Position",
    130306: "Wind Data",
}


@dataclass(slots=True)
class N2KFrame:
    raw: RawFrame
    pgn: int
    sa: int       # source address (0–253)
    dst: int      # destination address (255 = broadcast)
    priority: int


def pgn_from_id(arb_id: int) -> int:
    dp = (arb_id >> 24) & 0x01
    pf = (arb_id >> 16) & 0xFF
    ps = (arb_id >>  8) & 0xFF
    return ((dp << 16) | (pf << 8) | ps) if pf >= 240 else ((dp << 16) | (pf << 8))


def parse(frame: RawFrame) -> N2KFrame | None:
    """Return N2KFrame for extended-ID frames; None for standard CAN frames."""
    if not frame.is_extended_id:
        return None
    arb      = frame.arbitration_id
    priority = (arb >> 26) & 0x07
    dp       = (arb >> 24) & 0x01
    pf       = (arb >> 16) & 0xFF
    ps       = (arb >>  8) & 0xFF
    sa       =  arb        & 0xFF
    pgn      = ((dp << 16) | (pf << 8) | ps) if pf >= 240 else ((dp << 16) | (pf << 8))
    dst      = ps if pf < 240 else 255
    return N2KFrame(raw=frame, pgn=pgn, sa=sa, dst=dst, priority=priority)


def pgn_name(pgn: int) -> str:
    return PGN_NAMES.get(pgn, "")
