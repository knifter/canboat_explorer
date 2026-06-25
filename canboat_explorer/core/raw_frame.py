"""RawFrame: can.Message subclass with a back-link slot to its assembled N2KMessage."""
from __future__ import annotations

from typing import TYPE_CHECKING

import can

if TYPE_CHECKING:
    from canboat_explorer.core.fast_packet import N2KMessage


class RawFrame(can.Message):
    __slots__ = ('n2k_msg', 'bits')

    def __init__(self, *args, n2k_msg: "N2KMessage | None" = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.n2k_msg = n2k_msg
        self.bits    = self._compute_bits()

    def _compute_bits(self) -> int:
        """Approximate number of bits this CAN frame occupies on the wire.

        Counts framing overhead (SOF / arbitration / control / CRC / ACK / EOF / IFS)
        plus the data bytes, plus an average bit-stuffing allowance of ~1 in 5 over
        the stuffable range. Used for bus-utilization calculations.

        Error frames return 0 (they consume bus time but in non-standard quantities).
        """
        if self.is_error_frame:
            return 0
        dlc = max(0, min(self.dlc, 8))
        data_bits = 8 * dlc
        if self.is_extended_id:
            # SOF(1) + base ID(11) + SRR(1) + IDE(1) + ext ID(18) + RTR(1) + r1(1) + r0(1) + DLC(4) = 39
            # + CRC(15) + CRC delim(1) + ACK(2) + EOF(7) + IFS(3) = 28  →  67 + 8·DLC
            base      = 67
            stuffable = 34 + data_bits
        else:
            # SOF(1) + ID(11) + RTR(1) + IDE(1) + r0(1) + DLC(4) = 19
            # + CRC(15) + CRC delim(1) + ACK(2) + EOF(7) + IFS(3) = 28  →  47 + 8·DLC
            base      = 47
            stuffable = 19 + data_bits
        return base + data_bits + stuffable // 5

    @property
    def edp(self) -> int | None:
        """Extended Data Page bit (bit 25 of the 29-bit arbitration ID).

        Pure CAN / J1939 link-layer structure — present on every extended frame
        regardless of NMEA 2000 conformance (N2K simply requires it to be 0; a 1
        marks a non-N2K extended frame). None for standard 11-bit frames, which
        have no EDP bit.
        """
        if not self.is_extended_id:
            return None
        return (self.arbitration_id >> 25) & 1

    @classmethod
    def wrap(cls, msg: can.Message) -> "RawFrame":
        return cls(
            timestamp=msg.timestamp,
            arbitration_id=msg.arbitration_id,
            is_extended_id=msg.is_extended_id,
            is_remote_frame=msg.is_remote_frame,
            is_error_frame=msg.is_error_frame,
            dlc=msg.dlc,
            data=msg.data,
            is_fd=msg.is_fd,
            is_rx=msg.is_rx,
            channel=msg.channel,
            bitrate_switch=msg.bitrate_switch,
            error_state_indicator=msg.error_state_indicator,
        )
