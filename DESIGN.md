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
- **`can.Message` as the raw frame type** — python-can's `can.Message` is stored directly throughout the stack. No wrapper class. `can.Message.is_error_frame` surfaces real CAN bus errors (collision, CRC fail, etc.) and replaces any synthetic error injection. DLC is trusted as declared length. `recv()` exceptions signal a connection state change to the UI rather than entering the data stream.
- **No `N2KFrame`** — PGN, SA, priority, and destination are extracted from `arbitration_id` with 3–4 lines of bit math inlined in `store.py`. A wrapper class would add indirection without value.
- **PGN name lookup from bundled `canboat.json`** — `canboat_explorer/data/canboat.json` is the upstream PGN database that the nmea2000 library is generated from (Apache 2.0, © 2009-2025 Kees Verruijt). Parsed once at startup into `dict[int, str]` via `{e["PGN"]: e["Description"] for e in data["PGNs"]}`. Replaces the hand-maintained `PGN_NAMES` dict in `core/n2k.py`. The copyright notice must appear in the About dialog. Source: https://github.com/canboat/canboat/blob/master/docs/canboat.json
- **SA-keyed PGN name cache** — `PGN_SA_NAMES: dict[tuple[int,int], str]` in `core/n2k.py`, populated reactively by the store after each successful decode: `PGN_SA_NAMES[(decoded.PGN, decoded.source)] = decoded.description`. `pgn_sa_name(pgn, sa)` falls back to `pgn_name(pgn)` if no SA-specific entry exists. Tabs 1/2 use this in preference to `pgn_name()` wherever SA is known. This surfaces manufacturer-specific variant names (e.g. "Simnet: Temperature" vs "Yamaha: Temperature") and is the source for detecting SA collision errors: if `{desc for (p,_), desc in PGN_SA_NAMES.items() if p == pgn}` has more than one distinct value, the PGN is auto-flagged Error.
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
              input: can.Message directly       3-level tree: PGN → SA → qualifier
              handles FP internally             stacked view for PGN/SA nodes
              returns None for unknown PGNs     single-message view for qualifier leaves
              build_network_map=False           history entries expandable under leaf
              → NMEA2000Message(pgn, source, fields...)
                stored in decoded_by_key[(pgn, sa, qualifier_tuple)]
                qualifier_tuple = sorted primary-key field (id, raw_value) pairs
                maxlen=200 deque per key, newest-first
```

### What `nmea2000` (PyPI) Provides vs Our Stack

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
  - `decoded_by_key: dict[tuple[int, int, tuple], deque]` — keyed by `(pgn, sa, qualifier_tuple)`; bounded deque (`maxlen=200`), newest-first; `qualifier_tuple` is empty `()` when PGN has no primary-key fields.
  - `_known_pgns: set[int]` — PGNs that have ever decoded successfully; gates the warning/error auto-flags so a trailing incomplete fast-packet sequence at end of a session file does not re-flag PGNs that decoded correctly earlier.
- **Visual priority system** — four flag levels, two user-set and two auto-applied. Colors are muted/desaturated, not primary:
  - *Ignore* (muted grey, user-set): understood; softly fades the row across all tabs
  - *Highlight* (muted accent, user-set): draw attention to a specific PGN or arb ID
  - *Warning* (muted amber, auto-applied): something notable but not necessarily broken:
    - PGN not recognised by the nmea2000 decoder (proprietary / non-NMEA CAN)
    - Same PGN actively transmitted by two or more SAs (could be legitimate — e.g. apparent wind from one device, true wind from another)
  - *Error* (muted red, auto-applied): indicates a likely bus or device fault:
    - Same PGN decoded as two different manufacturer variants across different SAs — symptom of an SA address collision (two physical devices sharing one SA)
    - Incomplete fast-packet sequence: frames received but packet never assembled (timed out)
  - Tooltips on auto-flagged rows state the specific reason (e.g. "SA 22 and SA 47 both send PGN 130312 as different manufacturer variants")
  - Flags shown in both Tab 1/2 row colouring and Tab 3 collision markers
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
14. ✅ **Auto-flag: unknown PGN (warning)** — PGNs for which `NMEA2000Decoder` returns `None` auto-flagged amber warning; `_known_pgns` guards against re-flagging on session reload.
15. **Revised flag colour system** — four levels (grey / amber / red / highlight); muted palette; reclassify incomplete fast-packet as red error; add warning for multi-SA same-PGN; tooltips on all auto-flags.
16. **SA-keyed PGN name cache + SA collision detection** — `PGN_SA_NAMES` populated reactively from decoded messages; `pgn_sa_name(pgn, sa)` in Tabs 1/2; auto-error when same PGN decodes as different manufacturer variants across SAs.
17. **Filter bar, CSV export** *(want-to-haves)*
18. **Replay, annotations, pattern guesser** *(ideas — revisit post-core)*

---

## Open Questions

- **"Clear" semantics confirmed**: active log is truncated to zero bytes (not archived); in-memory state is reset; user confirmation dialog required. Archiving was considered but dropped — Save As covers that need.

