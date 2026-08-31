"""Tests for app.services.text_match.titles_match."""
import pytest

from app.services.text_match import is_derivative_title, titles_match

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
        # real book, not a companion work
        ("Atomic Habits", "Atomic Habits: An Easy & Proven Way to Build Good Habits", False),
        ("Sapiens", "Sapiens: A Brief History of Humankind", False),
        # the caller genuinely wants the summary/workbook
        ("Summary of Atomic Habits", "Summary of Atomic Habits", False),
        ("The Anxiety and Phobia Workbook", "The Anxiety and Phobia Workbook", False),
    ],
)
def test_is_derivative_title(wanted, got, expected):
    assert is_derivative_title(wanted, got) is expected
