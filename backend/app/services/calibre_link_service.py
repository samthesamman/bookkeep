"""Persist and maintain links between Calibre library books and ``Book`` rows.

Calibre is read-only (see ``calibre_service``); these links are how bookkeep
knows which local ``Book`` (and its Hardcover metadata) belongs to a given
Calibre book so the "My Books" view can be enriched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import structlog
from sqlalchemy.orm import Session, joinedload

from app.models import Book, CalibreBookLink
from app.services import calibre_service

logger = structlog.get_logger()

# Ordering used when more than one Book wants the same Calibre id.
_SOURCE_RANK = {"download": 3, "manual": 2, "fuzzy": 1}


def get_links_for_calibre_ids(
    db: Session, calibre_ids: Iterable[int]
) -> dict[int, CalibreBookLink]:
    ids = [int(i) for i in calibre_ids if i is not None]
    if not ids:
        return {}
    rows = (
        db.query(CalibreBookLink)
        .filter(CalibreBookLink.calibre_book_id.in_(ids))
        .all()
    )
    return {r.calibre_book_id: r for r in rows}


def get_link_for_book(db: Session, book_id: int) -> Optional[CalibreBookLink]:
    return (
        db.query(CalibreBookLink)
        .filter(CalibreBookLink.book_id == book_id)
        .first()
    )


def linked_library_book_id(
    db: Session, library_path: str, book_id: int
) -> Optional[int]:
    """Calibre book id for a ``Book`` via its persisted link, or None.

    Validates the link before trusting it: the Calibre id must still resolve and
    still carry an ebook format. Used as a fallback when live fuzzy matching
    misses a book whose metadata drifted after it was linked.
    """
    link = get_link_for_book(db, book_id)
    if link is None:
        return None
    try:
        formats = calibre_service.formats_for_ids(
            library_path, [link.calibre_book_id]
        ).get(link.calibre_book_id, [])
    except calibre_service.CalibreError as exc:
        logger.warning(
            "calibre_link_format_probe_failed", book_id=book_id, error=str(exc)
        )
        return None
    if not any(calibre_service.classify_format(f) == "ebook" for f in formats):
        return None
    return link.calibre_book_id


def find_library_book_id(
    db: Session, library_path: str, book: Book
) -> Optional[int]:
    """Calibre book id for a catalog ``Book``, or None if it isn't in the library.

    Tries a live fuzzy title/author/ISBN match first, then falls back to a
    validated persisted link (see ``linked_library_book_id``).
    """
    try:
        match_id = calibre_service.find_book_match(
            library_path, book.title, book.author, book.isbn
        )
    except calibre_service.CalibreError as exc:
        logger.warning(
            "calibre_link_fuzzy_probe_failed", book_id=book.id, error=str(exc)
        )
        match_id = None
    if match_id is not None:
        return match_id
    return linked_library_book_id(db, library_path, book.id)


def _rank(source: str, confirmed: bool) -> tuple[int, int]:
    return (1 if confirmed else 0, _SOURCE_RANK.get(source, 0))


def upsert_link(
    db: Session,
    *,
    calibre_book_id: int,
    book_id: int,
    source: str,
    confidence: Optional[float] = None,
    confirmed: bool = False,
    calibre_isbn: Optional[str] = None,
    calibre_title: Optional[str] = None,
    commit: bool = True,
) -> Optional[CalibreBookLink]:
    """Create or update the link for ``calibre_book_id``.

    A weaker link never displaces a stronger existing one (a confirmed download
    link is not overwritten by a fuzzy guess). Returns the live link, or None
    if an existing stronger link was left in place.
    """
    existing = (
        db.query(CalibreBookLink)
        .filter(CalibreBookLink.calibre_book_id == calibre_book_id)
        .first()
    )
    if existing is not None:
        same_book = existing.book_id == book_id
        if not same_book and _rank(source, confirmed) <= _rank(
            existing.source, existing.confirmed
        ):
            return None
        existing.book_id = book_id
        existing.source = source
        existing.confidence = confidence
        existing.confirmed = confirmed or existing.confirmed and same_book
        if calibre_isbn is not None:
            existing.calibre_isbn = calibre_isbn
        if calibre_title is not None:
            existing.calibre_title = calibre_title
        link = existing
    else:
        # Clear any other link pointing at this same Book (1:1 both ways).
        db.query(CalibreBookLink).filter(
            CalibreBookLink.book_id == book_id,
            CalibreBookLink.calibre_book_id != calibre_book_id,
        ).delete(synchronize_session=False)
        link = CalibreBookLink(
            calibre_book_id=calibre_book_id,
            book_id=book_id,
            source=source,
            confidence=confidence,
            confirmed=confirmed,
            calibre_isbn=calibre_isbn,
            calibre_title=calibre_title,
        )
        db.add(link)

    if commit:
        db.commit()
        db.refresh(link)
    return link


def delete_link_for_calibre_id(db: Session, calibre_book_id: int, commit: bool = True) -> bool:
    deleted = (
        db.query(CalibreBookLink)
        .filter(CalibreBookLink.calibre_book_id == calibre_book_id)
        .delete(synchronize_session=False)
    )
    if commit:
        db.commit()
    return bool(deleted)


def _pick(local, calibre_val, prefer_local: bool):
    """Choose between a local (overlay) value and Calibre's own value."""
    if prefer_local:
        return local if local not in (None, "", []) else calibre_val
    return calibre_val if calibre_val not in (None, "", []) else local


