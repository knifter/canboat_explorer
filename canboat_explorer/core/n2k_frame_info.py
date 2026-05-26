"""NMEA 2000 frame-level decoding: CAN frame → field-level info."""
from __future__ import annotations

from typing import NamedTuple

import can

from canboat_explorer.core.pgn_catalogue import PGN_LOOKUP


class N2KFrameInfo(NamedTuple):
    pgn:         int
    description: str           # PGN description from canboat; "" if unknown
    sa:          int
    priority:    int
    dst:         int | None    # PDU1: destination address; PDU2: None (broadcast)
    grp:         int | None    # PDU2: Group Extension; PDU1: None
    is_fast:     bool          # True if canboat marks PGN as "Fast" or PGN is unknown
    seq:         int | None    # fast-packet session counter; None if not is_fast / no data
    frame_idx:   int | None    # fast-packet frame number;   None if not is_fast / no data


def n2k_frame_info(msg: can.Message) -> N2KFrameInfo | None:
    """Return frame-level fields for extended-ID frames; None for standard."""
    if not msg.is_extended_id:
        return None
    arb      = msg.arbitration_id
    priority = (arb >> 26) & 0x07
    sa       =  arb        & 0xFF
    pf       = (arb >> 16) & 0xFF
    dp       = (arb >> 24) & 0x01
    ps       = (arb >>  8) & 0xFF

    pgn = (dp << 16) | (pf << 8)
    if pf >= 0xF0:          # PDU2 (broadcast)
        pgn = pgn | ps
        dst = None
        grp = ps
    else:                   # PDU1 (addressed)
        dst = ps
        grp = None

    entry       = PGN_LOOKUP.get(pgn) or {}
    description = entry.get("Description", "")
    is_fast     = entry.get("Type") in (None, "Fast")

    if is_fast and len(msg.data) > 0:
        seq       = (msg.data[0] >> 5) & 0x07
        frame_idx =  msg.data[0]       & 0x1F
    else:
        seq = frame_idx = None

    return N2KFrameInfo(pgn, description, sa, priority, dst, grp, is_fast, seq, frame_idx)
