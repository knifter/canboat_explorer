"""NMEA 2000 frame-level decoding: CAN frame → field-level info."""
from __future__ import annotations

from typing import NamedTuple

from canboat_explorer.core.pgn_catalogue import PGN_LOOKUP
from canboat_explorer.core.raw_frame import RawFrame


class N2KFrameInfo(NamedTuple):
    pgn:         int
    description: str           # PGN description from canboat; "" if unknown
    sa:          int
    priority:    int
    dp:          int           # bit 24: Data Page (high bit of the PGN)
    pf:          int           # bits 23–16: PDU Format (<240 = PDU1, ≥240 = PDU2)
    ps:          int           # bits 15–8: PDU Specific (destination if PDU1, group ext if PDU2)
    dst:         int | None    # PDU1: destination address; PDU2: None (broadcast)
    grp:         int | None    # PDU2: Group Extension; PDU1: None
    is_fast:     bool          # fast-packet: catalogue Type=="Fast", or proprietary DP=1 range
    seq:         int | None    # fast-packet session counter; None if not is_fast / no data
    frame_idx:   int | None    # fast-packet frame number;   None if not is_fast / no data


def n2k_frame_info(msg: RawFrame) -> N2KFrameInfo | None:
    """Return frame-level fields for conformant NMEA 2000 frames; None otherwise.
    """
    if not msg.is_extended_id:  # NMEA 2000 alwas are exteneded frames
        return None
    if msg.is_remote_frame:     # and never remote (without data)
        return None
    if msg.is_fd:               # and none flexible-data-rate (>8 bytes)
        return None
    if msg.edp:                 # and dont have extended data page set
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

    entry = PGN_LOOKUP.get(pgn)
    if entry is not None:                       # known PGN: trust the catalogue
        description = entry.get("Description", "")
        is_fast     = entry.get("Type") in (None, "Fast")
    else:
        # Unknown PGN ⇒ manufacturer-proprietary. Those ranges are single- vs
        # fast-packet purely by Data Page (PF 0xEF/0xFF marks the proprietary range):
        #   DP=0 → single (61184 / 65280–65535);  DP=1 → fast (126720 / 130816–131071)
        description = ""
        is_fast     = dp == 1 and pf in (0xEF, 0xFF)

    if is_fast and len(msg.data) > 0:
        seq       = (msg.data[0] >> 5) & 0x07
        frame_idx =  msg.data[0]       & 0x1F
    else:
        seq = frame_idx = None

    return N2KFrameInfo(pgn, description, sa, priority, dp, pf, ps,
                        dst, grp, is_fast, seq, frame_idx)
