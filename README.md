# Canboat Explorer

A desktop application for exploring, debugging, and recording CAN / NMEA 2000 bus traffic.

## What it does

- **Verify** what a bus is and isn't transmitting
- **Discover** and reverse-engineer proprietary or obscure manufacturer messages
- **Record** traffic persistently across sessions, including through crashes

## Requirements

- Python 3.11+
- A supported CAN adapter: Waveshare USB-CAN-A, or any adapter supported by python-can (`slcan`, `gs_usb`, `pcan`, `socketcan`)
- NMEA 2000 bus bitrate: 250 kbps

## Installation

```
pip install -e .
```

Or with uv:

```
uv pip install -e .
```

## Running

```
canboat_explorer
```

Settings are written to `canboat_explorer.ini` at the project root on first run. Session logs are saved to `data/` next to the project root.

## Tabs

| Tab | Description |
|-----|-------------|
| **Raw CAN** | All incoming frames. Time view (newest first) or accumulated view grouped by Arb ID, PGN+SA, or PGN. |
| **NMEA 2000** | Assembled NMEA 2000 messages including fast-packet reassembly. Time and accumulated views. |
| **Network** | Device tree — one node per source address. Shows TX/RX PGN lists, observed traffic, collision markers, product info, and address negotiation events. Devices silent for >60 s are dimmed. |
| **Decoded Values** | Live signal values decoded via the `nmea2000` library. 3-level tree (PGN → SA → qualifier variant) with update rates and message history. |

## Session files

Frames are written to `data/session.canlog` on receipt, before any UI processing. The file survives crashes and is reopened automatically on the next start.

- **Save as** — export the current session to a named `.canlog` file
- **Open** — load a saved file for offline browsing
- **Clear** — discard the current session (requires confirmation)

## Supported adapters

Configure the interface type and port via **Settings** in the toolbar. The connection uses NMEA 2000's standard 250 kbps CAN bitrate.