def overlay_book_dict(
    book_dict: dict, link: Optional[CalibreBookLink], *, prefer_local: bool = False
) -> dict:
    """Return ``book_dict`` merged with metadata from the linked ``Book``.

    Calibre stays authoritative for files (``formats``/``format_details``) and
    identity; the overlay fills descriptive fields. Always adds link-status keys
    so the UI can show where the metadata came from.
    """
    out = dict(book_dict)
    out["linked_book_id"] = link.book_id if link else None
    out["link_source"] = link.source if link else None
    out["link_confirmed"] = bool(link.confirmed) if link else False
    out["hardcover_id"] = None
    out["metadata_source"] = "calibre"

    book = link.book if link else None
    if book is None:
        return out

    out["hardcover_id"] = book.hardcover_id
    out["metadata_source"] = "overlay"

    # A locked row (an admin picked a metadata source for it) is authoritative
    # for every descriptive field — Calibre keeps only the files. Otherwise the
    # Book row just fills gaps in Calibre's own metadata.
    locked = bool(getattr(book, "metadata_locked", False))
    out["metadata_locked"] = locked
    authoritative = prefer_local or locked

    def field(local, calibre_val):
        if authoritative:
            return local
        return _pick(local, calibre_val, False)

    # Identity (title/author): once a Book row has resolved to a Hardcover record
    # its title/author are vetted, so trust them over Calibre's embedded file
    # metadata (frequently wrong, and sometimes title/author are swapped). A
    # "Calibre only" book (no hardcover_id) still shows Calibre's own identity.
    identity_local = authoritative or bool(getattr(book, "hardcover_id", None))

    def identity(local, calibre_val):
        if identity_local:
            return local or calibre_val
        return _pick(local, calibre_val, False)

    out["title"] = identity(book.title, out.get("title")) or out.get("title")
    out["authors"] = identity(book.author, out.get("authors")) or out.get("authors")
    out["rating"] = field(book.rating, out.get("rating"))
    out["series"] = field(book.series, out.get("series"))
    out["series_index"] = field(book.series_position, out.get("series_index"))
    out["pubdate"] = field(book.published_date, out.get("pubdate"))
    out["page_count"] = book.page_count
    out["overlay_cover_url"] = book.cover_url
    out["genres"] = (
        [g.strip() for g in (book.genres or "").split(",") if g.strip()] or None
    )

    if "description" in out:
        out["description"] = field(book.description, out.get("description"))

    return out


