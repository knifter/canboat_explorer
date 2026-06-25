# NMEA 2000 Message Classification

Reference for how Canboat Explorer decides whether a raw CAN frame is an
NMEA 2000 message, how the PGN is derived, and how a message is classified as
single-frame, fast-packet, or proprietary. The rules below are implemented in
[`core/n2k_frame_info.py`](../canboat_explorer/core/n2k_frame_info.py)
(conformance + fast/single decision) and
[`core/fast_packet.py`](../canboat_explorer/core/fast_packet.py) (reassembly).

NMEA 2000 is a profile of **J1939**, which is itself a profile of **classic
CAN**. Each layer adds constraints; an N2K message is a CAN frame that satisfies
all of them.

---

## 1. Is this CAN frame an NMEA 2000 message?

A raw CAN frame is treated as N2K **only if every one of these holds**. Any
failure means the frame is non-N2K ("out-of-band") — it is shown in Tab 1 as a
raw frame but is never assembled into an `N2KMessage` and never appears in
Tab 2.

| Requirement | Why | If violated |
|---|---|---|
| **Extended (29-bit) ID** | N2K/J1939 addressing needs the 29-bit ID; standard 11-bit frames carry no PGN/SA. | Not N2K |
| **EDP = 0** (bit 25) | EDP+DP select the "page". N2K lives only on the page with EDP=0. EDP=1 is J1939 extended data page / ISO 15765 (diagnostics). | Not N2K |
| **Not a remote frame** (RTR = 0) | N2K is data-only; it never uses remote-request frames. | Not N2K |
| **Not CAN FD** | N2K is *classic* CAN (≤ 8 data bytes, single bitrate). A CAN FD frame is a different framing. | Not N2K |

> **EDP is structure, not interpretation.** EDP is a bit of the arbitration ID
> present on *every* extended frame, so it is meaningful even on a non-N2K
> frame — it is precisely the discriminator. That is why Tab 1 still shows the
> EDP column for an excluded frame (you see `1`), while the N2K *interpretation*
> columns (PGN/DP/PF/PS) are blank.

---

## 2. The 29-bit arbitration ID

Once a frame passes the gate, the ID decomposes into J1939 fields:

```
 28      26 25  24  23            16 15             8 7              0
┌─────────┬───┬───┬─────────────────┬────────────────┬───────────────┐
│ Priority│EDP│ DP│   PF (PDU Fmt)  │  PS (PDU Spec) │ SA (Source)   │
│  3 bits │ 1 │ 1 │     8 bits      │     8 bits      │    8 bits     │
└─────────┴───┴───┴─────────────────┴────────────────┴───────────────┘
```

| Field | Bits | Meaning |
|---|---|---|
| **Priority** | 28–26 | Arbitration priority, 0 = highest … 7 = lowest. Not part of the PGN. |
| **EDP** | 25 | Extended Data Page. Always 0 for N2K (see gate above). |
| **DP** | 24 | Data Page — the high bit of the PGN. |
| **PF** | 23–16 | PDU Format — selects PDU1 vs PDU2 and is the middle byte of the PGN. |
| **PS** | 15–8 | PDU Specific — *destination address* (PDU1) **or** *group extension* (PDU2). |
| **SA** | 7–0 | Source Address — the transmitting device's address (0–253). |

---

## 3. PGN derivation: PDU1 vs PDU2

The **PF** value decides how the PGN is formed and whether the message is
addressed or broadcast:

- **PF < 240 → PDU1 (addressed).** PS is the **destination address**. The PGN
  does *not* include PS:
  `PGN = (DP << 16) | (PF << 8)`
- **PF ≥ 240 → PDU2 (broadcast).** PS is a **group extension** folded into the
  PGN, and there is no destination:
  `PGN = (DP << 16) | (PF << 8) | PS`

So a PDU1 PF (e.g. 0xEF) yields a single PGN regardless of PS, while a PDU2 PF
(e.g. 0xFF) spans a 256-PGN range (one per PS value).

---

## 4. Message types: Single, Fast, ISO

Whether a message fits in one frame or spans several is a **property of the
PGN**, declared in the canboat catalogue's `Type` field — *not* something you
can read off a single frame's ID. The three types:

| Type | Meaning |
|---|---|
| **Single** | Fits in one classic-CAN frame (≤ 8 data bytes). |
| **Fast** | **Fast-packet**: a lightweight N2K scheme for up to 223 bytes across multiple frames. |
| **ISO** | ISO 11783-3 **transport protocol** (TP.CM / TP.DT, PGNs 60416/60160) — a separate multi-packet mechanism used for large/addressed transfers. |

