"""Tests for the Audiobookshelf-style audiobook import layout.

Covers app.downloads.orchestrator.DownloadOrchestrator._import_audiobook:
files land at {media_path}/{Book Title}/{Author} - {Book Title}{ (NN)}{ext},
only audio files are carried over, and a single-file book gets no part suffix.
"""
import pytest
from datetime import datetime, timezone

from app.database import SessionLocal, engine, Base
from app.models import Book, DownloadTask
from app.downloads.orchestrator import DownloadOrchestrator, _sanitize_path_component


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _make_book_and_task(db, tmp_path, *, title="The Great Book", author="Jane Doe"):
    book = Book(title=title, author=author)
    db.add(book)
    db.commit()
    db.refresh(book)

    task = DownloadTask(
        book_id=book.id,
        format="audiobook",
        source="prowlarr",
        state="complete",
        download_url="magnet:?xt=urn:btih:deadbeef",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return book, task


def test_multi_file_audiobook_layout(db_session, tmp_path):
    book, task = _make_book_and_task(db_session, tmp_path)

    src = tmp_path / "release"
    src.mkdir()
    for name in ["part2.mp3", "part10.mp3", "part1.mp3", "cover.jpg", "info.nfo"]:
        (src / name).write_bytes(b"x")

    media = tmp_path / "media"
    media.mkdir()

    orch = DownloadOrchestrator(db_session=db_session)
    result = orch._import_audiobook(task, src, str(media), db_session, use_hardlinks=True)

    book_dir = media / "The Great Book"
    assert result == str(book_dir)
    got = sorted(p.name for p in book_dir.iterdir())
    # 3 audio tracks, numbered in filename order (part1, part2, part10), zero-padded to 2.
    assert got == [
        "Jane Doe - The Great Book (01).mp3",
        "Jane Doe - The Great Book (02).mp3",
        "Jane Doe - The Great Book (03).mp3",
    ]
    # Non-audio files are not carried over.
    assert not (book_dir / "cover.jpg").exists()

    db_session.refresh(task)
    assert task.import_status == "imported"
    assert task.imported_at is not None


def test_single_file_audiobook_has_no_part_suffix(db_session, tmp_path):
    book, task = _make_book_and_task(db_session, tmp_path, title="Solo", author="A. Writer")

    src = tmp_path / "Solo.m4b"
    src.write_bytes(b"x")
    media = tmp_path / "media"
    media.mkdir()

    orch = DownloadOrchestrator(db_session=db_session)
    result = orch._import_audiobook(task, src, str(media), db_session, use_hardlinks=False)

    assert result == str(media / "Solo")
    assert (media / "Solo" / "A. Writer - Solo.m4b").exists()


def test_no_audio_files_marks_failed(db_session, tmp_path):
    book, task = _make_book_and_task(db_session, tmp_path)

    src = tmp_path / "release"
    src.mkdir()
    (src / "readme.txt").write_bytes(b"x")
    media = tmp_path / "media"
    media.mkdir()

    orch = DownloadOrchestrator(db_session=db_session)
    result = orch._import_audiobook(task, src, str(media), db_session, use_hardlinks=True)

    assert result is None
    db_session.refresh(task)
    assert task.import_status == "failed"


def test_hardlink_shares_inode(db_session, tmp_path):
    book, task = _make_book_and_task(db_session, tmp_path)
    src = tmp_path / "book.mp3"
    src.write_bytes(b"content")
    media = tmp_path / "media"
    media.mkdir()

    orch = DownloadOrchestrator(db_session=db_session)
    orch._import_audiobook(task, src, str(media), db_session, use_hardlinks=True)

    linked = media / "The Great Book" / "Jane Doe - The Great Book.mp3"
    assert linked.stat().st_ino == src.stat().st_ino


@pytest.mark.parametrize("raw,expected", [
    ("Normal Title", "Normal Title"),
    ("Bad/Name: Here?", "BadName Here"),
    ("   spaced   out  ", "spaced out"),
    ("", "Unknown"),
    ("trailing dots...", "trailing dots"),
])
def test_sanitize_path_component(raw, expected):
    assert _sanitize_path_component(raw) == expected
