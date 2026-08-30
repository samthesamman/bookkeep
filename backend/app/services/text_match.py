"""Title matching — is this search result the book we actually asked for?

Used to guard the Google Books / Open Library / Hardcover lookups against loosely
related hits (a book whose title merely shares a few words, especially when the
wanted title has a long ``Main Title: subtitle`` shape).
"""
from __future__ import annotations

import re

# Dropped before comparing so subtitle filler words can't carry a match.
_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with"}


def tokens(text: str) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
    return {w for w in words if len(w) > 1 and w not in _STOP}


def _main(text: str) -> set[str]:
    """Tokens of the part before the first ':' — i.e. the title without subtitle."""
    return tokens((text or "").split(":", 1)[0])


def titles_match(wanted: str, got: str) -> bool:
    tw, tg = tokens(wanted), tokens(got)
    if not tw or not tg:
        return False
    if tw == tg:
        return True

    # Full containment of the shorter title in the longer (handles a subtitle on
    # one side only: "Foo" vs "Foo: a subtitle", or a series prefix "The Final
    # Empire" vs "Mistborn: The Final Empire").
    smaller, larger = (tw, tg) if len(tw) <= len(tg) else (tg, tw)
    if len(smaller) >= 2 and smaller <= larger:
        # ...but reject when the *candidate* matched only words from the wanted
        # title's subtitle ("Healing Trauma" inside "No Bad Parts: … Healing
        # Trauma …") — a real match touches the main title.
        if smaller is tg and tg.isdisjoint(_main(wanted)):
            return False
        return True

    # Otherwise require a strong overlap across the full titles.
    return len(tw & tg) / len(tw | tg) >= 0.7
