#!/usr/bin/env python3
"""
Generate canboat_explorer/icon.ico — four pastel rounded squares in a 2×2 grid,
each carrying one letter of N M E A (reading left→right, top→bottom). Pure
standard library: shapes and letter strokes are drawn with signed-distance
fields (cheap per-pixel anti-aliasing at every size) and the PNG/ICO containers
are written by hand, so no Pillow / no external tools.

Run:  python tools/make_icon.py      → writes canboat_explorer/icon.ico
                                        (+ prints an ASCII preview)
"""

import math
import os
import struct
import zlib

# ── palette ──────────────────────────────────────────────────────────────────
BG     = (0x16, 0x1a, 0x1d)        # dark backing tile (shows in the grid gaps)
LETTER = (0x2a, 0x2e, 0x33)        # dark glyph stroke on the pastel squares

# pastel square fills + the letter each one carries, in 2×2 reading order
PINK   = (0xff, 0xb3, 0xba)
GREEN  = (0xb5, 0xea, 0xc1)
BLUE   = (0xae, 0xd9, 0xff)
YELLOW = (0xff, 0xe9, 0xa8)

# letter strokes in a local [-1, 1]² box (x right, y down) — list of segments
GLYPHS = {
    "N": [(-1, 1, -1, -1), (-1, -1, 1, 1), (1, -1, 1, 1)],
    "M": [(-1, 1, -1, -1), (-1, -1, 0, 0.35), (1, -1, 0, 0.35), (1, 1, 1, -1)],
    "E": [(-1, -1, -1, 1), (-1, -1, 1, -1), (-1, 0, 0.7, 0), (-1, 1, 1, 1)],
    "A": [(-1, 1, 0, -1), (1, 1, 0, -1), (-0.5, 0.25, 0.5, 0.25)],
}

# (x-sign, y-sign, fill, letter)
CELLS = ((-1, -1, PINK,   "N"),
         (+1, -1, GREEN,  "M"),
         (-1, +1, BLUE,   "E"),
         (+1, +1, YELLOW, "A"))

SIZES = (256, 128, 64, 48, 32, 24, 16)


def clamp(v, a=0.0, b=1.0):
    return a if v < a else b if v > b else v


# ── signed-distance fields (centre-relative coords, +y down) ─────────────────
def sd_round_box(px, py, hw, hh, r):
    qx = abs(px) - hw + r
    qy = abs(py) - hh + r
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def sd_segment(px, py, ax, ay, bx, by):
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    h = clamp((pax * bax + pay * bay) / (bax * bax + bay * bay))
    return math.hypot(pax - bax * h, pay - bay * h)


def _over(dst, color, a):
    """src-over: paint `color` at coverage `a` onto a straight-alpha
    (r, g, b, alpha) tuple."""
    r, g, b, da = dst
    return (color[0] * a + r * (1 - a),
            color[1] * a + g * (1 - a),
            color[2] * a + b * (1 - a),
            a + da * (1 - a))


def render(S):
    cx = cy = S / 2.0
    hw = hh = 0.470 * S            # backing-tile half-size
    rr = 0.190 * S                 # backing-tile corner radius

    q       = 0.220 * S            # square centre offset from tile centre
    sq_h    = 0.195 * S            # pastel square half-size
    sq_rr   = 0.055 * S            # pastel square corner radius
    lw      = 0.085 * S            # letter half-width
    lh      = 0.105 * S            # letter half-height
    sw      = 0.020 * S            # letter stroke half-width

    img = bytearray(S * S * 4)
    for y in range(S):
        for x in range(S):
            px = x + 0.5 - cx
            py = y + 0.5 - cy
            d = (0.0, 0.0, 0.0, 0.0)

            # dark backing tile (visible as the cross-gap between squares)
            cov = clamp(0.5 - sd_round_box(px, py, hw, hh, rr))
            if cov > 0.0:
                d = _over(d, BG, cov)

            # four pastel squares, each with its letter
            for xs, ys, fill, letter in CELLS:
                lx = px - xs * q
                ly = py - ys * q
                covs = clamp(0.5 - sd_round_box(lx, ly, sq_h, sq_h, sq_rr))
                if covs <= 0.0:
                    continue
                d = _over(d, fill, covs)
                best = 0.0
                for x0, y0, x1, y1 in GLYPHS[letter]:
                    dist = sd_segment(lx, ly, x0 * lw, y0 * lh, x1 * lw, y1 * lh)
                    cl = clamp(0.5 - (dist - sw))
                    if cl > best:
                        best = cl
                if best > 0.0:
                    d = _over(d, LETTER, best * covs)

            i = (y * S + x) * 4
            img[i + 0] = int(clamp(d[0], 0, 255))
            img[i + 1] = int(clamp(d[1], 0, 255))
            img[i + 2] = int(clamp(d[2], 0, 255))
            img[i + 3] = int(clamp(d[3] * 255.0, 0, 255))
    return bytes(img)


# ── PNG / ICO encoders ───────────────────────────────────────────────────────
def png_bytes(S, rgba):
    def chunk(typ, data):
        body = typ + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0)   # 8-bit RGBA
    raw = bytearray()
    stride = S * 4
    for y in range(S):
        raw.append(0)                                      # filter: none
        raw += rgba[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def bmp_bytes(S, rgba):
    """Classic BMP/DIB icon image: BITMAPINFOHEADER + bottom-up 32-bit BGRA XOR
    bitmap + 1-bpp AND mask (1 = transparent, for legacy renderers)."""
    hdr = struct.pack("<IiiHHIIiiII", 40, S, S * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(S - 1, -1, -1):                          # bottom-up
        for x in range(S):
            i = (y * S + x) * 4
            xor += bytes((rgba[i + 2], rgba[i + 1], rgba[i + 0], rgba[i + 3]))
    stride = ((S + 31) // 32) * 4
    andm = bytearray()
    for y in range(S - 1, -1, -1):
        bits = bytearray(stride)
        for x in range(S):
            if rgba[(y * S + x) * 4 + 3] < 128:
                bits[x >> 3] |= 0x80 >> (x & 7)
        andm += bits
    return hdr + bytes(xor) + bytes(andm)


def ico_bytes(entries):
    """entries: list of (size, blob). Each blob is a PNG (>=128) or BMP/DIB."""
    out = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    blob = b""
    for size, png in entries:
        wh = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(png), offset)
        blob += png
        offset += len(png)
    return out + blob


def ascii_preview(S=44):
    rgba = render(S)
    lines = []
    for y in range(S):
        row = []
        for x in range(S):
            i = (y * S + x) * 4
            a = rgba[i + 3]
            tot = rgba[i] + rgba[i + 1] + rgba[i + 2]
            row.append(" " if a < 100 else
                       "#" if tot < 300 else      # dark glyph / backing tile
                       ".")                        # pastel square
        lines.append("".join(row))
    return "\n".join(lines)


if __name__ == "__main__":
    print(ascii_preview())
    entries = []
    for s in SIZES:
        rgba = render(s)
        entries.append((s, png_bytes(s, rgba) if s >= 128 else bmp_bytes(s, rgba)))

    out_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), os.pardir,
                     "canboat_explorer", "icon.ico"))
    with open(out_path, "wb") as f:
        f.write(ico_bytes(entries))
    print(f"\nwrote {out_path} ({sum(len(p) for _, p in entries)} bytes, "
          f"sizes {', '.join(str(s) for s in SIZES)})")
