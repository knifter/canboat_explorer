# Canboat Explorer — Design Document

## Purpose

A desktop application for exploring, debugging, and recording CAN / NMEA 2000 bus traffic. Primary use cases:
- Verify what a bus is and isn't transmitting
- Discover and reverse-engineer proprietary/obscure manufacturer messages
- Record traffic persistently across sessions, including through crashes

---

## Decisions

- **GUI framework: PyQt6** — best fit for large, responsive table UIs via virtual models (`QAbstractTableModel`). Only renders visible rows regardless of dataset size. Well-maintained, clean packaging on Windows, scales well as features are added.
- **Log format: fixed-size binary records** — each incoming frame is appended as a fixed 22-byte record (8-byte timestamp + 4-byte arbitration ID + 1-byte DLC + 8-byte data + 1-byte flags). Flags byte encodes `is_error_frame` (bit 0), `is_remote_frame` (bit 1), `is_fd` (bit 2). Fast sequential writes, crash-safe, exact reproduction. The whole file is read into memory once at startup; the disk is not used as a search space. CSV export is a separate user-triggered operation.
- **In-memory store is primary** — all querying, filtering, and display runs against the in-memory data model. The log file is write-only during live operation.
- **Settings file: `.ini` at project root** — `configparser`-backed singleton (`canboat_explorer.ini`). Stores `data_dir`, `last_interface`, `last_port`. Defaults written on first run. Data directory defaults to `<project root>/data/` (not AppData), so session files stay next to the application.
- **Package layout: `bus/` · `core/` · `ui/`** — `bus` owns adapter I/O; `core` owns `n2k`, `fast_packet`, `session_log`, `store`, `settings`, `paths`; `ui` owns all PyQt6 code.
- **Raw frame type: `RawFrame(can.Message)`** — python-can's `can.Message` uses `__slots__` and forbids dynamic attributes, so per-frame state (specifically: a back-link to the assembled `N2KMessage` the frame contributed to) cannot be attached directly. We subclass with one extra slot: `class RawFrame(can.Message): __slots__ = ('n2k_msg',)`. `RawFrame` is constructed at every ingress (`SessionLog.decode()` and at `store._add()` entry from the queue). Since `RawFrame` IS-A `can.Message`, all consumers — including the nmea2000 library — see it as a normal CAN frame. `is_error_frame` surfaces real CAN bus errors (collision, CRC fail, etc.); DLC is trusted as declared length; `recv()` exceptions signal a connection state change to the UI rather than entering the data stream.
- **No `N2KFrame` wrapper; central arb-ID parser** — PGN, SA, priority, destination (PDU1), group extension (PDU2), `is_fast`, and the FP `seq`/`frame_idx` are extracted by `n2k_frame_info(msg)` in `core/n2k_frame_info.py`, which returns a `NamedTuple` (`N2KFrameInfo`) joining bit-level arb-ID parsing with catalogue data (description, `is_fast`). All consumers — `store._add()`, `FastPacketReassembler.feed()`, `tab_raw_can` — use this single function instead of cherry-picking arb-ID bits inline.
- **PGN catalogue: `core/pgn_catalogue.py`** — bundled `canboat_explorer/canboat.json` is the upstream PGN database that the nmea2000 library is generated from (Apache 2.0, © 2009-2025 Kees Verruijt). Parsed once at startup into `PGN_LOOKUP: dict[int, dict]` using first-occurrence-wins (canboat lists variants per PGN; only one entry per PGN is kept). `pgn_name(pgn)` returns the generic description, `""` if unknown. The copyright notice must appear in the About dialog. Source: https://github.com/canboat/canboat/blob/master/docs/canboat.json
- **SA-keyed PGN name cache: `DataStore.pgn_sa_names`** — `dict[tuple[int,int], str]` populated reactively from `decoded.description` on every successful decode. `store.pgn_sa_name(pgn, sa)` returns the variant-resolved name if known, else falls back to `pgn_catalogue.pgn_name(pgn)`. Tabs 1/2 use this in preference to the catalogue alone wherever SA is known — surfaces manufacturer-specific variant names like "Simnet: Temperature" vs "Yamaha: Temperature". (SA-address-collision detection — same PGN producing different variant descriptions across SAs — has moved to Tab 3, see "Visual priority system" below.)
- **Decode success/failure is a per-message property** — `decoded: bool` on `N2KMessage`, set from the decoder's return on emit (`True` if the library returned a `NMEA2000Message`, `False` if `None`). Replaces the previous global `_unknown_pgns`/`_known_pgns` PGN-level tracking. Row-level flagging in Tab 2 reads this directly. Note: the library returns `None` for any of "could not decode", "filtered out", or "PGN unsupported" — for row-flagging purposes they're equivalent ("the bytes did not turn into structured data on this row").
- **Fast-packet completion is a per-message property** — `N2KMessage.is_complete: bool` (defaults `True`; set `False` when our `FastPacketReassembler` emits a stale-timed-out buffer). Replaces the previous global `_incomplete_fp_pgns`/`_complete_fp_pgns` sets.
- **Bidirectional frame ↔ message links** — `FastPacketReassembler._Buffer` keeps `contributing_frames: list[RawFrame]`. On emit (complete or stale-flush), each contributing frame's `n2k_msg` is set to the new `N2KMessage`, and the new message's `frames: list[RawFrame]` holds the same list. Single-frame N2K PGNs link 1:1 trivially. Out-of-band frames (standard 11-bit ID, error frames, FP continuations that arrived after a buffer timed out) keep `n2k_msg=None` and are flagged accordingly in the Raw CAN view.
- **Decoder result aligned with FP emit timing** — `NMEA2000Decoder.decode(msg)` handles fast-packet internally and returns `None` for intermediate FP frames, the assembled `NMEA2000Message` on the completing frame. Our `FastPacketReassembler.feed(msg)` emits in the same pattern. We line up the two by call timing: when `_fp.feed(msg)` emits a non-`is_complete=False` `N2KMessage`, the decoder's return on the same `msg` is the decoded form of that same message. No payload reconstruction, no second decode pass.
- **Library FP buffer not used for visibility** — `NMEA2000Decoder` has its own FP reassembler with private state (`self.data`, `FastPgnMetadata`). It provides no stale detection (buffers wait forever for missing frames), no incomplete-sequence notification (silently overwritten when a new sequence with same `pgn_src_dest` key arrives), no intermediate visibility (returns `None` while accumulating). We keep our own `FastPacketReassembler` precisely for these signals — they are the explorer-tool value.
- **Per-row, per-message flag detection** — auto-flags are computed from properties of a single frame (`frame.n2k_msg is None`) or a single message (`n2k_msg.decoded`, `n2k_msg.is_complete`), not from PGN-level aggregate state. See "Visual priority system" for full reclassification.
- **Tab-header warning marker** — each tab exposes a `has_warnings() -> bool` method; the main window prepends `⚠` to the tab text when any tab reports warnings. The check is O(1) for Tabs 1/2 (backed by three store counters: `oob_frame_count`, `incomplete_msg_count`, `undecoded_msg_count`, incremented in `_add()` and cleared in `reset_memory()`) and a small dict scan for Tab 3. Tab 4 always returns `False`. Updated after every drain and after open/clear.
- **Tab 3 multi-SA detection covers both observed and TX-advertised** — `_collision_pgns()` returns PGNs where the union of (SAs actually observed transmitting) ∪ (SAs advertising the PGN as TX via PGN 126464) is ≥2. Catches the "potential collision" case where two devices both claim they can send a PGN but neither has yet. The tooltip distinguishes three flavours: variant-collision (different decoded variant descriptions — likely SA address collision), actual multi-SA (≥2 SAs observed transmitting), and potential multi-SA (advertised by ≥2 SAs, observed from <2).
- **nmea2000 (PyPI) for Tab 4** — `NMEA2000Decoder.decode(can.Message)` returns decoded field values (`NMEA2000Message`) or `None` for unknown/proprietary PGNs. It handles fast-packet reassembly internally. Tabs 1–3 use our own stack, which exposes raw payload bytes, frame counts, and persistence that nmea2000 does not provide.
- **nmea2000's internal fast-packet reassembler not used for Tab 2/3** — it only surfaces the decoded result, never the raw assembled payload or frame count. It is tightly coupled to its own decoder and cannot be driven or queried independently. Tabs 2/3 need `payload:bytes`, `frame_count`, and per-frame timing, none of which nmea2000 exposes.
- **`build_network_map=False` on the decoder** — `NMEA2000Decoder` is initialised without `build_network_map=True`. That flag would cause `decode()` to return `None` for any source that has not yet sent an ISO Address Claim (PGN 60928), silently dropping messages for up to 10 minutes from decoder startup — which breaks `bulk_load`. Tab 3 will harvest device identity directly from `decoded_by_key` entries for PGNs 60928, 126464, and 126996 instead.
- **Qualifier key computed locally** — `NMEA2000Message.hash` is only set when `build_network_map=True`, so it is always `None` in our decoder. The qualifier key for `decoded_by_key` is computed as `tuple(sorted((f.id, f.raw_value) for f in decoded.fields if f.part_of_primary_key))` — deterministic, hashable, and requires no library flag.
- **Capture modes**:
  - *Live*: connected to adapter, appending to the active session log. A **Pause / Continue** button suspends frame capture without disconnecting.
  - *Read-only / Open*: a saved file is opened for offline browsing; live capture redirects to that file (subsequent frames append there).

