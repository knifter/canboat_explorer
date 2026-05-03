# NemaFiddler — Design Document

## Purpose

A desktop application for exploring, debugging, and recording CAN / NMEA 2000 bus traffic. Primary use cases:
- Verify what a bus is and isn't transmitting
- Discover and reverse-engineer proprietary/obscure manufacturer messages
- Record traffic persistently across sessions, including through crashes

---

## Decisions

- **GUI framework: PyQt6** — best fit for large, responsive table UIs via virtual models (`QAbstractTableModel`). Only renders visible rows regardless of dataset size. Well-maintained, clean packaging on Windows, scales well as features are added.
- **Log format: fixed-size binary records** — each incoming frame is appended as a fixed 21-byte record (8-byte timestamp + 4-byte arbitration ID + 1-byte DLC + 8-byte data). Fast sequential writes, crash-safe, exact reproduction. The whole file is read into memory once at startup; the disk is not used as a search space. CSV export is a separate user-triggered operation.
- **In-memory store is primary** — all querying, filtering, and display runs against the in-memory data model. The log file is write-only during live operation.
- **Settings file: `.ini` at project root** — `configparser`-backed singleton (`nemafiddler.ini`). Stores `data_dir`, `last_interface`, `last_port`. Defaults written on first run. Data directory defaults to `<project root>/data/` (not AppData), so session files stay next to the application.
- **Package layout: `bus/` · `core/` · `ui/`** — `bus` owns adapter I/O; `core` owns `n2k`, `fast_packet`, `session_log`, `store`, `settings`, `paths`; `ui` owns all PyQt6 code.
- **`can.Message` as the raw frame type** — python-can's `can.Message` is stored directly throughout the stack. `can.Message.is_error_frame` surfaces real CAN bus errors. DLC is trusted as declared length. `recv()` exceptions signal a connection state change to the UI rather than entering the data stream.
- **nmea2000 (PyPI) for Tab 4** — `NMEA2000Decoder.decode(can.Message)` returns decoded field values (`NMEA2000Message`) or `None` for unknown/proprietary PGNs. It handles fast-packet reassembly internally. Tabs 1–3 use our own stack, which exposes raw payload bytes, frame counts, and persistence that nmea2000 does not provide. See `ARCHITECTURE.md` for the full stack diagram.
- **Capture modes**:
  - *Live*: connected to adapter, appending to the active session log. A **Pause / Continue** button suspends frame capture without disconnecting.
  - *Read-only / Open*: a saved file is opened for offline browsing; live capture redirects to that file (subsequent frames append there).

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
- **Tab 4 – Decoded values** — live parsed signal values (heading, wind, position, SOG/COG, rudder, roll/pitch, depth, etc.) via `nmea2000` library. Proprietary/unknown PGNs are silently skipped.
- **Visual priority system** — user can flag any PGN or arbitration ID:
  - *Ignore* (grey): understood; softly fades across all tabs so unknown messages stand out
  - *Highlight* (accentuated): draw attention to a specific message
  - *Unknown* (orange, auto-applied): PGN not recognised by the nmea2000 decoder — covers proprietary messages and non-NMEA CAN frames
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
- **Unknown-PGN flag tooltip** — hover over an auto-flagged orange row to see why it was flagged (e.g. "PGN not recognised by nmea2000 decoder")
- **CSV export** — user-triggered export of current session or a saved file to CSV, or better yet: use nmea2000 write file format, whatever that supports so other applications can also read this data.

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
7. ✅ **UI shell** — main window, 4-tab layout, file toolbar + menu bar, connection widget, pause/continue, status bar; settings in `nemafiddler.ini`
8. ✅ **Tab 1 – Raw CAN** — virtual `QAbstractTableModel`, time/accumulated toggle, three grouping modes, average interval column
9. ✅ **Tab 2 – NMEA 2000** — virtual table model, time/accumulated toggle
10. **Tab 3 – Network** — `QTreeView` per SA; PGN advertised/observed cross-check; collision and discrepancy markers
11. **Tab 4 – Decoded values** — `NMEA2000Decoder` (nmea2000 PyPI) fed directly with `can.Message`; live signal value display at UI poll rate
12. ✅ **Flag / highlight controls** — sidecar JSON persistence done; right-click context menu pending
13. **Unknown-PGN auto-flag** — any PGN for which `NMEA2000Decoder` returns `None` (proprietary messages, non-NMEA CAN frames with `is_extended_id=False`) is automatically flagged orange on first occurrence, per-PGN, across Tab 1 and Tab 2. User can override via the normal priority system.
14. **Filter bar, CSV export** *(want-to-haves)*
15. **Replay, annotations, pattern guesser** *(ideas — revisit post-core)*

---

## Open Questions

- **"Clear" semantics confirmed**: active log is truncated to zero bytes (not archived); in-memory state is reset; user confirmation dialog required. Archiving was considered but dropped — Save As covers that need.

