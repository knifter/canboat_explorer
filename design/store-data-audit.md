# DataStore Data Audit

Current state as of 2026-05-28, after the per-row flag refactor (Commits A–E
following commit `db4b656`). Documents all data structures in `DataStore`,
their write/read patterns, and how the surrounding modules wire into them.

### Surrounding modules

- **`core/pgn_catalogue.py`** — Loads bundled `canboat.json` once at import
  time into the module-global `PGN_LOOKUP: dict[int, dict]` (PGN → catalogue
  entry). Exposes `pgn_name(pgn)` for description lookup. Single source of
  truth for *static* PGN metadata (description, Fast/Single classification).
- **`core/n2k_frame_info.py`** — `n2k_frame_info(msg)` decodes a CAN frame's
  arbitration ID into a `N2KFrameInfo` NamedTuple containing `pgn`,
  `description`, `sa`, `priority`, `dst`, `grp`, `is_fast`, `seq`,
  `frame_idx`. The description and `is_fast` come from `PGN_LOOKUP`. Canonical
  entry point for arb-ID parsing — `store._add()`, `fast_packet.feed()`, and
  `tab_raw_can` all go through it.
- **`core/raw_frame.py`** — `class RawFrame(can.Message)` with one extra slot
  `n2k_msg: N2KMessage | None`. Constructed at every ingress
  (`SessionLog.decode()` and `store.ingest()`). `RawFrame` IS-A `can.Message`,
  so the nmea2000 library and any other consumer that types `can.Message`
  works unchanged. The `n2k_msg` back-link is populated by the FP reassembler
  when the assembled message emits.
- **`core/fast_packet.py`** — `FastPacketReassembler` plus the `N2KMessage`
  dataclass. The reassembler emits `N2KMessage` objects on completion (or
  stale flush) and tracks contributing `RawFrame`s in `_Buffer` so it can
  back-link each contributing frame at emit time.

---

## All Structures in `DataStore`

### Public lists (time-ordered logs)

| Name | Type | Written | Read by | Notes |
|---|---|---|---|---|
| `frames` | `list[RawFrame]` | Every frame via `_add()` | `tab_raw_can` TimeViewModel (indexed by row), count label, `bulk_load` iteration | Canonical time log. Element type is `RawFrame` (can.Message subclass). |
| `n2k_messages` | `list[N2KMessage]` | Every output of `_fp.feed()` in `_add()` | `tab_n2k` TimeViewModel (indexed by row), `N2KAccumViewModel.refresh()` (incremental scan), `tab_network` (count, raw message scan) | Assembled fast-packet log. Each message carries `decoded: bool`, `is_complete: bool`, and `frames: list[RawFrame]` (contributing frames). |

### Public accumulation dicts (one entry per unique key, updated per frame)

| Name | Type | Key | Value | Written | Read by |
|---|---|---|---|---|---|
| `by_arb_id` | `dict[int, AccumEntry]` | arbitration ID | last frame + count + first_ts | Every non-error frame | `tab_raw_can` AccumViewModel "by Arb ID" mode |
| `by_pgn_sa` | `dict[tuple[int,int], AccumEntry]` | (pgn, sa) | last frame + count + first_ts | Every N2K frame | `tab_raw_can` AccumViewModel "by PGN+SA" mode; `tab_network` (SA discovery, per-SA stats, multi-SA detection) |
| `by_pgn` | `dict[int, AccumEntry]` | pgn | last frame + count + first_ts | Every N2K frame | `tab_raw_can` AccumViewModel "by PGN" mode |

The three grouping modes for the Raw CAN accumulated view. Separate by
design; no duplication.

### Public decoded dict

| Name | Type | Key | Value | Written | Read by |
|---|---|---|---|---|---|
| `decoded_by_key` | `dict[tuple[int,int,tuple], deque]` | (pgn, sa, qualifier) | deque of last 200 decoded messages | Every successful `_n2k_decoder.decode()` in `_add()` | `tab_decoded` (keys iterated for tree, deque read for field values); `tab_network` (specific PGN lookups: 60928 address claim, 126464 PGN list, 126996 product info) |

The qualifier tuple distinguishes manufacturer-specific variants. Bounded
deque (`maxlen=200`) caps memory regardless of session length.

### Public SA-keyed variant-name cache

