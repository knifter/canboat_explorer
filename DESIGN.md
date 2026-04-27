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
- **Capture modes**:
  - *Live*: connected to adapter, appending to the active session log. A **Pause / Continue** button suspends frame capture without disconnecting.
  - *Read-only*: a saved file is opened for offline browsing; no live connection, no writes.
  - *Append*: a saved file is opened and the user confirms they want to continue recording into it; live capture resumes appending to that file. App prompts on file-open whether to open read-only or append.

---

## Requirements

- **Adapter connection** — connect to one USB CAN adapter at a time. Supported types (via python-can): `waveshare`, `slcan`, `gs_usb`, `pcan`, `socketcan`. NMEA 2000 bitrate is always 250 kbps.
- **Tab 1 – Raw CAN** — all incoming frames. Two view modes:
  - *Time view*: newest frame on top, virtual scrolling
  - *Accumulated view*: one row per arbitration ID, last value + frame count
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
- **Tab 4 – Decoded values** — live parsed signal values: heading, wind, position, SOG/COG, rudder, roll/pitch, depth, etc.
- **Visual priority system** — user can flag any PGN or arbitration ID:
  - *Ignore* (grey): understood; softly fades across all tabs so unknown messages stand out
  - *Highlight* (accentuated): draw attention to a specific message
  - Non-N2K CAN frames are automatically accentuated
- **Crash-safe persistence** — frames written to log file on receipt, before UI processing
- **Session continuity** — last session log always reopened on startup; flags and highlights persist
- **Export and clear** — user saves current log to a named file and clears the live backlog. Saved files can be reopened read-only or in append mode.
- **Pause / Continue** — suspend and resume live frame capture without disconnecting the adapter

---

## Want-to-haves

- **Fast-packet / multi-frame reassembly** — required for full Tab 4 (PGN 126996 product info is multi-frame). Also improves Tab 2 for longer N2K messages.
- **Message rate column** — observed Hz per PGN / arbitration ID in accumulated views
- **Filter / search bar** — filter rows in any tab by PGN, arbitration ID, SA, or text
- **CSV export** — user-triggered export of current session or a saved file to CSV

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

1. **Project scaffold** — Python package layout, `pyproject.toml`, PyQt6 + python-can dependencies, adapter config
2. **CAN reader** — background thread, adapter-agnostic; pushes raw frames to thread-safe queue; supports pause/resume
3. **Binary session log** — fixed 21-byte records, append-on-receive; load-all-at-startup; open read-only or append modes; clear archives with timestamp name
4. **NMEA 2000 parser** — PGN extraction (reuse `pgn_from_id` from AutoPilotSim), N2K validity check, signal decoding for known PGNs
5. **In-memory data store** — fed from reader queue; maintains time-ordered list + accumulated-per-ID map; flag/highlight state per PGN/ID; thread-safe
6. **UI shell** — main window, 4-tab layout, adapter connection widget, pause/continue button, status bar
7. **Tab 1 – Raw CAN** — virtual `QAbstractTableModel`, time/accumulated toggle, visual priority rendering
8. **Tab 2 – NMEA 2000** — virtual table model, time/accumulated toggle, visual priority rendering
9. **Tab 3 – Network** — `QTreeView` per SA; PGN advertised/observed cross-check; collision and discrepancy markers
10. **Tab 4 – Decoded values** — live signal value display, updates at UI poll rate (~10 Hz)
11. **Flag / highlight controls** — right-click context menu; state persisted in a sidecar JSON file alongside the session log
12. **Non-N2K auto-accentuation** — frames failing N2K validation auto-flagged
13. **Fast-packet reassembly** *(want-to-have — also unlocks full Tab 4 product info)*
14. **Message rate, filter bar, CSV export** *(want-to-haves)*
15. **Replay, annotations, pattern guesser** *(ideas — revisit post-core)*

---

## Open Questions

- **"Clear" semantics confirmed**: current log is archived with a timestamp filename; a new log starts. Needs explicit user confirmation in the UI before executing.
- **"Clear" semantics confirmed**: current log is archived with a timestamp filename, then a fresh log starts. Requires explicit user confirmation.