def books_missing_metadata(
    db: Session, *, stale_days: int = 7, limit: Optional[int] = None
) -> list[CalibreBookLink]:
    """Links whose ``Book`` row is missing the metadata the overlay shows.

    Selects a link when its Book has never been refreshed, or was refreshed more
    than ``stale_days`` ago and still has a gap in description / cover / genres.
    Never-refreshed rows come first so repeated capped runs make progress.
    """
    stale_before = datetime.now(timezone.utc) - timedelta(days=stale_days)
    q = (
        db.query(CalibreBookLink)
        .join(Book, Book.id == CalibreBookLink.book_id)
        .filter(
            (Book.last_refreshed.is_(None))
            | (
                (Book.last_refreshed < stale_before)
                & (
                    Book.description.is_(None)
                    | Book.cover_url.is_(None)
                    | Book.genres.is_(None)
                )
            )
        )
        .order_by(Book.last_refreshed.asc())
    )
    if limit:
        q = q.limit(limit)
    return q.all()


def sync_availability_flags(db: Session, library_path: str) -> int:
    """Set ``ebook_available`` on linked Books that carry an ebook format in the
    library, so the rest of the app sees a linked library book as owned. (This
    Calibre library only ever holds ebooks.)
    """
    links = db.query(CalibreBookLink).options(joinedload(CalibreBookLink.book)).all()
    if not links:
        return 0
    try:
        fmt_map = calibre_service.formats_for_ids(
            library_path, [l.calibre_book_id for l in links]
        )
    except calibre_service.CalibreError as exc:
        logger.warning("calibre_link_availability_probe_failed", error=str(exc))
        return 0

    changed = 0
    for link in links:
        formats = fmt_map.get(link.calibre_book_id, [])
        book = link.book
        if book is None or book.ebook_available:
            continue
        if any(calibre_service.classify_format(f) == "ebook" for f in formats):
            book.ebook_available = True
            changed += 1

    if changed:
        db.commit()
        logger.info("calibre_link_availability_synced", changed=changed)
    return changed


def heal_stale_links(db: Session, library_path: str) -> int:
    """Drop or re-point links whose ``calibre_book_id`` no longer resolves.

    Calibre reassigns ids on delete + re-add. When the stored id is gone we try
    to find the book again by its snapshotted ISBN/title; failing that the link
    is removed so the fuzzy pass can rebuild it.
    """
    links = db.query(CalibreBookLink).all()
    if not links:
        return 0

    try:
        present = calibre_service.existing_book_ids(library_path)
    except calibre_service.CalibreError as exc:
        logger.warning("calibre_link_heal_probe_failed", error=str(exc))
        return 0

    healed = 0
    for link in links:
        if link.calibre_book_id in present:
            continue
        new_id = None
        if link.calibre_isbn or link.calibre_title:
            try:
                new_id = calibre_service.find_book_match(
                    library_path,
                    link.calibre_title or "",
                    None,
                    link.calibre_isbn,
                )
            except calibre_service.CalibreError:
                new_id = None
        if new_id and new_id in present:
            link.calibre_book_id = new_id
            healed += 1
            logger.info(
                "calibre_link_repointed", book_id=link.book_id, calibre_book_id=new_id
            )
        else:
            db.delete(link)
            healed += 1
            logger.info("calibre_link_removed_stale", book_id=link.book_id)

    if healed:
        db.commit()
    return healed


# Cap the unlinked-library scan per run so a huge library cannot stall the
# once-a-minute reconcile job. Matched books get a link and drop out.
BACKFILL_SCAN_LIMIT = 400


def _book_index(db: Session):
    """(isbn_map, catalog) over all Book rows, for matching Calibre books to them."""
    isbn_map: dict[str, int] = {}
    catalog: list[tuple[int, set, set]] = []
    for bid, title, author, isbn in db.query(
        Book.id, Book.title, Book.author, Book.isbn
    ).all():
        key = calibre_service._isbn_key(isbn)
        if key:
            isbn_map.setdefault(key, bid)
        catalog.append(
            (
                bid,
                calibre_service._title_tokens(title or ""),
                calibre_service._author_tokens(author or ""),
            )
        )
    return isbn_map, catalog


