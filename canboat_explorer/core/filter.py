"""
Global view filter — pure, Qt-free, unit-testable.

A single `FilterSpec` is shared across all four tabs (see DESIGN.md). The UI
populates its fields directly from discrete inputs (PGN / SA / free-text +
flag toggles); there is no query-string grammar. Each tab extracts the fields
it can supply from its own row/node and calls `matches`. A field a tab cannot
provide is passed as `None` and treated as pass-through there, so the global
filter never blanks a tab on an inapplicable key.

Result: visible = pgn ∧ sa ∧ text ∧ flagToggles ∧ ¬(hideIgnored ∧ ignored).
Empty inputs / inactive toggles are pass-through.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def parse_int_list(text: str) -> frozenset[int]:
    """Parse a comma-separated list of integers; skip blank/invalid tokens."""
    out: set[int] = set()
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
            continue
    return frozenset(out)


@dataclass(frozen=True)
class FilterSpec:
    pgns:         frozenset[int] = field(default_factory=frozenset)  # OR; empty = pass
    sas:          frozenset[int] = field(default_factory=frozenset)  # OR; empty = pass
    text:         str            = ""    # already lower-cased; empty = pass
    warnings:     bool           = False
    errors:       bool           = False
    highlighted:  bool           = False
    hide_ignored: bool           = False

    @property
    def is_empty(self) -> bool:
        """True when nothing restricts the view — lets models skip filtering."""
        return not (self.pgns or self.sas or self.text
                    or self.warnings or self.errors or self.highlighted
                    or self.hide_ignored)

    def matches(
        self, *,
        pgn:            int | None  = None,
        sa:             int | None  = None,
        text:           str         = "",
        is_warning:     bool | None = None,
        is_error:       bool | None = None,
        is_highlighted: bool | None = None,
        is_ignored:     bool        = False,
    ) -> bool:
        if self.pgns and pgn is not None and pgn not in self.pgns:
            return False
        if self.sas and sa is not None and sa not in self.sas:
            return False
        if self.text and self.text not in (text or "").lower():
            return False

        # Flag quick-toggles: restrict to rows carrying an active flag class.
        # Multiple active toggles combine as OR. A flag dimension a tab cannot
        # supply is passed as None and dropped from the OR set, so a tab where
        # *every* active toggle is inapplicable passes through.
        if self.warnings or self.errors or self.highlighted:
            hits: list[bool] = []
            if self.warnings    and is_warning     is not None: hits.append(is_warning)
            if self.errors      and is_error       is not None: hits.append(is_error)
            if self.highlighted and is_highlighted is not None: hits.append(is_highlighted)
            if hits and not any(hits):
                return False

        if self.hide_ignored and is_ignored:
            return False

        return True
