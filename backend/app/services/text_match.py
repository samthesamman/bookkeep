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


# Third-party "companion" works that Hardcover indexes alongside the real book:
# "Summary of Atomic Habits", "Workbook for Thinking Fast and Slow", etc. Matching
# one of these to a request for the original is a false positive.
_DERIVATIVE_MARKERS = re.compile(
    r"\b("
    r"summary|summaries|abridged|abridgement|"
    r"workbook|worksheets?|"
    # "study guide", "literature guide", "reading/teacher's/discussion guide", etc.
    r"(?:study|reading|literature|literary|discussion|teaching|teacher'?s?|"
    r"educator'?s?|instructor'?s?|lesson|novel|classroom|curriculum)[\s-]*guides?|"
    r"study[\s-]*guides?|studyguides?|"
    # "study notes", "revision notes", "chapter notes", etc. (not bare "notes" —
    # real titles like "Notes from Underground" use it)
    r"(?:study|revision|chapter|reader'?s?|lecture|class|reading)[\s-]*notes|"
    r"key takeaways|key insights|key points|key ideas|major themes|"
    r"conversation starters|companion|sidekick|quick ?read|quicklet|"
    r"instaread|blinkist|cliffs?[\s-]*notes|spark[\s-]*notes|shmoop|gradesaver|"
    r"supersummary|super summary|getflashnotes|sumoreads|classicnotes|litcharts|"
    r"trivia (?:on|for)|book review|analysis and summary|summary and analysis"
    r")\b",
    re.IGNORECASE,
)


def is_derivative_title(wanted: str, got: str) -> bool:
    """True when ``got`` looks like a summary / workbook / study guide of another
    book while ``wanted`` does not — matching the two would be a false positive."""
    if not _DERIVATIVE_MARKERS.search(got or ""):
        return False
    return not _DERIVATIVE_MARKERS.search(wanted or "")


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

    # A one-word wanted title ("Dune", "Recursion", "1984") matches a
    # "<Title>: <subtitle>" candidate only when the candidate's main title is
    # exactly that word — never on a shared word alone ("Dune" vs "Dune Messiah").
    if ":" in got and len(tw) < 2 and _main(got) == tw:
        return True

    # Otherwise require a strong overlap across the full titles.
    return len(tw & tg) / len(tw | tg) >= 0.7


def _name_parts(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return [w for w in cleaned.split() if len(w) > 1]


def authors_match(wanted: str, got: str) -> bool:
    """True when author string ``got`` plausibly names ``wanted``.

    Tolerates "Last, First" ordering, initials vs. spelled-out names, punctuation,
    and extra co-authors / narrators on either side — ``got`` is often a joined
    list of every contributor Hardcover has for the edition.
    """
    w, g = _name_parts(wanted), _name_parts(got)
    if not w or not g:
        return False
    wset, gset = set(w), set(g)
    # Every word of the wanted name appears in the candidate (reordering,
    # "Last, First", extra middle names or co-authors on the candidate side).
    if wset <= gset:
        return True
    # Otherwise: at least two shared name parts, one of them an end-name (surname).
    common = wset & gset
    return len(common) >= 2 and (w[-1] in gset or g[-1] in wset)
