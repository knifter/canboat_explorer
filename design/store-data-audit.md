# DataStore Data Audit

Current state as of 2026-05-27, after the `pgn_catalogue` / `n2k_frame_info`
refactor (commit `db4b656`). Documents all data structures in `DataStore`,
their write/read patterns, and what can still be simplified.

> **Status — planned refactor scheduled (2026-05-27).** This document also
> records the next refactor decided in DESIGN.md: moving all PGN-level
> auto-flag state to per-frame and per-message signals. Items below are
> annotated `[REMOVE]`, `[CHANGE]`, or `[NEW]` to indicate what the refactor
> will touch. See "Implementation plan" at the bottom for phased steps and
> commit boundaries.

### Surrounding modules

- **`core/pgn_catalogue.py`** — Loads bundled `canboat.json` once at import
  time into the module-global `PGN_LOOKUP: dict[int, dict]` (PGN → catalogue
  entry). Exposes `pgn_name(pgn)` for description lookup. This is the single
  source of truth for *static* PGN metadata (description, type/Fast flag, etc.).
- **`core/n2k_frame_info.py`** — `n2k_frame_info(msg)` decodes a CAN frame's
  arbitration ID into a `N2KFrameInfo` NamedTuple containing `pgn`, `description`,
  `sa`, `priority`, `dst`, `grp`, `is_fast`, `seq`, `frame_idx`. The description
  and `is_fast` come from `PGN_LOOKUP`. This is the canonical entry point for
  arb-ID parsing — `store._add()`, `fast_packet.feed()`, and the raw-CAN tab all
  go through it instead of cherry-picking bits inline.

---

## All Structures in `DataStore`

### Public lists (time-ordered logs)

| Name | Type | Written | Read by | Notes |
|---|---|---|---|---|
| `frames` | `list[can.Message]` → `list[RawFrame]` `[CHANGE]` | Every frame via `_add()` | `tab_raw_can` TimeViewModel (indexed by row), count label, `bulk_load` iteration | Irreplaceable. The canonical time log. Element type becomes `RawFrame(can.Message)` with extra `n2k_msg` slot — see Implementation plan Step 1. |
| `n2k_messages` | `list[N2KMessage]` `[CHANGE]` | Every output of `_fp.feed()` in `_add()` | `tab_n2k` TimeViewModel (indexed by row), `N2KAccumViewModel.refresh()` (incremental scan), `tab_network` (count, raw message scan) | Irreplaceable. `N2KMessage` gains three fields: `decoded: bool`, `is_complete: bool` (replacing `is_incomplete`), and `frames: list[RawFrame]` — see Implementation plan Steps 2–4. |

### Public accumulation dicts (one entry per unique key, updated per frame)

| Name | Type | Key | Value | Written | Read by |
|---|---|---|---|---|---|
| `by_arb_id` | `dict[int, AccumEntry]` | arbitration ID | last frame + count + first_ts | Every non-error frame | `tab_raw_can` AccumViewModel "by Arb ID" mode |
| `by_pgn_sa` | `dict[tuple[int,int], AccumEntry]` | (pgn, sa) | last frame + count + first_ts | Every N2K frame | `tab_raw_can` AccumViewModel "by PGN+SA" mode; `tab_network` (SA discovery, per-SA stats, collision detection) |
| `by_pgn` | `dict[int, AccumEntry]` | pgn | last frame + count + first_ts | Every N2K frame | `tab_raw_can` AccumViewModel "by PGN" mode |

These three are the three grouping modes for the Raw CAN accumulated view.
They're separate by design; no duplication.

### Public decoded dict

| Name | Type | Key | Value | Written | Read by |
|---|---|---|---|---|---|
| `decoded_by_key` | `dict[tuple[int,int,tuple], deque]` | (pgn, sa, qualifier) | deque of last 200 decoded messages | Every successful `_n2k_decoder.decode()` in `_add()` | `tab_decoded` (keys iterated for tree, deque read for field values); `tab_network` (specific PGN lookups: 126996 product info, 60928 address claim) |

