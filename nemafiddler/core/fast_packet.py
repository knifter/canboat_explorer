"""Fast-packet reassembler for NMEA 2000 multi-frame messages.

Detection is heuristic: a frame is treated as FP frame-0 when
  - it is an extended-ID (N2K) frame
  - bits 4–0 of data[0] == 0  (frame index = 0)
  - 7 <= data[1] <= 223        (plausible total payload length)

Buffers are keyed by (sa, pgn, seq).  A buffer is flushed as a
single-frame message if no continuation arrives within _STALE_S seconds
of the *previous* frame in that sequence.
"""
from __future__ import annotations

from dataclasses import dataclass

import can

_STALE_S = 0.010  # 10 ms inter-frame gap → sequence is broken


@dataclass
class N2KMessage:
    pgn:         int
    sa:          int
    priority:    int
    timestamp:   float   # timestamp of the first CAN frame
    payload:     bytes   # complete assembled payload
    frame_count: int     # number of CAN frames that made up this message


@dataclass
class _Buffer:
    pgn:          int
    sa:           int
    priority:     int
    seq:          int
    expected_len: int
    payload:      bytearray
    frame_count:  int
    start_ts:     float
    last_frame_ts: float
    first_frame:  can.Message


class FastPacketReassembler:
    def __init__(self) -> None:
        self._buffers: dict[tuple[int, int, int], _Buffer] = {}

    def feed(self, msg: can.Message) -> list[N2KMessage]:
        """Process one CAN frame; return any N2KMessages that became complete."""
        if not msg.is_extended_id or len(msg.data) < 2:
            return []

        arb      = msg.arbitration_id
        priority = (arb >> 26) & 0x07
        dp       = (arb >> 24) & 0x01
        pf       = (arb >> 16) & 0xFF
        ps       = (arb >>  8) & 0xFF
        sa       =  arb        & 0xFF
        pgn      = ((dp << 16) | (pf << 8) | ps) if pf >= 240 else ((dp << 16) | (pf << 8))

        results: list[N2KMessage] = []

        # Flush buffers whose last frame is more than _STALE_S seconds old
        stale = [k for k, b in self._buffers.items()
                 if msg.timestamp - b.last_frame_ts > _STALE_S]
        for k in stale:
            buf = self._buffers.pop(k)
            results.append(N2KMessage(
                pgn=buf.pgn, sa=buf.sa, priority=buf.priority,
                timestamp=buf.start_ts,
                payload=bytes(buf.first_frame.data[:buf.first_frame.dlc]),
                frame_count=1,
            ))

        frame_idx = msg.data[0] & 0x1F
        seq       = (msg.data[0] >> 5) & 0x07
        key       = (sa, pgn, seq)

        if frame_idx == 0:
            length = msg.data[1]
            if 7 <= length <= 223:
                self._buffers[key] = _Buffer(
                    pgn=pgn, sa=sa, priority=priority, seq=seq,
                    expected_len=length,
                    payload=bytearray(msg.data[2:8]),
                    frame_count=1,
                    start_ts=msg.timestamp,
                    last_frame_ts=msg.timestamp,
                    first_frame=msg,
                )
            else:
                results.append(N2KMessage(
                    pgn=pgn, sa=sa, priority=priority,
                    timestamp=msg.timestamp,
                    payload=bytes(msg.data[:msg.dlc]),
                    frame_count=1,
                ))
        else:
            buf = self._buffers.get(key)
            if buf is not None:
                buf.payload.extend(msg.data[1:8])
                buf.frame_count += 1
                buf.last_frame_ts = msg.timestamp
                if len(buf.payload) >= buf.expected_len:
                    self._buffers.pop(key)
                    results.append(N2KMessage(
                        pgn=buf.pgn, sa=buf.sa, priority=buf.priority,
                        timestamp=buf.start_ts,
                        payload=bytes(buf.payload[:buf.expected_len]),
                        frame_count=buf.frame_count,
                    ))
            else:
                # No matching buffer: normal single-frame message whose first
                # data byte happens to have non-zero low 5 bits, or an orphaned
                # continuation after a timeout.  Emit as-is so nothing is lost.
                results.append(N2KMessage(
                    pgn=pgn, sa=sa, priority=priority,
                    timestamp=msg.timestamp,
                    payload=bytes(msg.data[:msg.dlc]),
                    frame_count=1,
                ))

        return results