## Architecture

### Stack

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
      │  pushes can.Message to frame_queue (thread-safe)
      │  recv() exceptions → connection-state signal to UI, not a fake frame
      ▼
  frame_queue → DataStore  (drained every 50 ms on main thread)
      │  wraps incoming can.Message → RawFrame (adds n2k_msg slot, default None)
      │
      ├── SessionLog                       → disk (.canlog) [encodes inherited slots only]
      │                                      decode() reconstructs as RawFrame
      │
      ├── DataStore.frames                 → Tab 1 – Raw CAN
      │       list[RawFrame]                   time view  (all frames, newest first)
      │       row flag: n2k_msg is None         accum view (by arb_id / PGN+SA / PGN)
      │       and not is_error_frame
      │
      ├── FastPacketReassembler            → Tab 2 – NMEA 2000
      │       input:  RawFrame                  time view  (assembled messages, newest first)
      │       heuristic FP detection            accum view (by PGN+SA, count + interval)
      │       10 ms inter-frame stale timeout   row flags: not n2k_msg.is_complete (error)
      │       _Buffer.contributing_frames                  not n2k_msg.decoded     (warning)
      │       → N2KMessage(timestamp, pgn, sa, priority, payload:bytes,
      │                    frame_count, is_complete, decoded, frames)
      │
      ├── (N2KMessage stream, same)        → Tab 3 – Network
      │       filters N2KMessage for:          device tree per SA
      │         PGN 60928  address claim        PGN advertised/observed cross-check
      │         PGN 126464 PGN list             multi-SA + variant-collision markers
      │         PGN 126996 product info         (sole owner of collision detection)
      │
      └── NMEA2000Decoder.decode(msg)      → Tab 4 – Decoded Values
              input: RawFrame directly          3-level tree: PGN → SA → qualifier
              handles FP internally             stacked view for PGN/SA nodes
              returns None for unsupported/     single-message view for qualifier leaves
                intermediate-FP/filtered        history entries expandable under leaf
              build_network_map=False
              → NMEA2000Message(pgn, source, fields...)
                stored in decoded_by_key[(pgn, sa, qualifier_tuple)]
                qualifier_tuple = sorted primary-key field (id, raw_value) pairs
                maxlen=200 deque per key, newest-first
              also sets n2k_msg.decoded = (result is not None) on the matching emit