The qualifier tuple distinguishes manufacturer-specific variants (e.g. different wind instruments
from different brands sharing the same PGN).

### Public SA-keyed name cache

| Name | Type | Key | Value | Written | Read by |
|---|---|---|---|---|---|
| `pgn_sa_names` | `dict[tuple[int,int], str]` | (pgn, sa) | `decoded.description` (resolved variant name) | Every successful decode in `_add()`, overwriting previous value | `pgn_sa_name()` method (read during rendering by both tab_raw_can and tab_n2k); `[CHANGE]` after refactor: also read by `tab_network` for cross-SA variant-collision detection (currently this lives in store via `_collision_pgns` — moves out). |

Holds the *variant-specific* description returned by the nmea2000 decoder (e.g.
the manufacturer-resolved name for a proprietary PGN). When no entry exists,
`pgn_sa_name()` falls back to the generic catalogue description via
`pgn_name(pgn)` in `pgn_catalogue`. Small dict in practice: one entry per
(pgn, sa) pair seen, typically 5–15 entries on a real N2K bus.

### Private flag-detection sets

**All of these are being removed (Step 7 of the implementation plan).** The
new design replaces every entry below with per-frame (`RawFrame.n2k_msg`) or
per-message (`N2KMessage.decoded`, `N2KMessage.is_complete`) state. The
"Replaced by" column shows where each signal moves.

