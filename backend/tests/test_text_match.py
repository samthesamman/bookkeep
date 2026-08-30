"""Tests for app.services.text_match.titles_match."""
import pytest

from app.services.text_match import titles_match

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