```

### What `nmea2000` (PyPI) Provides vs Our Stack

| Concern | Our stack | `nmea2000` |
|---|---|---|
| Open CAN bus | `WaveshareCANBus` / `can.Bus` | same `can.Bus` underneath |
| Raw frame representation | `can.Message` (direct) | `can.Message` (direct) |
| Bit extraction (PGN/SA/priority/FP header) | `n2k_frame_info()` in `core/n2k_frame_info.py` | internal |
| Fast-packet reassembly | `FastPacketReassembler` → raw bytes | `NMEA2000Decoder` internal → decoded fields |
| Assembled raw payload | `N2KMessage.payload` (bytes) | not exposed |
| Persistence / session log | `SessionLog` (.canlog) | nothing |
| UI data model | `DataStore` | nothing |
| Field decoding (heading, SOG…) | **nothing** | **full PGN library** |

---

## Requirements

- **Adapter connection** — connect to one USB CAN adapter at a time. Supported types (via python-can): `waveshare`, `slcan`, `gs_usb`, `pcan`, `socketcan`. NMEA 2000 bitrate is always 250 kbps.
- **Tab 1 – Raw CAN** — all incoming frames. Two view modes:
  - *Time view*: newest frame on top, virtual scrolling. Columns split by a visual separator: CAN fields (Time, Arb ID, Type [classic/extended], SRR, IDE, RTR, DLC, Data) | NMEA fields (PGN, SA, Priority). Header tooltips describe each column and note adapter-specific caveats (e.g. SRR always 1).
  - *Accumulated view*: one row per group, last value + frame count + average interval (ms). Three grouping modes selectable via combo box: by Arb ID / by PGN + SA / by PGN. Column widths only grow, never shrink during a session.
- **Tab 2 – NMEA 2000 messages** — frames decoded as N2K. Two view modes:
  - *Time view*: newest message on top
  - *Accumulated view*: one row per PGN + source address, last value + count
- **Tab 3 – Network** — tree view of devices discovered on the bus:
  - Top-level node per source address (SA) showing device name (from Address Claim PGN 60928)
  - Child nodes: PGNs the device advertises as transmitting and receiving (PGN List, PGN 126464), with observed-in-traffic status per PGN
  - Collision markers: PGNs where more than one device is actively transmitting
  - Discrepancy markers: PGNs advertised as TX but never observed, or observed but not advertised (likely proprietary)
  - Product info (model, manufacturer, firmware) where available (PGN 126996 — requires fast-packet)
  - Address negotiation events shown as a log below the tree
- **Tab 4 – Decoded values** — live parsed signal values (heading, wind, position, SOG/COG, rudder, roll/pitch, depth, etc.) via the `nmea2000` library. Proprietary/unknown PGNs are silently skipped.

  **Layout: two-panel split**

  *Left panel — signal tree (3-level for qualifier PGNs, 2-level otherwise):*
  - **Level 1** — PGN group header (bold; label = `NMEA2000Message.description`). Selectable: right panel shows stacked sections for all SA variants.
  - **Level 2** — SA node (`SA:xx`). For PGNs with qualifiers this is an intermediate node (selectable → stacked sections for all qualifier variants of that SA). For PGNs without qualifiers the SA node is the leaf.
  - **Level 3** — Qualifier leaf (label = values of all `part_of_primary_key=True` fields joined by " / ", e.g. `"Apparent"`, `"True (Boat Referenced)"`). Only present when the PGN has primary-key fields. Shows update rate. Selectable: right panel shows that variant's latest fields.
  - Expanding a qualifier leaf (or a no-qualifier SA leaf) reveals indented history entries (timestamp); clicking a history entry shows that snapshot in the right panel.

  *Right panel — field table:*
  - **Header label** — full breadcrumb path: `"Wind Data  (PGN 130306)  —  SA:100  —  Apparent"` for qualifier leaves; timestamp appended for history entries. No colour tinting.
  - **Single-message view** (qualifier leaf or history entry): one row per field; columns **Field** | **Value** | **Unit**. `RESERVED`/`SPARE` fields hidden. Primary-key fields shown in italic.
  - **Stacked view** (SA node or PGN group): one grey section-header row per qualifier variant (showing qualifier label), followed by that variant's field rows. Section headers suppressed for non-qualifier PGNs (no "SA:xx" noise).
  - `field.description` shown as tooltip on the Field cell.
  - `field.value` after `apply_preferred_units({})`.

  *Store:*
  - `decoded_by_key: dict[tuple[int, int, tuple], deque]` — keyed by `(pgn, sa, qualifier_tuple)`; bounded deque (`maxlen=200`), newest-first; `qualifier_tuple` is empty `()` when PGN has no primary-key fields. (Reload safety is handled per-message via `N2KMessage.decoded`; no PGN-level "ever seen good" state required.)
- **Visual priority system** — four flag levels, two user-set and two auto-applied. Colors are muted/desaturated, not primary. **Auto-flags are now per-row** (derived from frame or message state on that row), never PGN-aggregate:
  - *Ignore* (muted grey, user-set): understood; softly fades the row across all tabs
  - *Highlight* (muted accent, user-set): draw attention to a specific PGN or arb ID
  - *Warning* (muted amber, auto-applied):
    - **Tab 1 – Raw CAN**: frame is out-of-band — `frame.n2k_msg is None` and `not frame.is_error_frame`. Covers standard 11-bit IDs, FP continuations that arrived after their buffer timed out, and anything else that did not participate in an assembled N2K message.
    - **Tab 2 – NMEA 2000**: `not n2k_msg.decoded` — the assembled message exists but the nmea2000 library returned `None` for it (could not parse the payload, PGN excluded by filter, or PGN unsupported).
  - *Error* (muted red, auto-applied):
    - **Tab 2 – NMEA 2000**: `not n2k_msg.is_complete` — fast-packet sequence timed out before all frames arrived.
  - **Network-level signals (Tab 3 only)** — multi-SA-same-PGN and SA-address-collision (same PGN producing different variant descriptions across SAs) are detected and shown only in the Network tab. They no longer apply row flags in Tabs 1/2. Tab 3 maintains its own detection by scanning `store.by_pgn_sa` and `store.pgn_sa_names`.
  - Tooltips on auto-flagged rows state the specific reason.
- **Crash-safe persistence** — frames written to log file on receipt, before UI processing
- **Session continuity** — last session log always reopened on startup; flags and highlights persist
- **File management**
  - *Save As*: exports current frames to a named `.canlog` file; live capture redirects to that file going forward. `session.canlog` is deleted so the next app start is clean.
  - *Open*: loads a `.canlog` file, disconnects any live reader, redirects the active log to that file (subsequent live frames append there).
  - *Clear*: confirms with the user, truncates the active log to zero bytes, resets all in-memory state.
  - On startup, `session.canlog` is reopened if it exists (crash continuity).
- **Pause / Continue** — suspend and resume live frame capture without disconnecting the adapter

---

## Want-to-haves

- **Filter / search bar** — filter rows in any tab by PGN, arbitration ID, SA, or text
- **Flag tooltip detail** — hover over any auto-flagged row to see the full reason string (moved to requirements as part of the revised flag system)
- **CSV export** — user-triggered export of current session or a saved file to CSV, or better yet: use nmea2000 write file format, whatever that supports so other applications can also read this data.
- **Data Sources** — Support other data sources/usb-adapters. TCP/IP? SignalK? 
- **Message Sender** — A tab to send messages, clone from received or create new ones and periodically send these. 

---

## Ideas (needs exploring)

Written down to lightly influence architecture; not committed.

- **Replay** — play back a saved binary file as if it were live traffic, at real or scaled speed
- **Proprietary message annotations** — attach persistent notes to a mystery PGN/ID once partially understood
- **Pattern / structure guesser** — for unknown messages, highlight byte positions that vary vs. stay constant across frames; aids reverse engineering
- **Timeline / rate graph** — sparkline per PGN showing message rate over time
- **Network topology visual** — graphical bus diagram (nodes on a backbone line); revisit once typical network shape is known from real captures

---

## Implementation Plan

1. ✅ **Project scaffold** — Python package layout (`bus/` · `core/` · `ui/`), `pyproject.toml`, PyQt6 + python-can dependencies, adapter config
2. ✅ **CAN reader** — background thread, adapter-agnostic; pushes `can.Message` to thread-safe queue; supports pause/resume; `recv()` exceptions surface as connection-state signals
3. ✅ **Binary session log** — fixed-record format, append-on-receive; load-all-at-startup; Save As / Open / Clear file management; `session.canlog` as crash-safe scratch buffer
4. ✅ **NMEA 2000 parser** — PGN extraction, N2K validity check, ~80 known PGN names
5. ✅ **Fast-packet reassembler** — heuristic detection (no PGN whitelist), 10 ms inter-frame stale timeout, produces `N2KMessage(pgn, sa, priority, payload, frame_count)`
6. ✅ **In-memory data store** — `can.Message` list + `N2KMessage` list; three accumulation dicts; flag/highlight state persisted in sidecar JSON
7. ✅ **UI shell** — main window, 4-tab layout, file toolbar + menu bar, connection widget, pause/continue, status bar; settings in `canboat_explorer.ini`
8. ✅ **Tab 1 – Raw CAN** — virtual `QAbstractTableModel`, time/accumulated toggle, three grouping modes, average interval column
9. ✅ **Tab 2 – NMEA 2000** — virtual table model, time/accumulated toggle
10. ✅ **PGN name lookup from canboat.json** — `canboat_explorer/canboat.json` bundled as package data; loaded at startup via `importlib.resources`; `PGN_LOOKUP: dict[int, dict]` + `PGN_COUNT`; canboat + all third-party copyright in About dialog; GPL-3.0 project license.
11. ✅ **Tab 3 – Network** — `QTreeView` per SA; device identity from `decoded_by_key[(60928, sa, ())]`; PGN 126464 TX/RX cross-check; collision and discrepancy markers. Do NOT use `build_network_map=True` (breaks bulk_load).
12. ✅ **Tab 4 – Decoded values** — 3-level tree (PGN → SA → qualifier) for qualifier PGNs, 2-level for others; stacked right-panel view for PGN/SA nodes; `decoded_by_key` keyed by `(pgn, sa, qualifier_tuple)`; qualifier computed from primary-key field raw values (not `msg.hash`); breadcrumb header; no colour tinting.
13. ✅ **Flag / highlight controls** — sidecar JSON persistence done; right-click context menu pending
14. ✅ **Auto-flag: unknown PGN (warning) — original implementation** — PGN-level auto-flag with `_unknown_pgns`/`_known_pgns` tracking. Superseded by item 15.
15. ✅ **Per-row flag refactor** — replaced PGN-level auto-flag state (`_unknown_pgns`, `_known_pgns`, `_unknown_arbs`, `_incomplete_fp_pgns`, `_complete_fp_pgns`, `_multi_sa_pgns`, `_collision_pgns`) with per-frame and per-message signals. Subclassed `can.Message` as `RawFrame` with a `n2k_msg` back-link slot; extended `N2KMessage` with `decoded: bool`, `is_complete: bool`, and `frames: list[RawFrame]`; `FastPacketReassembler._Buffer` tracks contributing frames and emits them on the message; nmea2000 decoder result aligned with FP emit by call timing. Row flagging in Tabs 1/2 reads frame/message state directly; collision detection lives in Tab 3 as sole owner (covering both actual multi-SA and TX-advertised potential collisions). Tab headers gained `⚠` markers backed by three store counters. See `design/store-data-audit.md` for the final state.
16. ✅ **SA-keyed PGN name cache** — `DataStore.pgn_sa_names` populated reactively from `decoded.description`; `store.pgn_sa_name(pgn, sa)` falls back to `pgn_catalogue.pgn_name(pgn)`. (Cross-SA variant-collision detection moved to Tab 3 as part of item 15.)
17. **Filter bar, CSV export** *(want-to-haves)*
18. **Replay, annotations, pattern guesser** *(ideas — revisit post-core)*

---

## Open Questions

- **"Clear" semantics confirmed**: active log is truncated to zero bytes (not archived); in-memory state is reset; user confirmation dialog required. Archiving was considered but dropped — Save As covers that need.

