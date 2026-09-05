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

# Calibre normally moves leading articles to the end in ``b.sort`` ("The Hobbit"
# -> "Hobbit, The"), but fall back to stripping them here in case a library's
# sort values were never regenerated.
_TITLE_SORT_EXPR = """
    CASE
        WHEN b.sort LIKE 'the %' THEN substr(b.sort, 5)
        WHEN b.sort LIKE 'an %'  THEN substr(b.sort, 4)
        WHEN b.sort LIKE 'a %'   THEN substr(b.sort, 3)
        ELSE b.sort
    END COLLATE NOCASE ASC
"""

_SORT_COLUMNS = {
    "title": _TITLE_SORT_EXPR,
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


def existing_book_ids(library_path: str) -> set[int]:
    """Return the set of all book ids currently in the library."""
    conn = _connect(library_path)
    try:
        return {int(r[0]) for r in conn.execute("SELECT id FROM books").fetchall()}
    finally:
        conn.close()


def formats_for_ids(library_path: str, ids: list[int]) -> dict[int, list[str]]:
    """Return {book_id: [FORMAT, ...]} for the given ids, in one query."""
    if not ids:
        return {}
    conn = _connect(library_path)
    try:
        rows = conn.execute(
            f"SELECT book, format FROM data WHERE book IN ({','.join('?' * len(ids))})",
            list(ids),
        ).fetchall()
    finally:
        conn.close()
    out: dict[int, list[str]] = {}
    for r in rows:
        if r["format"]:
            out.setdefault(int(r["book"]), []).append(r["format"].upper())
    return out


def book_identities(
    library_path: str, ids: Optional[list[int]] = None
) -> list[tuple[int, str, Optional[str], Optional[str]]]:
    """Return (book_id, title, author, isbn) for the given ids (or the whole library)."""
    conn = _connect(library_path)
    try:
        where = ""
        params: list[Any] = []
        if ids is not None:
            if not ids:
                return []
            where = f"WHERE b.id IN ({','.join('?' * len(ids))})"
            params = list(ids)
        rows = conn.execute(
            f"""
            SELECT b.id AS id, b.title AS title, b.author_sort AS author_sort,
                   (SELECT GROUP_CONCAT(a.name, ' & ')
                      FROM books_authors_link bal JOIN authors a ON a.id = bal.author
                     WHERE bal.book = b.id) AS authors,
                   (SELECT val FROM identifiers
                     WHERE book = b.id AND type IN ('isbn','isbn13','isbn10') LIMIT 1) AS isbn
              FROM books b {where}
            """,
            params,
        ).fetchall()
        return [
            (int(r["id"]), r["title"] or "", r["authors"] or r["author_sort"], r["isbn"])
            for r in rows
        ]
    finally:
        conn.close()


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


# Preferred format order when auto-picking a file to email for a request. This
# Calibre library only ever holds ebooks.
EBOOK_FORMAT_PREFERENCE = ["EPUB", "AZW3", "MOBI", "AZW", "PDF", "FB2", "DOCX", "TXT", "RTF"]


def classify_format(fmt: str) -> str:
    """Return 'ebook' for a known ebook format name, else 'other'."""
    return "ebook" if (fmt or "").upper() in EBOOK_FORMAT_PREFERENCE else "other"


# Words ignored when comparing titles.
_TITLE_STOPWORDS = {"the", "a", "an", "and", "&", "of", "to", "in"}


def _norm(text: str) -> str:
    out = (text or "").lower()
    out = out.replace("&", " and ")
    for ch in [",", ".", ":", ";", "!", "?", "'", "’", '"', "(", ")", "[", "]", "{", "}", "-", "–", "—", "_", "/"]:
        out = out.replace(ch, " ")
    return " ".join(out.split())


def _title_tokens(text: str) -> set:
    return {w for w in _norm(text).split() if w not in _TITLE_STOPWORDS}


def _titles_match(a: set, b: set) -> bool:
    """True when two title token sets are the same book.

    Exact token-set equality always matches. Otherwise one side must be fully
    contained in the other with at least two shared tokens (handles a subtitle
    on one side only, e.g. "The Final Empire" vs "Mistborn: The Final Empire"),
    or the two must have a strong Jaccard overlap. Single-word titles only match
    exactly, so "Dune" never matches "Dune Messiah".
    """
    if not a or not b:
        return False
    if a == b:
        return True
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    if len(smaller) >= 2 and smaller <= larger:
        return True
    return len(a & b) / len(a | b) >= 0.7


def _isbn_key(value: Optional[str]) -> str:
    """Normalize an ISBN to bare digits (plus a trailing X check digit)."""
    if not value:
        return ""
    return "".join(c for c in value.lower() if c.isdigit() or c == "x")


# Filler author strings that carry no identity ("Unknown", "Unknown Author",
# "Various Authors", ...) — treated as "no author" so they neither score nor
# disqualify a title match.
_PLACEHOLDER_AUTHOR_TOKENS = {
    "unknown", "anonymous", "anon", "various", "author", "authors",
    "na", "none", "unnamed", "unattributed", "uncredited",
}


def _author_tokens(text: str) -> set:
    toks = {w for w in _norm(text or "").split() if len(w) > 1}
    if toks and toks <= _PLACEHOLDER_AUTHOR_TOKENS:
        return set()
    return toks


def _load_catalog(conn) -> tuple[list, dict]:
    """Return (catalog, isbn_map) for the whole library.

    catalog: list of (book_id, title_tokens, author_tokens)
    isbn_map: {normalized_isbn: book_id}
    """
    rows = conn.execute(
        """
        SELECT b.id AS id, b.title AS title, b.author_sort AS author_sort,
               (SELECT GROUP_CONCAT(a.name, ' ')
                  FROM books_authors_link bal JOIN authors a ON a.id = bal.author
                 WHERE bal.book = b.id) AS authors
          FROM books b
        """
    ).fetchall()
    catalog = [
        (
            int(r["id"]),
            _title_tokens(r["title"] or ""),
            _author_tokens(f"{r['authors'] or ''} {r['author_sort'] or ''}"),
        )
        for r in rows
    ]

    isbn_map: dict[str, int] = {}
    for r in conn.execute(
        "SELECT book, val FROM identifiers WHERE type IN ('isbn','isbn13','isbn10')"
    ):
        key = _isbn_key(r["val"])
        if key:
            isbn_map.setdefault(key, int(r["book"]))
    return catalog, isbn_map


def _match_against_catalog(
    title: str,
    author: Optional[str],
    isbn: Optional[str],
    catalog: list,
    isbn_map: dict,
) -> Optional[int]:
    key = _isbn_key(isbn)
    if key and key in isbn_map:
        return isbn_map[key]

    want_title = _title_tokens(title or "")
    if not want_title:
        return None
    want_author = _author_tokens(author or "")

    best_id: Optional[int] = None
    best_score = -1.0
    best_cand: Optional[tuple] = None
    for cand_id, cand_title, cand_auth in catalog:
        if not _titles_match(want_title, cand_title):
            continue
        author_score = 0.0
        if want_author and cand_auth:
            author_score = len(want_author & cand_auth) / len(want_author)
            # A title-only match with a clearly different author is not it.
            if author_score == 0 and len(want_title) < 3:
                continue
        score = len(want_title & cand_title) / max(len(want_title | cand_title), 1) + author_score
        if score > best_score:
            best_score = score
            best_id = cand_id
            best_cand = (cand_title, cand_auth)

    if best_id is not None:
        cand_title, cand_auth = best_cand
        logger.info(
            "calibre_fuzzy_match_found",
            query_title=title,
            query_author=author,
            matched_calibre_id=best_id,
            matched_title_tokens=sorted(cand_title),
            matched_author_tokens=sorted(cand_auth),
            score=round(best_score, 3),
            author_overlap=bool(want_author and cand_auth and (want_author & cand_auth)),
        )
    else:
        logger.info(
            "calibre_fuzzy_match_not_found",
            query_title=title,
            query_author=author,
            query_title_tokens=sorted(want_title),
        )
    return best_id


def match_books(
    library_path: str,
    items: list[tuple[str, Optional[str], Optional[str]]],
) -> list[Optional[int]]:
    """Match many (title, author, isbn) tuples against the library in one pass.

    Returns a list of Calibre book ids (or None), aligned with ``items``.
    """
    if not items:
        return []
    conn = _connect(library_path)
    try:
        catalog, isbn_map = _load_catalog(conn)
    finally:
        conn.close()
    return [_match_against_catalog(t, a, i, catalog, isbn_map) for (t, a, i) in items]


def find_book_match(
    library_path: str,
    title: str,
    author: Optional[str] = None,
    isbn: Optional[str] = None,
) -> Optional[int]:
    """Return the Calibre book id that best matches the given metadata, or None.

    Matches on ISBN identifier first, then on normalized title tokens with an
    author-name tie-breaker.
    """
    return match_books(library_path, [(title, author, isbn)])[0]


def pick_format(library_path: str, book_id: int) -> Optional[str]:
    """Return the best available ebook format name for a book, or None."""
    conn = _connect(library_path)
    try:
        rows = conn.execute(
            "SELECT format FROM data WHERE book = ?", (book_id,)
        ).fetchall()
    finally:
        conn.close()
    available = {r["format"].upper() for r in rows if r["format"]}
    for fmt in EBOOK_FORMAT_PREFERENCE:
        if fmt in available:
            return fmt
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
