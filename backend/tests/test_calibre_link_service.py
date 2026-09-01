"""Tests for app.services.calibre_link_service (Calibre <-> Book linking)."""
import sqlite3

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine, Base
from app.models import Book, CalibreBookLink
from app.services import calibre_link_service as cls

_SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, timestamp TEXT,
  pubdate TEXT, series_index REAL DEFAULT 1.0, author_sort TEXT, path TEXT, has_cover BOOL DEFAULT 0);
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INTEGER, type TEXT, val TEXT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, uncompressed_size INTEGER, name TEXT);
"""

_DATA = """
INSERT INTO books VALUES (10,'The Hobbit','Hobbit, The','2024-01-02','1937',1.0,'Tolkien, J.R.R.','x',0);
INSERT INTO books VALUES (20,'Dune','Dune','2025-06-01','1965',1.0,'Herbert, Frank','y',0);
INSERT INTO authors VALUES (1,'J.R.R. Tolkien','Tolkien'),(2,'Frank Herbert','Herbert');
INSERT INTO books_authors_link VALUES (1,10,1),(2,20,2);
INSERT INTO identifiers VALUES (1,20,'isbn','9780441013593');
INSERT INTO data VALUES (1,10,'EPUB',1,'hobbit');
INSERT INTO data VALUES (2,20,'EPUB',1,'dune');
INSERT INTO data VALUES (3,20,'MP3',1,'dune-audio');
"""


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "Calibre Library"
    lib.mkdir()
    conn = sqlite3.connect(lib / "metadata.db")
    conn.executescript(_SCHEMA)
    conn.executescript(_DATA)
    conn.commit()
    conn.close()
    return str(lib)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _book(db, **kw):
    b = Book(title=kw.pop("title", "T"), author=kw.pop("author", "A"), **kw)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def test_upsert_creates_and_strength_ordering(db):
    b1 = _book(db, title="Dune", author="Frank Herbert")
    b2 = _book(db, title="Dune (other)", author="Frank Herbert")

    link = cls.upsert_link(db, calibre_book_id=20, book_id=b1.id, source="fuzzy")
    assert link.book_id == b1.id and link.confirmed is False

    # A weaker/equal link must not steal the Calibre id.
    assert cls.upsert_link(db, calibre_book_id=20, book_id=b2.id, source="fuzzy") is None
    assert db.query(CalibreBookLink).filter_by(calibre_book_id=20).one().book_id == b1.id

    # A stronger link wins.
    link = cls.upsert_link(
        db, calibre_book_id=20, book_id=b2.id, source="download", confirmed=True
    )
    assert link.book_id == b2.id and link.confirmed is True


def test_upsert_is_one_to_one_on_book(db):
    b = _book(db, title="Dune")
    cls.upsert_link(db, calibre_book_id=20, book_id=b.id, source="manual", confirmed=True)
    cls.upsert_link(db, calibre_book_id=99, book_id=b.id, source="manual", confirmed=True)
    links = db.query(CalibreBookLink).filter_by(book_id=b.id).all()
    assert len(links) == 1 and links[0].calibre_book_id == 99


def test_backfill_fuzzy_links_matches_by_isbn_and_title(db, library):
    _book(db, title="Dune", author="Frank Herbert", isbn="9780441013593")
    _book(db, title="The Hobbit", author="J.R.R. Tolkien")
    _book(db, title="Nonexistent Book", author="Nobody")

    created = cls.backfill_fuzzy_links(db, library)
    assert created == 2
    linked = {l.calibre_book_id for l in db.query(CalibreBookLink).all()}
    assert linked == {10, 20}


def test_backfill_skips_already_linked_books(db, library):
    b = _book(db, title="Dune", author="Frank Herbert", isbn="9780441013593")
    cls.upsert_link(db, calibre_book_id=20, book_id=b.id, source="download", confirmed=True)
    assert cls.backfill_fuzzy_links(db, library) == 0


def test_heal_removes_stale_link(db, library):
    b = _book(db, title="Gone", author="X")
    cls.upsert_link(
        db, calibre_book_id=555, book_id=b.id, source="fuzzy", calibre_title="Gone"
    )
    healed = cls.heal_stale_links(db, library)
    assert healed == 1
    assert db.query(CalibreBookLink).count() == 0


def test_heal_repoints_by_isbn(db, library):
    b = _book(db, title="Dune", author="Frank Herbert", isbn="9780441013593")
    cls.upsert_link(
        db,
        calibre_book_id=999,
        book_id=b.id,
        source="manual",
        confirmed=True,
        calibre_isbn="9780441013593",
        calibre_title="Dune",
    )
    cls.heal_stale_links(db, library)
    assert db.query(CalibreBookLink).one().calibre_book_id == 20


def test_sync_availability_flags(db, library):
    b1 = _book(db, title="The Hobbit", author="Tolkien")
    b2 = _book(db, title="Dune", author="Frank Herbert")
    cls.upsert_link(db, calibre_book_id=10, book_id=b1.id, source="fuzzy")
    cls.upsert_link(db, calibre_book_id=20, book_id=b2.id, source="fuzzy")

    changed = cls.sync_availability_flags(db, library)
    assert changed == 3  # b1 ebook; b2 ebook + audiobook
    db.refresh(b1)
    db.refresh(b2)
    assert b1.ebook_available and not b1.audiobook_available
    assert b2.ebook_available and b2.audiobook_available
    # Idempotent.
    assert cls.sync_availability_flags(db, library) == 0


def test_find_library_book_id_prefers_fuzzy_then_link(db, library):
    # Fuzzy match works on its own — no link needed.
    b1 = _book(db, title="The Hobbit", author="J.R.R. Tolkien")
    assert cls.find_library_book_id(db, library, b1, "ebook") == 10

    # Metadata drifted so fuzzy misses, but a persisted link carries it.
    b2 = _book(db, title="Dune: Deluxe Anniversary Edition", author="F. Herbert (ed.)")
    assert cls.find_library_book_id(db, library, b2, "ebook") is None
    cls.upsert_link(db, calibre_book_id=20, book_id=b2.id, source="download", confirmed=True)
    assert cls.find_library_book_id(db, library, b2, "ebook") == 20


def test_linked_library_book_id_validates_format_and_existence(db, library):
    b = _book(db, title="Whatever", author="Someone")
    # Calibre book 10 has only an EPUB (no audio).
    cls.upsert_link(db, calibre_book_id=10, book_id=b.id, source="manual", confirmed=True)
    assert cls.linked_library_book_id(db, library, b.id, "ebook") == 10
    assert cls.linked_library_book_id(db, library, b.id, "audiobook") is None
    assert cls.linked_library_book_id(db, library, b.id, "") == 10

    # Link to a Calibre id that no longer exists.
    b2 = _book(db, title="Gone", author="X")
    cls.upsert_link(db, calibre_book_id=4242, book_id=b2.id, source="manual", confirmed=True)
    assert cls.linked_library_book_id(db, library, b2.id, "") is None

    # No link at all.
    b3 = _book(db, title="Unlinked", author="Y")
    assert cls.linked_library_book_id(db, library, b3.id, "ebook") is None


def test_books_missing_metadata_selects_unrefreshed_and_stale(db):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    fresh = _book(db, title="Fresh", description="d", cover_url="c", genres="g")
    fresh.last_refreshed = now
    never = _book(db, title="Never")
    stale_gap = _book(db, title="StaleGap", cover_url="c", genres="g")  # no description
    stale_gap.last_refreshed = now - timedelta(days=30)
    stale_ok = _book(db, title="StaleComplete", description="d", cover_url="c", genres="g")
    stale_ok.last_refreshed = now - timedelta(days=30)
    db.commit()

    for i, b in enumerate((fresh, never, stale_gap, stale_ok), start=1):
        cls.upsert_link(db, calibre_book_id=i, book_id=b.id, source="fuzzy")

    got = {l.book.title for l in cls.books_missing_metadata(db)}
    assert got == {"Never", "StaleGap"}

    # Never-refreshed first, and the limit is honored.
    limited = cls.books_missing_metadata(db, limit=1)
    assert len(limited) == 1 and limited[0].book.title == "Never"

    # Unlinked books are never returned.
    lonely = _book(db, title="Unlinked")
    db.commit()
    assert "Unlinked" not in {l.book.title for l in cls.books_missing_metadata(db)}


def test_overlay_book_dict_fills_and_prefers(db):
    b = _book(
        db,
        title="Dune",
        description="hc desc",
        rating=4.2,
        series="Dune",
        series_position=1.0,
        genres="Sci-Fi, Classic",
        page_count=412,
        cover_url="http://img/x.jpg",
    )
    link = cls.upsert_link(db, calibre_book_id=20, book_id=b.id, source="download", confirmed=True)

    base = {"id": 20, "title": "Dune", "rating": None, "series": None,
            "series_index": None, "pubdate": None, "description": "calibre desc"}
    out = cls.overlay_book_dict(base, link, prefer_local=False)
    assert out["metadata_source"] == "overlay"
    assert out["rating"] == 4.2          # filled (calibre empty)
    assert out["description"] == "calibre desc"  # calibre wins when present
    assert out["genres"] == ["Sci-Fi", "Classic"]
    assert out["hardcover_id"] is None

    out2 = cls.overlay_book_dict(base, link, prefer_local=True)
    assert out2["description"] == "hc desc"

    out3 = cls.overlay_book_dict(base, None)
    assert out3["metadata_source"] == "calibre" and out3["linked_book_id"] is None


def test_overlay_title_author_only_win_when_locked(db):
    b = _book(db, title="Corrected Title", author="Real Author")
    link = cls.upsert_link(db, calibre_book_id=20, book_id=b.id, source="manual", confirmed=True)
    base = {"id": 20, "title": "Wrong Calibre Title", "authors": "Calibre Author"}

    # Not locked, no resolved identity → Calibre's file identity wins.
    out = cls.overlay_book_dict(base, link)
    assert out["title"] == "Wrong Calibre Title"
    assert out["authors"] == "Calibre Author"

    # Locked (curated via the source picker) → the Book row wins.
    b.metadata_locked = True
    db.commit()
    out = cls.overlay_book_dict(base, link)
    assert out["title"] == "Corrected Title"
    assert out["authors"] == "Real Author"


def test_overlay_prefers_book_identity_once_hardcover_resolved(db):
    # A Book row that resolved to a Hardcover record has a vetted title/author,
    # so it wins over Calibre's (here swapped) file metadata without needing lock.
    b = _book(db, title="The Way of Kings", author="Brandon Sanderson")
    b.hardcover_id = 12345
    db.commit()
    link = cls.upsert_link(db, calibre_book_id=20, book_id=b.id, source="fuzzy")
    base = {"id": 20, "title": "Brandon Sanderson", "authors": "The Way of Kings"}

    out = cls.overlay_book_dict(base, link)
    assert out["title"] == "The Way of Kings"
    assert out["authors"] == "Brandon Sanderson"