| Name | Type | Key | Value | Written | Read by |
|---|---|---|---|---|---|
| `pgn_sa_names` | `dict[tuple[int,int], str]` | (pgn, sa) | `decoded.description` (resolved variant name) | Every successful decode in `_add()`, overwriting previous value | `pgn_sa_name()` method (used during rendering by both tab_raw_can and tab_n2k); `tab_network._collision_tooltip()` for variant-collision detection |

Holds the *variant-specific* description returned by the nmea2000 decoder
(e.g. the manufacturer-resolved name for a proprietary PGN). When no entry
exists, `pgn_sa_name()` falls back to `pgn_catalogue.pgn_name(pgn)`. Small
dict in practice: one entry per (pgn, sa) pair seen, typically 5–15 entries.

### Public auto-flag counters (tab-header indicators)

| Name | Type | Written | Read by |
|---|---|---|---|
| `oob_frame_count` | `int` | Incremented in `_add()` when `n2k_frame_info()` returns None (non-extended-ID or unparseable frame) | `tab_raw_can.has_warnings()` |
| `incomplete_msg_count` | `int` | Incremented when `_fp.feed()` emits a stale-flushed N2KMessage (`is_complete=False`) | `tab_n2k.has_warnings()` |
| `undecoded_msg_count` | `int` | Incremented when a complete N2KMessage's decoder call returned None | `tab_n2k.has_warnings()` |

All three cleared in `reset_memory()`. Drive the `⚠` marker prepended to
each tab's header label by `main_window._update_tab_warnings()` after every
drain and after open/clear. Tab 3 computes its own warning state from
`store.by_pgn_sa` and TX-advertised PGNs (see Tab 3 notes below).

### Private user-flag dict

| Name | Type | Key | Value | Written | Read by |
|---|---|---|---|---|---|
| `_flags` | `dict[str, str]` | `"a:<arb_id>"` or `"p:<pgn>"` | Priority string: "ignore" / "highlight" / "warning" / "error" | `set_flag_by_arb/pgn()` (user right-click); `_load_sidecar()` at startup | `get_user_flag_by_arb/pgn()`; serialised to sidecar JSON |

The only flag state on the store is the user override dict. All auto-flag
detection is now per-row (frame-level or message-level), not PGN-aggregate.

---

## Call Graph

### `_add(msg)` — called on every frame (hot path)

```
_add(msg)
  → frames.append()
  → [early-return if is_error_frame]
  → n2k_frame_info(msg)              [extended-ID only → pgn/sa/is_fast/desc]
      → PGN_LOOKUP.get(pgn)          [catalogue: description, Type]
  → oob_frame_count += 1             [if non-N2K]
  → _n2k_decoder.decode(msg)         [N2K frames only — library does its own FP]
  → _fp.feed(msg)
      → n2k_frame_info(msg)          [same call inside FP; used for seq/frame_idx]
      → emit N2KMessage(s)           [single-frame / FP-complete / stale-flushed / orphan]
          ├── n2k_messages.append()
          ├── if not is_complete:
          │       incomplete_msg_count += 1
          │       n2k_msg.decoded stays False
          │   else:
          │       n2k_msg.decoded = (decoder returned non-None for THIS msg)
          │       if not decoded: undecoded_msg_count += 1
          └── for frame in n2k_msg.frames: frame.n2k_msg = n2k_msg
  → if decoded is not None:
      → decoded_by_key[key].appendleft(decoded)
      → pgn_sa_names[(pgn, sa)] = decoded.description
  → by_arb_id update
  → by_pgn_sa update                 [N2K only]
  → by_pgn update                    [N2K only]
```

Note: `n2k_frame_info(msg)` is called twice per frame (once in `_add`, once
in `_fp.feed`). Both calls hit `PGN_LOOKUP.get()` (O(1)). Cheap.

### Rendering — per UI refresh

