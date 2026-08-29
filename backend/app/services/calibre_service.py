"""Read a local Calibre library by parsing its metadata.db (SQLite) directly.

No Calibre server is required. The database is opened read-only and immutable so
this never contends with a running Calibre instance.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Optional

import structlog

logger = structlog.get_logger()

# Calibre stores format names uppercase (EPUB, MOBI, ...). Map the common ones.
_MEDIA_TYPES = {
    "epub": "application/epub+zip",
    "mobi": "application/x-mobipocket-ebook",
    "azw": "application/vnd.amazon.ebook",
    "azw3": "application/vnd.amazon.ebook",
    "pdf": "application/pdf",
    "cbz": "application/vnd.comicbook+zip",
    "cbr": "application/vnd.comicbook-rar",
    "txt": "text/plain",
    "rtf": "application/rtf",
    "djvu": "image/vnd.djvu",
    "fb2": "application/x-fictionbook+xml",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_SORT_COLUMNS = {
    "title": "b.sort COLLATE NOCASE ASC",
    "author": "b.author_sort COLLATE NOCASE ASC",
    "added": "b.timestamp DESC",
    "pubdate": "b.pubdate DESC",
}


class CalibreError(Exception):
    """Raised when the configured Calibre library cannot be read."""


def resolve_db_path(library_path: str) -> str:
    """Return the path to metadata.db inside library_path, or raise CalibreError."""
    if not library_path:
        raise CalibreError("No Calibre library path configured")
    db_path = os.path.join(library_path, "metadata.db")
    if not os.path.isfile(db_path):
        raise CalibreError(f"metadata.db not found in {library_path}")
    return db_path


def _connect(library_path: str) -> sqlite3.Connection:
    db_path = resolve_db_path(library_path)
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def library_stats(library_path: str) -> dict[str, Any]:
    """Lightweight probe used by the Settings "Test connection" button."""
    conn = _connect(library_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        return {"book_count": int(count)}
    finally:
        conn.close()


_BASE_SELECT = """
    SELECT
        b.id                                              AS id,
        b.title                                           AS title,
        b.sort                                            AS title_sort,
        b.author_sort                                     AS author_sort,
        b.path                                            AS path,
        b.has_cover                                       AS has_cover,
        b.pubdate                                         AS pubdate,
        b.timestamp                                       AS timestamp,
        b.series_index                                    AS series_index,
        (SELECT GROUP_CONCAT(a.name, ' & ')
           FROM books_authors_link bal JOIN authors a ON a.id = bal.author
          WHERE bal.book = b.id)                          AS authors,
        (SELECT s.name
           FROM books_series_link bsl JOIN series s ON s.id = bsl.series
          WHERE bsl.book = b.id LIMIT 1)                  AS series,
        (SELECT r.rating
           FROM books_ratings_link brl JOIN ratings r ON r.id = brl.rating
          WHERE brl.book = b.id LIMIT 1)                  AS rating,
        (SELECT GROUP_CONCAT(d.format, ',')
           FROM data d WHERE d.book = b.id)               AS formats
    FROM books b