| Name | Type | Written | Read by | Replaced by |
|---|---|---|---|---|
| `_unknown_pgns` `[REMOVE]` | `set[int]` | Add on decode fail; discard when decode later succeeds | `get_flag_by_pgn` (warning), `get_flag_reason` | `n2k_msg.decoded` (per-message). Tab 2 row flag: `not n2k_msg.decoded`. |
| `_known_pgns` `[REMOVE]` | `set[int]` | Add on every successful decode, never removed | `get_flag_by_pgn` (guards unknown-pgn warning), `get_flag_reason` | Same — per-message `decoded` makes "ever decoded successfully" PGN-wide tracking unnecessary. Session reload safety is preserved because `N2KMessage.decoded` is rebuilt on bulk_load. |
| `_unknown_arbs` `[REMOVE]` | `set[int]` | Add when non-N2K (standard 11-bit ID) frame seen | `get_flag_by_arb` (warning) | `frame.n2k_msg is None` (per-frame). Tab 1 row flag: `frame.n2k_msg is None and not frame.is_error_frame`. Catches more than just standard 11-bit IDs — also stale FP continuations and unparseable arb-IDs. |
| `_multi_sa_pgns` `[REMOVE]` | `set[int]` | Add when a decoded PGN is seen from a second SA | `get_flag_by_pgn` (warning), `get_flag_reason` | Detected in `tab_network` only (already has its own scan of `store.by_pgn_sa`). No longer applies row flags in Tabs 1/2. |
| `_collision_pgns` `[REMOVE]` | `set[int]` | Add when same PGN produces different description strings from different SAs | `get_flag_by_pgn` (error), `get_flag_reason` | Detected in `tab_network` only (gets the same `pgn_sa_names`-scan logic that's currently here). No longer applies row flags in Tabs 1/2. |
| `_incomplete_fp_pgns` `[REMOVE]` | `set[int]` | Add when `_fp.feed()` emits a stale/timed-out buffer | `get_flag_by_pgn` (error, guarded by `_complete_fp_pgns`), `get_flag_reason` | `n2k_msg.is_complete` (per-message). Tab 2 row flag: `not n2k_msg.is_complete`. |
| `_complete_fp_pgns` `[REMOVE]` | `set[int]` | Add when `_fp.feed()` emits a multi-frame message | `get_flag_by_pgn` (guards the incomplete error) | Same — no longer needed as a guard since the flag is now per-message. A completed FP message just has `is_complete=True`; the prior incomplete attempt's row keeps `is_complete=False`. |

### Private user-flag dict

| Name | Type | Key | Value | Written | Read by |
|---|---|---|---|---|---|
| `_flags` | `dict[str, str]` | `"a:<arb_id>"` or `"p:<pgn>"` | Priority string: "ignore" / "highlight" / "warning" / "error" | `set_flag_by_arb/pgn()` (user right-click); `_load_sidecar()` at startup | `get_flag_by_arb/pgn()` (user override takes priority over auto-flags); serialized to sidecar JSON |

---

## Call Graph

### `_add(msg)` — called on every frame (hot path)

```
_add(msg)
  → frames.append()
  → n2k_frame_info(msg)              [extended-ID only → pgn/sa/priority/is_fast]
      → PGN_LOOKUP.get(pgn)          [catalogue: description, Type]
  → _fp.feed(msg)
      → n2k_frame_info(msg)          [same call inside FP; used for seq/frame_idx/is_fast]
      → n2k_messages.append()
      → _incomplete_fp_pgns.add()    [if stale flush]
      → _complete_fp_pgns.add()      [if frame_count > 1]
  → _n2k_decoder.decode(msg)         [N2K frames only]
      → _unknown_pgns.add/discard()
      → _known_pgns.add()
      → decoded_by_key[key].appendleft()
      → pgn_sa_names[sa_key] = desc
      → (scan pgn_sa_names) → _multi_sa_pgns.add()
      → (scan pgn_sa_names) → _collision_pgns.add()
  → _unknown_arbs.add()              [non-N2K frames only]
  → by_arb_id update
  → by_pgn_sa update                 [N2K only]
  → by_pgn update                    [N2K only]
```

Note: `n2k_frame_info(msg)` is called twice per frame (once in `_add`, once in
`_fp.feed`). Both calls hit `PGN_LOOKUP.get()` (O(1) dict lookup). Cheap, but
worth knowing if profiling ever points here.

### Rendering — called per UI refresh (warm path, not hot)

```
tab_raw_can TimeViewModel.data(col=10)
  → store.pgn_sa_name(pgn, sa)
      → pgn_sa_names.get()           [O(1) — variant-specific decoded name]
      → pgn_name(pgn)                [fallback, reads PGN_LOOKUP in pgn_catalogue.py]

tab_raw_can TimeViewModel.data(ForegroundRole/BackgroundRole)
  → store.get_flag_by_pgn(pgn)       [called per visible row]       TvR: Searches dicts, slow
      → _flags.get()                 [O(1)]
      → _collision_pgns              [O(1) set lookup]
      → _incomplete_fp_pgns          [O(1) set lookup]
      → _complete_fp_pgns            [O(1) set lookup]
      → _unknown_pgns                [O(1) set lookup]
      → _known_pgns                  [O(1) set lookup]
      → _multi_sa_pgns               [O(1) set lookup]
  → store.get_flag_by_arb(arb_id)
      → _flags.get()
      → _unknown_arbs                [O(1) set lookup]

tab_raw_can TimeViewModel.data(ToolTipRole)
  → store.get_flag_reason(pgn)       [called per visible row, returns "" for most]
      → _collision_pgns              [O(1)]
      → pgn_sa_names.items()         [O(n), n≈5-15, builds variant set]
      → _incomplete_fp_pgns          [O(1)]
      → _multi_sa_pgns               [O(1)]
      → pgn_sa_names                 [O(n), builds SA list]
      → _unknown_pgns / _known_pgns  [O(1)]

tab_raw_can AccumViewModel._active_dict()
  → by_arb_id / by_pgn_sa / by_pgn  [direct dict reference]

tab_n2k TimeViewModel.data()
  → n2k_messages[row]               [O(1) index]
  → store.pgn_sa_name()
  → store.get_flag_by_pgn()
  → store.get_flag_reason()

tab_n2k N2KAccumViewModel.refresh()
  → n2k_messages[processed:]        [incremental slice]
  → (local _by_pgn_sa dict — see note below)

tab_decoded
  → decoded_by_key.keys()           [tree population]
  → decoded_by_key.get(key)         [field value display]

tab_network
  → by_pgn_sa.keys()                [SA discovery]
  → by_pgn_sa.items()               [per-SA stats]
  → decoded_by_key.get()            [product info, address claim]
  → n2k_messages                    [raw message scan for time ranges]
  → (own _collision_pgns() method — see note below)
```

---

## Notes / Findings

### `tab_n2k` has its own `_by_pgn_sa` dict
`N2KAccumViewModel` maintains a local `_by_pgn_sa: dict[tuple[int,int], N2KMessage]`.
This is **not** the same as `store.by_pgn_sa` — the store version holds `AccumEntry`
(raw CAN frames), the tab version holds `N2KMessage` (assembled fast-packet payloads).
Different data, different purpose. Not a duplicate.

### `tab_network` has its own `_collision_pgns()` method
`tab_network.py` line 119 has a local `_collision_pgns()` method that scans
`store.by_pgn_sa` to detect PGNs from multiple SAs. Note: this detects the
*multi-SA* condition (same as `store._multi_sa_pgns`), not the stronger
*different-variant* condition that `store._collision_pgns` checks (which
requires resolved description strings to differ). If the store exposes a
multi-SA accessor, `tab_network` should use it instead of recomputing.

### What changed in the `pgn_catalogue` / `n2k_frame_info` refactor (db4b656)

- Extracted catalogue loading and `pgn_name()` from `n2k.py` into a dedicated
  `core/pgn_catalogue.py` module. `PGN_LOOKUP` is now imported wherever needed
  instead of being a side-effect of importing `n2k.py`.
- Introduced `core/n2k_frame_info.py` with the `N2KFrameInfo` NamedTuple and
  the `n2k_frame_info(msg)` function. This is the single place that parses
  N2K arbitration IDs (`pgn`/`sa`/`priority`/`dst`/`grp`) and joins them
  with catalogue data (`description`/`is_fast`) and fast-packet header bytes
  (`seq`/`frame_idx`).
- `store._add()` no longer extracts PGN/SA/priority with inline bit-shifts —
  it calls `n2k_frame_info(msg)` once and reads fields off the NamedTuple.
- `fast_packet.feed()` was rewritten on top of `n2k_frame_info()`. The
  Fast-packet heuristic now consults `n2k.is_fast` (which combines "PGN
  marked Fast in the catalogue" with "PGN unknown to the catalogue"); the
  earlier hand-coded `_KNOWN_NON_FAST` set is gone.
- `tab_raw_can` parses frames via `n2k_frame_info()` instead of cherry-picking
  arb-ID bits in the view model.
- `tab_network` imports `pgn_name` from `pgn_catalogue` (previously `n2k`).

### Remaining after the planned refactor

```
Public:   frames (list[RawFrame]), n2k_messages, by_arb_id, by_pgn_sa, by_pgn,
          decoded_by_key, pgn_sa_names
Private:  _flags
```

Every other private set goes away. `get_flag_by_arb()` and `get_flag_by_pgn()`
collapse to user-flag lookups only (or are removed if no caller remains after
Tabs 1/2 read per-frame/per-message state directly).

---

## Implementation plan

Derived from the design session on 2026-05-27. The work moves PGN-level
auto-flag state to per-frame and per-message signals. Each step below leaves
the app in a working state.

### Step 1 — `RawFrame` plumbing (no behaviour change)
- New `core/raw_frame.py`: `class RawFrame(can.Message)` with
  `__slots__ = ('n2k_msg',)`, default `None`.
- `session_log.decode()` constructs `RawFrame` instead of `can.Message`.
- `store._add()` wraps the incoming `can.Message` into a `RawFrame` at entry
  (cheap, keeps reader/queue typing simple).
- Update annotations: `self.frames: list[RawFrame]`.
- Result: nothing reads `n2k_msg` yet. Everything still works.

### Step 2 — Extend `N2KMessage`
- Add to the dataclass in `core/fast_packet.py`:
  - `decoded: bool = False`
  - `is_complete: bool = True` (replaces `is_incomplete: bool = False` — inverted polarity so the common case is the default)
  - `frames: list[RawFrame] = field(default_factory=list)`
- Update the existing emit sites in `FastPacketReassembler` to write
  `is_complete=False` where they currently write `is_incomplete=True`.
- No writes yet for `decoded` or `frames`.

### Step 3 — `FastPacketReassembler` tracks contributing frames
- Add `contributing_frames: list[RawFrame]` to `_Buffer`.
- Append the current frame on every consumed call (frame 0 init + each
  continuation).
- On emit (completion AND stale-flush AND orphan-continuation AND
  single-frame-PGN pass-through): hand the frame list to the new `N2KMessage`.

### Step 4 — Wire decoder result into `N2KMessage.decoded` and back-link frames
- In `store._add()`, after computing `decoded = self._n2k_decoder.decode(msg)`:
  - Loop over `self._fp.feed(msg)`:
    - `is_complete=False` (stale-flushed) message → `n2k_msg.decoded = False`
    - Otherwise → `n2k_msg.decoded = decoded is not None`
  - For each emitted `N2KMessage`, walk `n2k_msg.frames` and set
    `frame.n2k_msg = n2k_msg`.
- `decoded_by_key` stash logic unchanged.

### Step 5 — Update Raw CAN view to use frame-level flags
- `tab_raw_can` flag logic for a frame row:
  - `frame.is_error_frame` → existing handling
  - `frame.n2k_msg is None` → warning (out-of-band)
  - Else → no flag from store
- Remove calls to `store.get_flag_by_pgn()` from this tab.

### Step 6 — Update N2K view to use per-message flags
- `tab_n2k` flag logic for an N2KMessage row:
  - `not n2k_msg.is_complete` → error (FP timed out)
  - `not n2k_msg.decoded` → warning (payload didn't decode)
  - User flag from `_flags` dict (priority override) → as today
- Remove calls to `store.get_flag_by_pgn()` from this tab.

### Step 7 — Strip dead state from `DataStore`
Delete from `core/store.py`:
- Fields: `_unknown_pgns`, `_known_pgns`, `_unknown_arbs`,
  `_incomplete_fp_pgns`, `_complete_fp_pgns`, `_multi_sa_pgns`,
  `_collision_pgns`.
- Their writes in `_add()` (the scan loop on `pgn_sa_names`, the
  `_unknown_arbs.add()`, the FP completeness sets).
- Their clears in `reset_memory()`.
- Simplify `get_flag_by_pgn()` to user-override-only (or remove entirely if no
  caller remains).
- Simplify or remove `get_flag_reason()` accordingly.

### Step 8 — Confirm `tab_network` covers the dropped detection
- It already has a local `_collision_pgns()` method scanning `by_pgn_sa` —
  that handles multi-SA detection. Keep it.
- Add variant-collision detection there too (the logic currently in
  `store._collision_pgns` — scans `pgn_sa_names` for distinct description
  strings per PGN). Same logic, new home.
- `decoded_by_key` lookups for 60928/126996 are unchanged.

### Step 9 — Refresh this audit doc post-implementation
- Remove the `[REMOVE]`/`[CHANGE]`/`[NEW]` annotations once the refactor
  lands.
- Update the call graph to drop the deleted set lookups.
- Document `RawFrame.n2k_msg` and `N2KMessage.{decoded, is_complete, frames}`
  as the per-row signal carriers in the main tables.
- Note that `get_flag_by_pgn` is user-flags-only (or removed).

---

## Commit boundaries

Each commit is independently revertible and leaves the app functional.

| Commit | Steps | Result |
|---|---|---|
| A | 1, 2 | `RawFrame` and new `N2KMessage` fields exist; nothing reads them yet. Old flag system unchanged. |
| B | 3, 4 | FP tracks contributing frames; decoder result attached to messages; back-links populated. Old flags still rendered (now alongside new state — redundant but harmless). |
| C | 5, 6 | Tabs switch to reading new per-row state. Old PGN-level sets become dead writes. User-visible behaviour matches design. |
| D | 7, 8 | Old state deleted from store; `tab_network` becomes sole owner of multi-SA / variant-collision detection. Pure deletion — readers already rewired in C. |
| E | 9 | Audit doc rewritten to reflect new reality. |

After Commit C the user-visible flag system is the new one. After Commit D
the store is at its target shape. Commit E is paperwork.
