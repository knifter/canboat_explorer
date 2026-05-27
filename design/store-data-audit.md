# DataStore Data Audit

Current state after session 2026-05-07. Documents all data structures in `DataStore`,
their write/read patterns, and what can still be simplified.

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

Previously maintained twice: as `PGN_SA_NAMES` (module-global in `n2k.py`) and as
`_pgn_sa_descs` (instance field on DataStore). Now unified into one dict on the store.
Small dict in practice: one entry per (pgn, sa) pair seen, typically 5–15 entries on a
real N2K bus.

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
  → _fp.feed(msg)
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

### Rendering — called per UI refresh (warm path, not hot)

```
tab_raw_can TimeViewModel.data(col=10)
  → store.pgn_sa_name(pgn, sa)
      → pgn_sa_names.get()           [O(1)]
      → pgn_name(pgn)                [fallback, reads PGN_LOOKUP in n2k.py]

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
`store.by_pgn_sa` to detect PGNs from multiple SAs. This duplicates the logic in
`store._collision_pgns` (the set) and `store._multi_sa_pgns`. If the store exposes
collision/multi-SA info directly, `tab_network` should use it instead of recomputing.

### What was moved this session
- `PGN_SA_NAMES` (module-global mutable dict in `n2k.py`) → `DataStore.pgn_sa_names`
- `pgn_sa_name()` (free function in `n2k.py`) → `DataStore.pgn_sa_name()` method
- `_pgn_sa_descs` (duplicate instance dict) → eliminated, merged into `pgn_sa_names`
- `fast_packet.py` now imports `PGN_LOOKUP` to guard heuristic against known non-Fast PGNs

### What can still be removed

**`_unknown_pgns`** — replace with `pgn in by_pgn and pgn not in _known_pgns`.
`by_pgn` is already maintained and only contains N2K PGNs. No behaviour change.

**`_multi_sa_pgns` and `_collision_pgns`** — both are cached derivations of
`pgn_sa_names`. Since `pgn_sa_names` is tiny (≤20 entries), computing on demand during
rendering is fine. However, these sets are also used during ingestion in `get_flag_by_pgn`
indirectly — they're O(1) there, vs O(n) if derived. Given n is tiny this is not a real
concern, but it's a trade-off to document.

If removed, the scan in `_add()` that builds these sets can also be removed. The only
cost is that `get_flag_by_pgn` iterates `pgn_sa_names` instead of doing a set lookup.

**Remaining after full cleanup:**
```
Public:   frames, n2k_messages, by_arb_id, by_pgn_sa, by_pgn, decoded_by_key, pgn_sa_names
Private:  _known_pgns, _unknown_arbs, _incomplete_fp_pgns, _complete_fp_pgns, _flags
```