"""


def _row_to_book(row: sqlite3.Row) -> dict[str, Any]:
    raw_rating = row["rating"]
    formats = [f for f in (row["formats"] or "").split(",") if f]
    return {
        "id": row["id"],
        "title": row["title"],
        "authors": row["authors"] or row["author_sort"] or "Unknown",
        "series": row["series"],
        "series_index": row["series_index"],
        "rating": (raw_rating / 2) if raw_rating else None,  # Calibre stores 0-10
        "pubdate": row["pubdate"],
        "added": row["timestamp"],
        "has_cover": bool(row["has_cover"]),
        "formats": formats,
    }


def list_books(
    library_path: str,
    search: Optional[str] = None,
    sort: str = "added",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Return (books, total) for a page of the library."""
    order_by = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["added"])
    page = max(page, 1)
    page_size = max(1, min(page_size, 200))
    offset = (page - 1) * page_size

    where = ""
    params: list[Any] = []
    if search and search.strip():
        term = f"%{search.strip()}%"
        where = """
            WHERE b.id IN (
                SELECT b2.id FROM books b2
                  LEFT JOIN books_authors_link bal2 ON bal2.book = b2.id
                  LEFT JOIN authors a2 ON a2.id = bal2.author
                 WHERE b2.title LIKE ? COLLATE NOCASE
                    OR a2.name LIKE ? COLLATE NOCASE
            )
        """
        params.extend([term, term])

    conn = _connect(library_path)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM books b {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"{_BASE_SELECT} {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
        return [_row_to_book(r) for r in rows], int(total)
    finally:
        conn.close()


def get_book(library_path: str, book_id: int) -> Optional[dict[str, Any]]:
    conn = _connect(library_path)
    try:
        row = conn.execute(f"{_BASE_SELECT} WHERE b.id = ?", (book_id,)).fetchone()
        if row is None:
            return None
        book = _row_to_book(row)

        comment = conn.execute(
            "SELECT text FROM comments WHERE book = ?", (book_id,)
        ).fetchone()
        book["description"] = comment["text"] if comment else None

        book["tags"] = [
            r["name"]
            for r in conn.execute(
                "SELECT t.name FROM books_tags_link l JOIN tags t ON t.id = l.tag WHERE l.book = ? ORDER BY t.name",
                (book_id,),
            ).fetchall()
        ]

        publisher = conn.execute(
            "SELECT p.name FROM books_publishers_link l JOIN publishers p ON p.id = l.publisher WHERE l.book = ? LIMIT 1",
            (book_id,),
        ).fetchone()
        book["publisher"] = publisher["name"] if publisher else None

        book["languages"] = [
            r["lang_code"]
            for r in conn.execute(
                "SELECT lc.lang_code FROM books_languages_link l JOIN languages lc ON lc.id = l.lang_code WHERE l.book = ?",
                (book_id,),
            ).fetchall()
        ]

        book["identifiers"] = {
            r["type"]: r["val"]
            for r in conn.execute(
                "SELECT type, val FROM identifiers WHERE book = ?", (book_id,)
            ).fetchall()
        }

        book["format_details"] = [
            {"format": r["format"], "size": r["uncompressed_size"], "name": r["name"]}
            for r in conn.execute(
                "SELECT format, uncompressed_size, name FROM data WHERE book = ? ORDER BY format",
                (book_id,),
            ).fetchall()
        ]
        return book
    finally:
        conn.close()


def _book_dir(library_path: str, book_id: int) -> Optional[str]:
    conn = _connect(library_path)
    try:
        row = conn.execute("SELECT path FROM books WHERE id = ?", (book_id,)).fetchone()
    finally:
        conn.close()
    if row is None or not row["path"]:
        return None
    return os.path.join(library_path, row["path"])


def _within(base: str, target: str) -> bool:
    base_real = os.path.realpath(base)
    target_real = os.path.realpath(target)
    return target_real == base_real or target_real.startswith(base_real + os.sep)


def cover_file(library_path: str, book_id: int) -> Optional[str]:
    book_dir = _book_dir(library_path, book_id)
    if not book_dir:
        return None
    path = os.path.join(book_dir, "cover.jpg")
    if _within(library_path, path) and os.path.isfile(path):
        return path
    return None


def format_file(
    library_path: str, book_id: int, fmt: str
) -> Optional[tuple[str, str, str]]:
    """Return (absolute_path, download_filename, media_type) for a book format."""
    fmt_upper = (fmt or "").upper()
    conn = _connect(library_path)
    try:
        row = conn.execute(
            "SELECT name FROM data WHERE book = ? AND format = ? LIMIT 1",
            (book_id, fmt_upper),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None

    book_dir = _book_dir(library_path, book_id)
    if not book_dir:
        return None

    ext = fmt_upper.lower()
    path = os.path.join(book_dir, f"{row['name']}.{ext}")
    if not _within(library_path, path) or not os.path.isfile(path):
        return None

    media_type = _MEDIA_TYPES.get(ext, "application/octet-stream")
    download_name = f"{row['name']}.{ext}"
    return path, download_name, media_type
