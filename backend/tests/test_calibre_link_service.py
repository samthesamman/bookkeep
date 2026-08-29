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