Canboat Explorer reassembles **fast-packet**; ISO-TP is its own protocol layer.

---

## 5. Why fast-packet exists, and how it works

A classic-CAN frame carries at most **8 data bytes**. Many N2K messages are
larger (e.g. product information, GNSS position), so N2K defines **fast-packet**
to split a logical message across several frames and reassemble it cheaply —
without the full handshake of ISO-TP.

The first data byte of every fast-packet frame is a **control byte**:

```
 7   5 4       0
┌─────┬─────────┐
│ seq │  frame  │     seq   = 3-bit sequence counter (groups frames of one message)
│ 3b  │   5b    │     frame = 5-bit frame index within that sequence
└─────┴─────────┘
```

- **First frame** (`frame index == 0`): byte 0 = control, **byte 1 = total
  payload length** (7–223), bytes 2–7 = first **6** payload bytes.
- **Continuation frames** (`frame index 1…31`): byte 0 = control, bytes 1–7 =
  next **7** payload bytes.

Maximum payload = 6 + 31 × 7 = **223 bytes**, which is why the length field tops
out at 223. Frames are reassembled by the key **(SA, PGN, seq)**; the sequence
counter lets two interleaved instances of the same PGN from the same source be
told apart. See `FastPacketReassembler` in
[`core/fast_packet.py`](../canboat_explorer/core/fast_packet.py).

Detection of a fast-packet first frame therefore requires: the PGN is
fast-capable, `byte0 & 0x1F == 0`, and `7 ≤ byte1 ≤ 223`.

---

## 6. The proprietary special case (why PF alone is not enough)

Manufacturers may define their own PGNs in four **proprietary ranges**. Whether
those are single-frame or fast-packet is fixed by the **Data Page (DP)** bit —
**not** by PF:

| Range | PGN(s) | PF | DP | Type |
|---|---|---|---|---|
| Proprietary **A** | 61184 (`0xEF00`) | 239 (`0xEF`) | 0 | **Single** |
| Proprietary **B** | 65280–65535 (`0xFF00–0xFFFF`) | 255 (`0xFF`) | 0 | **Single** |
| Proprietary **A2** | 126720 (`0x1EF00`) | 239 (`0xEF`) | 1 | **Fast** |
| Proprietary **B2** | 130816–131071 (`0x1FF00–0x1FFFF`) | 255 (`0xFF`) | 1 | **Fast** |

So within the proprietary PF values (0xEF = "A", 0xFF = "B"):
**DP = 0 → single-frame, DP = 1 → fast-packet.** PF only tells you A vs B.

### Why this needs special handling

The catalogue stores each proprietary range under its **base PGN only**
(65280, 130816, …). A *specific* proprietary PGN such as **65384** is not an
exact key, so the lookup misses and the PGN is "unknown."

If unknown PGNs defaulted to fast-packet, a single-frame Proprietary-B message
(like 65384) would have its first data byte misread as a fast-packet control
byte — producing bogus Seq/Frame values and, in the worst case, triggering
false reassembly. Conversely, defaulting unknown to single would drop genuine
Proprietary-B2 fast-packets.

The fix is to classify unknown PGNs **structurally**:

```python
# Known PGN: trust the catalogue's Type.
# Unknown PGN ⇒ proprietary; fast iff DP == 1 within the proprietary PF range:
is_fast = (dp == 1) and (pf in (0xEF, 0xFF))
```

This is correct because the only PGNs absent from the catalogue are
proprietary, and the proprietary ranges are exactly `PF ∈ {0xEF, 0xFF}` with the
single/fast split determined by DP.

---

## 7. Summary flow

```
CAN frame
  │  extended? ──no──► non-N2K (Tab 1 only)
  │  EDP=0? RTR=0? not FD? ──no──► non-N2K (Tab 1 only)
  ▼
Decode 29-bit ID → Priority, DP, PF, PS, SA
  │  PF<240 → PDU1 (PS = destination)   PGN = DP<<16 | PF<<8
  │  PF≥240 → PDU2 (PS = group ext)     PGN = DP<<16 | PF<<8 | PS
  ▼
Fast or single?
  │  PGN in catalogue → use its Type
  │  PGN unknown (proprietary) → fast iff DP=1 and PF in {0xEF,0xFF}
  ▼
Single → emit as one N2KMessage
Fast   → reassemble by (SA, PGN, seq) via fast-packet control bytes
```