```
tab_raw_can TimeViewModel.data(col=10)
  → store.pgn_sa_name(pgn, sa)
      → pgn_sa_names.get()           [O(1) — variant-specific decoded name]
      → pgn_name(pgn)                [fallback, reads PGN_LOOKUP]

tab_raw_can TimeViewModel.data(Foreground/Background/ToolTip)
  → _flag_for(frame, n2k)
      → store.get_user_flag_by_pgn(pgn)  [O(1) dict lookup, user override only]
      → store.get_user_flag_by_arb(arb)  [O(1)]
      → frame.n2k_msg is None?           [O(1) attribute check → warning if true]

tab_n2k TimeViewModel.data(Foreground/Background/ToolTip)
  → _flag_for_message(store, msg)
      → store.get_user_flag_by_pgn(pgn)  [O(1)]
      → not msg.is_complete?             [O(1) → error if true]
      → not msg.decoded?                 [O(1) → warning if true]

tab_decoded
  → decoded_by_key.keys()           [tree population]
  → decoded_by_key.get(key)         [field value display]

tab_network
  → _all_sa() / _observed_pgns(sa)   [iterate by_pgn_sa]
  → _last_seen_ts(sa)                [iterate by_pgn_sa, max timestamp]
  → decoded_by_key.get()             [product info 126996, address claim 60928]
  → _get_pgn_lists(sa)               [decode 126464 → TX/RX PGN lists]
  → _collision_pgns()                [observed ∪ TX-advertised, len ≥ 2]
  → _collision_tooltip(pgn)          [variants from pgn_sa_names → variant collision vs multi-SA tooltip]
  → n2k_messages                     [address negotiation log scan]

main_window._update_tab_warnings()
  → tab_raw.has_warnings()           → store.oob_frame_count > 0
  → tab_n2k.has_warnings()           → store.incomplete_msg_count + undecoded_msg_count > 0
  → tab_network.has_warnings()       → bool(self._collision_pgns())
  → tab_decoded.has_warnings()       → False
```

---

## Notes

### `tab_n2k` has its own `_by_pgn_sa` dict
`N2KAccumViewModel` maintains a local `_by_pgn_sa: dict[tuple[int,int], N2KMessage]`.
This is **not** the same as `store.by_pgn_sa` — the store version holds
`AccumEntry` (raw CAN frames), the tab version holds `N2KMessage` (assembled
fast-packet payloads). Different data, different purpose. Not a duplicate.

### `tab_network` is sole owner of network-level collision detection
- `_collision_pgns()` returns PGNs where (observed SAs ∪ TX-advertised SAs)
  has ≥2 members. Catches both actual multi-SA transmission and the
  "potential collision" case where multiple devices advertise they could
  send the PGN.
- `_collision_tooltip(pgn)` differentiates three flavours by inspecting
  `pgn_sa_names`:
  - **Variant collision** — same PGN decoded as different manufacturer
    variant strings across SAs (likely SA address collision).
  - **Actual multi-SA** — ≥2 SAs actually observed transmitting, same
    variant.
  - **Potential multi-SA** — observed from <2 SAs but advertised by ≥2.
- The store does not maintain any PGN-aggregate collision/multi-SA sets;
  detection lives entirely in the tab.

### nmea2000 library has its own independent FP reassembler
The library keeps private state per `(pgn, src, dest)` in `self.data` and
silently overwrites buffers on a new sequence, with no stale detection. We
keep our own `FastPacketReassembler` precisely because the library's state
isn't introspectable and provides no signal for stale/incomplete sequences.
Both reassemblers run in parallel; we align their outputs by call timing —
when `_fp.feed(msg)` emits a non-incomplete `N2KMessage`, the decoder's
return on the same `msg` is the decoded form of that same message.

### Dead variable in `_add()`
The `pgn` and `sa` locals are set in the `n2k is None` branch to `0` only
because the post-FP code path uses them as dict keys (`by_pgn`, `by_pgn_sa`)
guarded by `if is_n2k:`. They're never accessed when `is_n2k=False`. Kept
for guard clarity; not worth refactoring.

---

## Refactor history

This document tracks state through one major refactor:

**db4b656 (2026-05-XX)** — Extracted `pgn_catalogue` and `n2k_frame_info` as
central modules. Removed inline arb-ID parsing throughout the codebase.

**Per-row flag refactor (Commits A–E, 2026-05-27/28)** — Replaced all
PGN-level auto-flag state (`_unknown_pgns`, `_known_pgns`, `_unknown_arbs`,
`_incomplete_fp_pgns`, `_complete_fp_pgns`, `_multi_sa_pgns`,
`_collision_pgns`) with per-frame `RawFrame.n2k_msg` and per-message
`N2KMessage.{decoded, is_complete, frames}` signals. Added tab-header
warning markers backed by three store counters. Moved network-level
collision detection entirely into `tab_network`, and extended it to cover
the TX-advertised-but-not-observed case. Deleted `get_flag_by_pgn`,
`get_flag_by_arb`, `get_flag_reason` from the store; only
`get_user_flag_by_*` (user-set overrides) remain. Net result: ~66 lines
deleted from `store.py`, plus the new per-row state plumbing.