def _match_calibre_book(
    title: str, author: Optional[str], isbn: Optional[str], isbn_map: dict, catalog: list
) -> Optional[int]:
    key = calibre_service._isbn_key(isbn)
    if key and key in isbn_map:
        return isbn_map[key]
    want_title = calibre_service._title_tokens(title or "")
    if not want_title:
        return None
    want_author = calibre_service._author_tokens(author or "")
    best_id, best = None, -1.0
    for cand_id, cand_title, cand_auth in catalog:
        if not calibre_service._titles_match(want_title, cand_title):
            continue
        author_score = 0.0
        if want_author and cand_auth:
            author_score = len(want_author & cand_auth) / len(want_author)
            if author_score == 0 and len(want_title) < 3:
                continue
        score = len(want_title & cand_title) / max(len(want_title | cand_title), 1) + author_score
        if score > best:
            best, best_id = score, cand_id
    return best_id


def find_matching_book(
    db: Session, title: str, author: Optional[str] = None, isbn: Optional[str] = None
) -> Optional[Book]:
    """An existing ``Book`` row that is the same work as ``(title, author, isbn)``, or None.

    For use *before* creating a new Book from an external source (an
    Audiobookshelf item, an unlinked Calibre book), so the ebook and audiobook of
    one work land on the same record even when their titles differ only by a
    subtitle, or each resolved to a different Hardcover entry for the same book.

    Stricter than `_match_calibre_book`: when both sides name a (non-placeholder)
    author they must share a token — wrongly merging two different books is worse
    than missing a merge, which just leaves a second record to merge by hand.
    """
    key = calibre_service._isbn_key(isbn)
    want_title = calibre_service._title_tokens(title or "")
    if not want_title and not key:
        return None
    want_author = calibre_service._author_tokens(author or "")

    best_id: Optional[int] = None
    best_score = -1.0
    for bid, btitle, bisbn, bauthor in db.query(
        Book.id, Book.title, Book.isbn, Book.author
    ).all():
        if key and calibre_service._isbn_key(bisbn) == key:
            return db.query(Book).filter(Book.id == bid).first()
        if not want_title:
            continue
        cand_title = calibre_service._title_tokens(btitle or "")
        if not calibre_service._titles_match(want_title, cand_title):
            continue
        cand_author = calibre_service._author_tokens(bauthor or "")
        if want_author and cand_author and not (want_author & cand_author):
            continue
        score = len(want_title & cand_title) / max(len(want_title | cand_title), 1)
        if want_author and cand_author:
            score += len(want_author & cand_author) / len(want_author)
        if score > best_score:
            best_id, best_score = bid, score

    return db.query(Book).filter(Book.id == best_id).first() if best_id is not None else None


def backfill_fuzzy_links(db: Session, library_path: str) -> int:
    """Match not-yet-linked library books to ``Book`` rows and persist fuzzy links.

    Driven from the Calibre side (the bounded set): each library book without a
    link is matched against the ``Book`` table by ISBN then fuzzy title/author.
    """
    try:
        library_ids = calibre_service.existing_book_ids(library_path)
    except calibre_service.CalibreError as exc:
        logger.warning("calibre_link_backfill_probe_failed", error=str(exc))
        return 0

    linked = {r[0] for r in db.query(CalibreBookLink.calibre_book_id).all()}
    todo = sorted(library_ids - linked)[:BACKFILL_SCAN_LIMIT]
    if not todo:
        return 0

    try:
        identities = calibre_service.book_identities(library_path, todo)
    except calibre_service.CalibreError as exc:
        logger.warning("calibre_link_backfill_lookup_failed", error=str(exc))
        return 0

    isbn_map, catalog = _book_index(db)
    if not catalog:
        return 0

    created = 0
    for cal_id, title, author, isbn in identities:
        book_id = _match_calibre_book(title, author, isbn, isbn_map, catalog)
        if book_id is None:
            continue
        link = upsert_link(
            db,
            calibre_book_id=cal_id,
            book_id=book_id,
            source="fuzzy",
            confidence=None,
            confirmed=False,
            calibre_isbn=isbn,
            calibre_title=title,
            commit=False,
        )
        if link is not None:
            created += 1

    if created:
        db.commit()
        logger.info("calibre_link_backfill_complete", created=created)
    return created
