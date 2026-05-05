"""
Command-line CAN frame monitor.

Usage:
    python listen_can.py [interface] [channel]

Defaults to waveshare / COM7.

Examples:
    python listen_can.py
    python listen_can.py -p COM21
    python listen_can.py waveshare -p COM7
    python listen_can.py slcan -p COM5
    python listen_can.py --raw -p COM21
"""
from __future__ import annotations

import sys
import time

from canboat_explorer.core.n2k import pgn_from_id, pgn_name

BITRATE = 250_000


def _open_bus(interface: str, channel: str | int):
    if interface == "waveshare":
        from canboat_explorer.bus.waveshare_bus import WaveshareCANBus
        return WaveshareCANBus(channel=str(channel), bitrate=BITRATE)

    import can
    kwargs: dict = {"interface": interface, "channel": channel}
    if interface in ("slcan", "gs_usb", "pcan"):
        kwargs["bitrate"] = BITRATE
    return can.Bus(**kwargs)


def _fmt(msg) -> str:
    pgn   = pgn_from_id(msg.arbitration_id)
    name  = pgn_name(pgn)
    src   = msg.arbitration_id & 0xFF
    prio  = (msg.arbitration_id >> 26) & 0x07
    ext   = "ext" if msg.is_extended_id else "std"
    data  = bytes(msg.data).hex(" ").upper()
    name_part = f"  ({name})" if name else ""
    return (f"PGN {pgn:6d}{name_part:<28s}  "
            f"src={src:3d}  prio={prio}  dlc={msg.dlc}  [{ext}]  {data}")


def monitor(interface: str = "waveshare", channel: str | int = "COM7") -> None:
    print(f"Opening {interface} / {channel} @ {BITRATE} bps ...")
    try:
        bus = _open_bus(interface, channel)
    except Exception as exc:
        print(f"ERROR: could not open bus: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Listening — Ctrl+C to stop\n")
    n = 0
    try:
        while True:
            try:
                msg = bus.recv(timeout=2.0)
            except Exception as exc:
                print(f"  [recv error: {exc}]")
                continue

            if msg is None:
                print("  [no message in 2 s]")
                continue

            n += 1
            ts = time.strftime("%H:%M:%S")
            print(f"[{n:5d}]  {ts}  {_fmt(msg)}")
    except KeyboardInterrupt:
        print(f"\nStopped after {n} frames.")
    finally:
        bus.shutdown()


def raw_dump(port: str, baudrate: int = 2_000_000, seconds: float = 4.0) -> None:
    """Print raw bytes from the serial port — diagnose protocol/baud mismatch."""
    import serial as _serial
    print(f"Raw dump: {port} @ {baudrate} baud for {seconds:.0f} s\n")
    try:
        ser = _serial.Serial(port, baudrate, timeout=0.1)
    except Exception as exc:
        print(f"Cannot open {port}: {exc}", file=sys.stderr)
        sys.exit(1)

    deadline = time.time() + seconds
    buf = bytearray()
    total = 0
    try:
        while time.time() < deadline:
            chunk = ser.read(256)
            if not chunk:
                continue
            buf.extend(chunk)
            total += len(chunk)
            while len(buf) >= 16:
                row = bytes(buf[:16])
                del buf[:16]
                hex_part   = " ".join(f"{b:02X}" for b in row)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
                print(f"  {hex_part}  |{ascii_part}|")
    except KeyboardInterrupt:
        pass
    finally:
        if buf:
            hex_part   = " ".join(f"{b:02X}" for b in buf)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in buf)
            print(f"  {hex_part:<47}  |{ascii_part}|")
        ser.close()

    print(f"\n{total} bytes in {seconds:.0f} s")
    if total == 0:
        print("No bytes — baud rate wrong or wrong port.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="CAN frame monitor")
    ap.add_argument("interface", nargs="?", default="waveshare")
    ap.add_argument("-p", "--port", default="COM7", help="serial port / channel")
    ap.add_argument("--raw", action="store_true", help="raw byte dump mode")
    ap.add_argument("--baud", type=int, default=2_000_000, help="UART baud (raw mode)")
    ap.add_argument("--seconds", type=float, default=4.0, help="dump duration (raw mode)")
    ns = ap.parse_args()

    if ns.raw:
        raw_dump(ns.port, ns.baud, ns.seconds)
    else:
        monitor(interface=ns.interface, channel=ns.port)
