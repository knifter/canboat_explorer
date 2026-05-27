# DataStore Data Audit

Current state as of 2026-05-27, after the `pgn_catalogue` / `n2k_frame_info`
refactor (commit `db4b656`). Documents all data structures in `DataStore`,
their write/read patterns, and what can still be simplified.

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
| `frames` | `list[can.Message]` | Every frame via `_add()` | `tab_raw_can` TimeViewModel (indexed by row), count label, `bulk_load` iteration | Irreplaceable. The canonical time log. |
| `n2k_messages` | `list[N2KMessage]` | Every output of `_fp.feed()` in `_add()` | `tab_n2k` TimeViewModel (indexed by row), `N2KAccumViewModel.refresh()` (incremental scan), `tab_network` (count, raw message scan) | Irreplaceable. Assembled fast-packet log. |

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
| `pgn_sa_names` | `dict[tuple[int,int], str]` | (pgn, sa) | `decoded.description` (resolved variant name) | Every successful decode in `_add()`, overwriting previous value | `pgn_sa_name()` method (read during rendering by both tab_raw_can and tab_n2k); `get_flag_reason()` (collision variant list, multi-SA SA list) |

Holds the *variant-specific* description returned by the nmea2000 decoder (e.g.
the manufacturer-resolved name for a proprietary PGN). When no entry exists,
`pgn_sa_name()` falls back to the generic catalogue description via
`pgn_name(pgn)` in `pgn_catalogue`. Small dict in practice: one entry per
(pgn, sa) pair seen, typically 5–15 entries on a real N2K bus.

### Private flag-detection sets

| Name | Type | Written | Read by | Can be removed? |
|---|---|---|---|---|
| `_unknown_pgns` | `set[int]` | Add on decode fail; discard when decode later succeeds | `get_flag_by_pgn` (warning), `get_flag_reason` | **Yes** — `pgn in by_pgn and pgn not in _known_pgns` gives identical information. `by_pgn` only contains N2K PGNs. |
| `_known_pgns` | `set[int]` | Add on every successful decode, never removed | `get_flag_by_pgn` (guards unknown-pgn warning), `get_flag_reason` | Keep — needed regardless of whether `_unknown_pgns` is removed |
| `_unknown_arbs` | `set[int]` | Add when non-N2K (standard 11-bit ID) frame seen | `get_flag_by_arb` (warning) | Keep — no equivalent elsewhere |
| `_multi_sa_pgns` | `set[int]` | Add when a decoded PGN is seen from a second SA | `get_flag_by_pgn` (warning), `get_flag_reason` | **Maybe** — fully derivable from `pgn_sa_names`: `len({s for (p,s) in pgn_sa_names if p==pgn}) > 1`. Tiny dict, O(n) iteration during rendering only. |
| `_collision_pgns` | `set[int]` | Add when same PGN produces different description strings from different SAs | `get_flag_by_pgn` (error), `get_flag_reason` | **Maybe** — derivable from `pgn_sa_names`: `len({desc for (p,_),desc in pgn_sa_names.items() if p==pgn}) > 1` |
| `_incomplete_fp_pgns` | `set[int]` | Add when `_fp.feed()` emits a stale/timed-out buffer (`is_incomplete=True`) | `get_flag_by_pgn` (error, guarded by `_complete_fp_pgns`), `get_flag_reason` | Keep — no other source |
| `_complete_fp_pgns` | `set[int]` | Add when `_fp.feed()` emits a successfully assembled multi-frame message (`frame_count > 1`) | `get_flag_by_pgn` (guards the incomplete error) | Keep — no other source |

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

### What can still be removed

**`_unknown_pgns`** — replace with `pgn in by_pgn and pgn not in _known_pgns`.
`by_pgn` is already maintained and only contains N2K PGNs. No behaviour change.

Note: an alternative definition `pgn in by_pgn and pgn not in PGN_LOOKUP`
would *not* be equivalent, but also wouldn't be more useful — the `nmea2000`
library's decode functions are generated 1:1 from `canboat.json` (verified:
both contain the same 339 PGNs). So a PGN missing from `PGN_LOOKUP` is also
missing from the decoder. The only difference between the two definitions is
runtime decode failure (malformed/truncated payload of a catalogued PGN),
which the current `_known_pgns`-based definition correctly flags as a
warning. Keep the current logic.

**`_multi_sa_pgns` and `_collision_pgns`** — both are cached derivations of
`pgn_sa_names`. Since `pgn_sa_names` is tiny (≤20 entries), computing on demand during
rendering is fine. However, these sets are also used during ingestion in `get_flag_by_pgn`
indirectly — they're O(1) there, vs O(n) if derived. Given n is tiny this is not a real
concern, but it's a trade-off to document.

If removed, the scan in `_add()` that builds these sets can also be removed. The only
cost is that `get_flag_by_pgn` iterates `pgn_sa_names` instead of doing a set lookup.

**`pgn_sa_names` revisited.** Now that `pgn_catalogue.pgn_name()` gives a
generic description for every catalogued PGN, `pgn_sa_names` only earns its
keep for the *variant-resolved* case (proprietary PGNs where the decoder picks
a manufacturer-specific name). For non-variant PGNs the catalogue would
return the same string. Pruning entries that match the catalogue description
would keep the dict smaller and make `_collision_pgns` (which compares
variant strings) more precise — but the current overwrite-every-frame
behaviour is simpler and cheap at current sizes. Not a refactor priority,
just worth noting.

**Remaining after full cleanup:**
```
Public:   frames, n2k_messages, by_arb_id, by_pgn_sa, by_pgn, decoded_by_key, pgn_sa_names
Private:  _known_pgns, _unknown_arbs, _incomplete_fp_pgns, _complete_fp_pgns, _flags
```
