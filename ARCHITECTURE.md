# NemaFiddler — Architecture Overview

## Stack

```
Hardware
  Waveshare USB-CAN-A (or slcan, gs_usb, pcan, socketcan)
      │  serial/USB  (tty_baudrate = e.g. 2,000,000 bps)
      │  CAN wire    (bitrate = 250,000 bps for NMEA 2000)
      ▼
Bus Layer
  WaveshareCANBus  ──or──  can.Bus (python-can)
      │  yields can.Message objects
      ▼
  CanReader  (background thread)
      │  sets msg.timestamp = time.time() if not already set by driver
      │  pushes can.Message directly to frame_queue (thread-safe)
      │  recv() exceptions → connection-state signal to UI, not a fake frame
      ▼
  frame_queue → DataStore  (drained every 50 ms on main thread)
      │
      ├── SessionLog                       → disk (.canlog)
      │
      ├── DataStore.frames                 → Tab 1 – Raw CAN
      │       list[can.Message]                time view  (all frames, newest first)
      │                                         accum view (by arb_id / PGN+SA / PGN)
      │
      ├── FastPacketReassembler            → Tab 2 – NMEA 2000
      │       input:  can.Message              time view  (assembled messages, newest first)
      │       heuristic FP detection           accum view (by PGN+SA, count + interval)
      │       10 ms inter-frame stale timeout
      │       → N2KMessage(timestamp, pgn, sa, priority, payload:bytes, frame_count)
      │         dead end — raw bytes only, no field decoding
      │
      ├── FastPacketReassembler (same)     → Tab 3 – Network
      │       filters N2KMessage for:          device tree per SA
      │         PGN 60928  address claim        PGN advertised/observed cross-check
      │         PGN 126464 PGN list             collision + discrepancy markers
      │         PGN 126996 product info
      │
      └── NMEA2000Decoder.decode(msg)      → Tab 4 – Decoded Values
              input: can.Message directly       live signal values (heading, SOG, etc.)
              handles FP internally
              returns None for unknown PGNs (proprietary frames silently skipped)
              → NMEA2000Message(pgn, source, fields...)
```

---

## Key Design Decisions

**`can.Message` stored directly — no `RawFrame` wrapper**
`can.Message` (python-can) already carries all fields we need: `timestamp`, `arbitration_id`,
`dlc`, `data`, `is_extended_id`, `is_remote_frame`, `is_error_frame`. We store it as-is.
No conversion, no shadow class, no padding. DLC is trusted as declared length; a mismatch
is a genuine adapter error, not something to paper over.

**`is_error_frame` replaces custom `is_error`**
`can.Message.is_error_frame=True` means the adapter saw a real CAN bus error (collision,
CRC fail, etc.). This is what we want to surface in Tab 1. The previous synthetic error
frame (injected when `recv()` threw an exception) is replaced by a connection-state signal
to the UI — it was never a real frame.

**No `N2KFrame`**
PGN, SA, priority, and destination are extracted from `arbitration_id` with 3–4 lines of
bit math. There is no state and no reuse beyond one call site in `store.py`. The wrapper
class added indirection without value.

**Two fast-packet consumers, one stream**
`FastPacketReassembler` runs once; both Tab 2 and Tab 3 read from its `N2KMessage` output.
`NMEA2000Decoder` runs its own FP reassembly internally for Tab 4. The two reassemblers
are independent — Tab 2/3 need raw payload bytes; Tab 4 needs decoded field values.
nmea2000 does not expose its intermediate assembled payload.

**nmea2000 (PyPI) for Tab 4 only**
`NMEA2000Decoder.decode(can.Message)` returns `None` for unknown PGNs (no exception).
It is the sole decoder for Tab 4 field values. It does not replace our stack for Tab 1/2/3
because it exposes no raw frames, no timestamps, no frame counts, and no persistence.

---

## What `nmea2000` (PyPI) Provides vs Our Stack

| Concern | Our stack | `nmea2000` |
|---|---|---|
| Open CAN bus | `WaveshareCANBus` / `can.Bus` | same `can.Bus` underneath |
| Raw frame representation | `can.Message` (direct) | `can.Message` (direct) |
| Bit extraction (PGN/SA/priority) | inlined in `store.py` | internal |
| Fast-packet reassembly | `FastPacketReassembler` → raw bytes | `NMEA2000Decoder` internal → decoded fields |
| Assembled raw payload | `N2KMessage.payload` (bytes) | not exposed |
| Persistence / session log | `SessionLog` (.canlog) | nothing |
| UI data model | `DataStore` | nothing |
| Field decoding (heading, SOG…) | **nothing** | **full PGN library** |
