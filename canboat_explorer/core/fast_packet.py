"""Fast-packet reassembler for NMEA 2000 multi-frame messages.

Detection: a frame is treated as FP frame-0 when
  - it is an extended-ID (N2K) frame
  - bits 4–0 of data[0] == 0  (frame index = 0)
  - 7 <= data[1] <= 223        (plausible total payload length)
  - PGN is known-Fast OR unknown (known non-Fast PGNs are passed through as-is)

Buffers are keyed by (sa, pgn, seq).  A buffer is flushed as a
single-frame message if no continuation arrives within _STALE_S seconds
of the *previous* frame in that sequence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from canboat_explorer.core.n2k_frame_info import n2k_frame_info
from canboat_explorer.core.raw_frame import RawFrame

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
    is_complete: bool = True   # False when the sequence timed out before completing
    decoded:     bool = False  # True if nmea2000 decoder returned a NMEA2000Message
    frames:      list[RawFrame] = field(default_factory=list)


@dataclass
class _Buffer:
    pgn:                 int
    sa:                  int
    priority:            int
    seq:                 int
    expected_len:        int
    payload:             bytearray
    frame_count:         int
    start_ts:            float
    last_frame_ts:       float
    first_frame:         RawFrame
    contributing_frames: list[RawFrame] = field(default_factory=list)


class FastPacketReassembler:
    def __init__(self) -> None:
        self._buffers: dict[tuple[int, int, int], _Buffer] = {}

    def feed(self, msg: RawFrame) -> list[N2KMessage]:
        """Process one CAN frame; return any N2KMessages that became complete."""
        n2k = n2k_frame_info(msg)
        if n2k is None or len(msg.data) < 2:
            return []

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
                is_complete=False,
                frames=buf.contributing_frames,
            ))

        key = (n2k.sa, n2k.pgn, n2k.seq)

        if n2k.frame_idx == 0:
            length = msg.data[1]
            if 7 <= length <= 223 and n2k.is_fast:
                self._buffers[key] = _Buffer(
                    pgn=n2k.pgn, sa=n2k.sa, priority=n2k.priority, seq=n2k.seq,
                    expected_len=length,
                    payload=bytearray(msg.data[2:8]),
                    frame_count=1,
                    start_ts=msg.timestamp,
                    last_frame_ts=msg.timestamp,
                    first_frame=msg,
                    contributing_frames=[msg],
                )
            else:
                results.append(N2KMessage(
                    pgn=n2k.pgn, sa=n2k.sa, priority=n2k.priority,
                    timestamp=msg.timestamp,
                    payload=bytes(msg.data[:msg.dlc]),
                    frame_count=1,
                    frames=[msg],
                ))
        else:
            buf = self._buffers.get(key)
            if buf is not None:
                buf.payload.extend(msg.data[1:8])
                buf.frame_count += 1
                buf.last_frame_ts = msg.timestamp
                buf.contributing_frames.append(msg)
                if len(buf.payload) >= buf.expected_len:
                    self._buffers.pop(key)
                    results.append(N2KMessage(
                        pgn=buf.pgn, sa=buf.sa, priority=buf.priority,
                        timestamp=buf.start_ts,
                        payload=bytes(buf.payload[:buf.expected_len]),
                        frame_count=buf.frame_count,
                        frames=buf.contributing_frames,
                    ))
            else:
                # No matching buffer: normal single-frame message whose first
                # data byte happens to have non-zero low 5 bits, or an orphaned
                # continuation after a timeout.  Emit as-is so nothing is lost.
                results.append(N2KMessage(
                    pgn=n2k.pgn, sa=n2k.sa, priority=n2k.priority,
                    timestamp=msg.timestamp,
                    payload=bytes(msg.data[:msg.dlc]),
                    frame_count=1,
                    frames=[msg],
                ))

        return results
