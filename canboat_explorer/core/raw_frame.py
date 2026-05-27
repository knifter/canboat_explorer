"""RawFrame: can.Message subclass with a back-link slot to its assembled N2KMessage."""
from __future__ import annotations

from typing import TYPE_CHECKING

import can

if TYPE_CHECKING:
    from canboat_explorer.core.fast_packet import N2KMessage


class RawFrame(can.Message):
    __slots__ = ('n2k_msg',)

    def __init__(self, *args, n2k_msg: "N2KMessage | None" = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.n2k_msg = n2k_msg

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
