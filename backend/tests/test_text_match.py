"""Tests for app.services.text_match."""
import pytest

from app.services.text_match import authors_match, is_derivative_title, titles_match

_NO_BAD_PARTS = (
    "No Bad Parts: Healing Trauma and Restoring Wholeness "
    "with the Internal Family Systems Model"
)


@pytest.mark.parametrize(
    "wanted, got, expected",
    [
        # exact / trivial
        ("Dune", "Dune", True),
        ("Dune", "Dune Messiah", False),  # single-word titles only match exactly
        ("The Hobbit", "A Wizard of Earthsea", False),
        # subtitle on one side only
        ("The End of Faith: Religion, Terror, and the Future of Reason", "The End of Faith", True),
        ("Atomic Habits", "Atomic Habits: An Easy & Proven Way to Build Good Habits", True),
        # series prefix
        ("The Final Empire", "Mistborn: The Final Empire", True),
        # one-word wanted title vs. "<Title>: <subtitle>" candidate
        ("Recursion", "Recursion: A Novel", True),
        ("1984", "1984: Nineteen Eighty-Four", True),
        ("Dune", "Dune: A Novel", True),
        # ...but not on a shared word alone
        ("Dune", "Dune and Other Stories", False),
        # audiobook title carrying series / "Book N" junk
        ("Words of Radiance: Book Two of the Stormlight Archive", "Words of Radiance", True),
        # long "Main: subtitle" wanted title
        (_NO_BAD_PARTS, "No Bad Parts", True),
        (_NO_BAD_PARTS, _NO_BAD_PARTS, True),
        (_NO_BAD_PARTS, "Healing Trauma", False),  # only subtitle words
        (_NO_BAD_PARTS, "Restoring Wholeness", False),
        (_NO_BAD_PARTS, "Bad Blood", False),
        # near-misses
        ("The End of Faith", "The End of Days", False),
        ("The End of Faith", "The Faith Instinct", False),
    ],
)
def test_titles_match(wanted, got, expected):
    assert titles_match(wanted, got) is expected


@pytest.mark.parametrize(
    "wanted, got, expected",
    [
        ("Atomic Habits", "Summary of Atomic Habits", True),
        ("Atomic Habits", "Summary: Atomic Habits by James Clear", True),
        ("Thinking, Fast and Slow", "Workbook For Thinking, Fast and Slow", True),
        ("The Body Keeps the Score", "Summary & Analysis of The Body Keeps the Score", True),
        ("Sapiens", "Blinkist Summary of Sapiens", True),
        ("The Great Gatsby", "Study Guide: The Great Gatsby", True),
        ("The Great Gatsby", "The Great Gatsby (SparkNotes Literature Guide)", True),
        ("The Great Gatsby", "GradeSaver(tm) ClassicNotes The Great Gatsby", True),
        ("The Great Gatsby", "LitCharts: The Great Gatsby", True),
        ("The Great Gatsby", "The Great Gatsby - Reading Guide", True),
        ("The Great Gatsby", "The Great Gatsby: Study Notes", True),
        ("The Great Gatsby", "The Great Gatsby (Cliffs Notes)", True),
        # real book, not a companion work
        ("Atomic Habits", "Atomic Habits: An Easy & Proven Way to Build Good Habits", False),
        ("Sapiens", "Sapiens: A Brief History of Humankind", False),
        ("A Field Guide to the Birds", "A Field Guide to the Birds of Eastern North America", False),
        ("The Hitchhiker's Guide to the Galaxy", "The Hitchhiker's Guide to the Galaxy", False),
        ("Notes from Underground", "Notes from Underground", False),
        # the caller genuinely wants the summary/workbook/guide
        ("Summary of Atomic Habits", "Summary of Atomic Habits", False),
        ("The Anxiety and Phobia Workbook", "The Anxiety and Phobia Workbook", False),
        ("A Study Guide for Fitzgerald's Gatsby", "A Study Guide for Fitzgerald's Gatsby", False),
    ],
)
def test_is_derivative_title(wanted, got, expected):
    assert is_derivative_title(wanted, got) is expected


@pytest.mark.parametrize(
    "wanted, got, expected",
    [
        ("Andy Weir", "Andy Weir", True),
        ("J.R.R. Tolkien", "J. R. R. Tolkien", True),  # initials vs. spaced
        ("James S. A. Corey", "James S.A. Corey", True),
        ("Brandon Sanderson", "Robert Jordan, Brandon Sanderson", True),  # co-authors
        ("Stephen King", "Stephen King, Owen King", True),
        ("Neil Gaiman, Terry Pratchett", "Neil Gaiman", True),  # narrower candidate
        ("Duran, Gil", "Gil Duran", True),  # "Last, First"
        # mismatches — caller falls back to a title-only match instead
        ("Andy Weir", "", False),
        ("Samuel Clemens", "Mark Twain", False),
        ("J.K. Rowling", "Jack London", False),
        ("Brandon Sanderson", "Brandon Mull", False),
    ],
)
def test_authors_match(wanted, got, expected):
    assert authors_match(wanted, got) is expected
