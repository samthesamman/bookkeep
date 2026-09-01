"""Tests for reading a Calibre library via app.services.calibre_service."""
import os
import sqlite3

import pytest

from app.services import calibre_service as cs

_SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, timestamp TEXT,
  pubdate TEXT, series_index REAL DEFAULT 1.0, author_sort TEXT, path TEXT, has_cover BOOL DEFAULT 0);
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER, series INTEGER);
CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INTEGER);
CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INTEGER, rating INTEGER);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER);
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INTEGER, publisher INTEGER);
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INTEGER, lang_code INTEGER);
CREATE TABLE comments (id INTEGER PRIMARY KEY, book INTEGER, text TEXT);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INTEGER, type TEXT, val TEXT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, uncompressed_size INTEGER, name TEXT);
"""

_DATA = """
INSERT INTO books VALUES (1,'The Hobbit','Hobbit, The','2024-01-02','1937-09-21',1.0,'Tolkien, J.R.R.','J.R.R. Tolkien/The Hobbit (1)',1);
INSERT INTO books VALUES (2,'Dune','Dune','2025-06-01','1965-08-01',1.0,'Herbert, Frank','Frank Herbert/Dune (2)',0);
INSERT INTO authors VALUES (1,'J.R.R. Tolkien','Tolkien, J.R.R.'),(2,'Frank Herbert','Herbert, Frank');
INSERT INTO books_authors_link VALUES (1,1,1),(2,2,2);
INSERT INTO series VALUES (1,'Middle-earth');
INSERT INTO books_series_link VALUES (1,1,1);
INSERT INTO ratings VALUES (1,8);
INSERT INTO books_ratings_link VALUES (1,1,1);
INSERT INTO tags VALUES (1,'Fantasy');
INSERT INTO books_tags_link VALUES (1,1,1);
INSERT INTO publishers VALUES (1,'Allen & Unwin');
INSERT INTO books_publishers_link VALUES (1,1,1);
INSERT INTO languages VALUES (1,'eng');
INSERT INTO books_languages_link VALUES (1,1,1);
INSERT INTO comments VALUES (1,1,'<p>A hobbit goes on an adventure.</p>');
INSERT INTO identifiers VALUES (1,1,'isbn','9780547928227');
INSERT INTO data VALUES (1,1,'EPUB',123456,'The Hobbit - J.R.R. Tolkien');
INSERT INTO data VALUES (2,1,'PDF',234567,'The Hobbit - J.R.R. Tolkien');
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

    book_dir = lib / "J.R.R. Tolkien" / "The Hobbit (1)"
    book_dir.mkdir(parents=True)
    (book_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0JFIF-cover")
    (book_dir / "The Hobbit - J.R.R. Tolkien.epub").write_bytes(b"PK\x03\x04fake")
    (book_dir / "The Hobbit - J.R.R. Tolkien.pdf").write_bytes(b"%PDF-1.4 fake")
    return str(lib)


def test_resolve_db_path_missing():
    with pytest.raises(cs.CalibreError):
        cs.resolve_db_path("/does/not/exist")


def test_library_stats(library):
    assert cs.library_stats(library) == {"book_count": 2}


def test_list_books_sort_and_paging(library):
    books, total = cs.list_books(library, sort="title")
    assert total == 2
    assert [b["title"] for b in books] == ["Dune", "The Hobbit"]

    page1, _ = cs.list_books(library, sort="title", page=1, page_size=1)
    page2, _ = cs.list_books(library, sort="title", page=2, page_size=1)
    assert page1[0]["title"] == "Dune"
    assert page2[0]["title"] == "The Hobbit"


def test_list_books_search_matches_title_and_author(library):
    assert [b["title"] for b in cs.list_books(library, search="dune")[0]] == ["Dune"]
    assert [b["title"] for b in cs.list_books(library, search="tolkien")[0]] == ["The Hobbit"]
    assert cs.list_books(library, search="nothing")[1] == 0


def test_get_book_detail(library):
    book = cs.get_book(library, 1)
    assert book["title"] == "The Hobbit"
    assert book["authors"] == "J.R.R. Tolkien"
    assert book["series"] == "Middle-earth"
    assert book["rating"] == 4.0  # Calibre stores 0-10
    assert book["tags"] == ["Fantasy"]
    assert book["publisher"] == "Allen & Unwin"
    assert book["languages"] == ["eng"]
    assert book["identifiers"] == {"isbn": "9780547928227"}
    assert sorted(book["formats"]) == ["EPUB", "PDF"]
    assert "adventure" in book["description"]


def test_get_book_missing(library):
    assert cs.get_book(library, 999) is None


def test_cover_file(library):
    path = cs.cover_file(library, 1)
    assert path and os.path.isfile(path)
    assert cs.cover_file(library, 2) is None  # has_cover = 0


def test_format_file_case_insensitive(library):
    for fmt in ("epub", "EPUB"):
        result = cs.format_file(library, 1, fmt)
        assert result is not None
        path, name, media_type = result
        assert os.path.isfile(path)
        assert name == "The Hobbit - J.R.R. Tolkien.epub"
        assert media_type == "application/epub+zip"


def test_format_file_unavailable(library):
    assert cs.format_file(library, 2, "epub") is None


def test_format_file_rejects_path_traversal(library):
    assert cs.format_file(library, 1, "../../../etc/passwd") is None


# --- book matching -------------------------------------------------------------

_MATCH_DATA = """
INSERT INTO books VALUES (10,'The Silmarillion','Silmarillion, The','2024','1977',1.0,'Tolkien, J.R.R.','x',0);
INSERT INTO books VALUES (20,'The Gift','Gift, The','2024','2007',1.0,'Croggon, Alison','y',0);
INSERT INTO books VALUES (21,'The Gift','Gift, The','2024','2008',1.0,'Ahern, Cecelia','z',0);
INSERT INTO authors VALUES (10,'J.R.R. Tolkien','Tolkien, J.R.R.'),(20,'Alison Croggon','Croggon, Alison'),
  (21,'Cecelia Ahern','Ahern, Cecelia');
INSERT INTO books_authors_link VALUES (10,10,10),(20,20,20),(21,21,21);
INSERT INTO identifiers VALUES (10,10,'isbn','9780618391110');
"""


@pytest.fixture
def match_library(tmp_path):
    lib = tmp_path / "Match Library"
    lib.mkdir()
    conn = sqlite3.connect(lib / "metadata.db")
    conn.executescript(_SCHEMA)
    conn.executescript(_MATCH_DATA)
    conn.commit()
    conn.close()
    return str(lib)


def test_match_exact_title_and_author(match_library):
    assert cs.find_book_match(match_library, "The Silmarillion", "J.R.R. Tolkien") == 10


def test_match_by_isbn_ignores_title(match_library):
    assert cs.find_book_match(match_library, "Totally Wrong Title", None, "978-0-618-39111-0") == 10


def test_match_same_title_different_author_uses_author_as_tiebreak(match_library):
    assert cs.find_book_match(match_library, "The Gift", "Cecelia Ahern") == 21
    assert cs.find_book_match(match_library, "The Gift", "Alison Croggon") == 20


def test_match_ignores_placeholder_author(tmp_path):
    # A Calibre book credited to "Unknown" must still match on a short title -
    # the placeholder author is treated as "no author", so it neither scores nor
    # disqualifies the match the way a real, different author would.
    lib = tmp_path / "ph"
    lib.mkdir()
    conn = sqlite3.connect(lib / "metadata.db")
    conn.executescript(_SCHEMA)
    conn.executescript(
        "INSERT INTO books VALUES (5,'Beowulf','Beowulf','2024','1000',1.0,'Unknown','p',0);"
        "INSERT INTO authors VALUES (5,'Unknown','Unknown');"
        "INSERT INTO books_authors_link VALUES (5,5,5);"
    )
    conn.commit()
    conn.close()
    assert cs.find_book_match(str(lib), "Beowulf", "Maria Dahvana Headley") == 5
