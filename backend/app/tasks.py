"""
Background tasks for refreshing seed data
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_, func as sa_func
from app.database import SessionLocal
from app.models import Book
from app import schemas
from app.routers.hardcover import execute_graphql, _parse_hardcover_book
from app.routers.settings import get_hardcover_token
import structlog

logger = structlog.get_logger()


def create_book_from_booklore_data(
    title: str,
    author: Optional[str],
    description: Optional[str],
    cover_url: Optional[str],
    isbn: Optional[str],
    page_count: Optional[int],
    published_date: Optional[str],
    hardcover_id: int,
    hardcover_slug: Optional[str],
    booklore_id: str,
    booklore_added_on: Optional[datetime],
    series_name: Optional[str],
    series_id: Optional[int],
    series_number: Optional[float],
    rating: Optional[float],
    ratings_count: Optional[int],
    users_count: Optional[int],
    genres: Optional[str],
    format_type: str,
) -> Book:
    """
    Helper function to create a Book instance from Booklore data.
    Eliminates duplicate book creation logic.
    """
    return Book(
        title=title,
        author=author,
        description=description,
        cover_url=cover_url,
        isbn=isbn,
        page_count=page_count,
        published_date=published_date,
        hardcover_id=hardcover_id,
        hardcover_slug=hardcover_slug,
        booklore_id=booklore_id,
        booklore_added_on=booklore_added_on,
        series=series_name,
        series_id=series_id,
        series_position=series_number,
        rating=rating,
        ratings_count=ratings_count,
        users_count=users_count,
        genres=genres,
        ebook_available=(format_type == "ebook"),
        audiobook_available=(format_type == "audiobook"),
    )


async def refresh_seed_data():
    """Background task to fetch new books from Hardcover API using progressive offset"""
    import json
    from app.models import JobSchedule
    
    db: Session = SessionLocal()
    try:
        # Check if we have a token
        token, _ = get_hardcover_token(db)
        if not token:
            logger.info("refresh_seed_data_skipped", reason="no_token")
            return
        
        # Get job state for offset tracking
        job = db.query(JobSchedule).filter(JobSchedule.job_name == "refresh_seed_data").first()
        
        # Parse state JSON or initialize
        state = {}
        if job and job.state_json:
            try:
                state = json.loads(job.state_json)
            except:
                state = {}
        
        current_offset = state.get("offset", 0)
        batch_size = 1000  # Fetch 100 books per run
        
        logger.info("refresh_seed_data_starting", offset=current_offset, batch_size=batch_size)
        
        # Get all existing hardcover_ids to skip duplicates
        existing_ids = set(
            row[0] for row in db.query(Book.hardcover_id).filter(Book.hardcover_id.isnot(None)).all()
        )
        logger.info("refresh_seed_data_existing_books", count=len(existing_ids))
        
        # Fetch books using offset - order by users_count for variety
        query = """
        query PopularBooks($limit: Int!, $offset: Int!) {
          books(
            order_by: [{users_count: desc_nulls_last}, {rating: desc_nulls_last}],
            limit: $limit,
            offset: $offset,
            where: {ratings_count: {_gte: 50}}
          ) {
            id
            title
            slug
            release_year
            release_date
            pages
            description
            cached_image
            cached_contributors
            rating
            ratings_count
            users_count
            activities_count
            book_series {
              series {
                id
                name
              }
              position
            }
            contributions {
              author {
                id
                name
                slug
              }
            }
            taggings(limit: 10) {
              tag {
                tag
              }
            }
          }
        }
        """
        
        result = await execute_graphql(query, {"limit": batch_size, "offset": current_offset}, db=db)
        books_data = result.get("books", [])
        
        if not books_data:
            # No more books at this offset, reset to beginning
            logger.info("refresh_seed_data_offset_reset", old_offset=current_offset)
            current_offset = 0
            state["offset"] = 0
            if job:
                job.state_json = json.dumps(state)
                db.commit()
            return
        
        inserted = 0
        skipped = 0
        
        for hc_book_data in books_data:
            hardcover_id = hc_book_data.get("id")
            if not hardcover_id:
                continue
            
            # Skip if already exists
            if hardcover_id in existing_ids:
                skipped += 1
                continue
            
            # Parse book
            try:
                hc_book = _parse_hardcover_book(hc_book_data)
            except Exception as e:
                logger.debug("refresh_seed_data_parse_error", hardcover_id=hardcover_id, error=str(e))
                continue
            
            # Extract data
            authors = []
            if hc_book.contributions:
                authors = [c.author.name for c in hc_book.contributions if c.author]
            elif hc_book.cached_contributors:
                authors = [c.author.get("name", "") if isinstance(c.author, dict) else "" for c in hc_book.cached_contributors]
            
            author = ", ".join(authors) if authors else "Unknown Author"
            
            cover_url = None
            if hc_book.cached_image and isinstance(hc_book.cached_image, schemas.HardcoverCachedImage):
                cover_url = hc_book.cached_image.url
            
            series = None
            series_id = None
            series_position = None
            if hc_book.book_series and len(hc_book.book_series) > 0:
                series = hc_book.book_series[0].series.name
                series_id = hc_book.book_series[0].series.id
                series_position = hc_book.book_series[0].position
            
            genres = []
            if hc_book.taggings:
                genres = [t.tag.tag for t in hc_book.taggings if t.tag]
            
            # Create new book
            db_book = Book(
                title=hc_book.title,
                author=author,
                description=hc_book.description,
                cover_url=cover_url,
                published_date=hc_book.release_date or str(hc_book.release_year or ""),
                rating=hc_book.rating,
                page_count=hc_book.pages,
                hardcover_id=hardcover_id,
                hardcover_slug=hc_book.slug,
                series=series,
                series_id=series_id,
                series_position=series_position,
                genres=", ".join(genres) if genres else None,
                ratings_count=hc_book.ratings_count,
                users_count=hc_book.users_count,
                activities_count=hc_book.activities_count,
                release_year=hc_book.release_year,
                is_seed_data=True,
                last_refreshed=datetime.now(timezone.utc),
            )
            db.add(db_book)
            existing_ids.add(hardcover_id)  # Track to avoid duplicates in same batch
            inserted += 1
        
        # Update offset for next run
        new_offset = current_offset + batch_size
        state["offset"] = new_offset
        state["last_inserted"] = inserted
        state["total_processed"] = state.get("total_processed", 0) + len(books_data)
        
        if job:
            job.state_json = json.dumps(state)
        
        db.commit()
        logger.info("refresh_seed_data_complete", 
                   inserted=inserted, 
                   skipped=skipped,
                   fetched=len(books_data),
                   current_offset=current_offset,
                   next_offset=new_offset,
                   total_books_in_db=len(existing_ids))
        
    except Exception as e:
        logger.error("refresh_seed_data_error", error=str(e))
        db.rollback()
    finally:
        db.close()

def update_job_execution(job_name: str, max_retries: int = 3):
    """Update the last and next execution times for a job with retry logic"""
    import time
    from app.models import JobSchedule
    
    for attempt in range(max_retries):
        db = SessionLocal()
        try:
            schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
            if schedule:
                schedule.last_execution = datetime.now(timezone.utc)
                interval = schedule.interval_seconds or 3600
                schedule.next_execution = datetime.now(timezone.utc) + timedelta(seconds=interval)
                db.commit()
            return  # Success
        except Exception as e:
            db.rollback()
            if "database is locked" in str(e) and attempt < max_retries - 1:
                logger.debug("update_job_execution_retry", job_name=job_name, attempt=attempt + 1)
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
            else:
                logger.warning("update_job_execution_failed", job_name=job_name, error=str(e))
        finally:
            db.close()


def get_job_interval_standalone(job_name: str, default_seconds: int = 3600) -> int:
    """Get the interval for a job from the database (standalone, creates own session)"""
    from app.models import JobSchedule
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule and schedule.interval_seconds:
            return schedule.interval_seconds
        return default_seconds
    except Exception:
        return default_seconds
    finally:
        db.close()


def get_seconds_until_next_execution(job_name: str) -> int:
    """Get seconds until the next scheduled execution, or 0 if it should run now"""
    from app.models import JobSchedule
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule and schedule.next_execution:
            now = datetime.now(timezone.utc)
            if schedule.next_execution > now:
                return int((schedule.next_execution - now).total_seconds())
        # If no next_execution or it's in the past, check last_execution + interval
        if schedule and schedule.last_execution:
            interval = schedule.interval_seconds or 3600
            next_run = schedule.last_execution + timedelta(seconds=interval)
            if next_run > datetime.now(timezone.utc):
                return int((next_run - datetime.now(timezone.utc)).total_seconds())
        return 0  # Run immediately if never run before
    except Exception:
        return 0
    finally:
        db.close()


async def run_background_refresh():
    """Background task to refresh seed data periodically"""
    job_name = "refresh_seed_data"
    
    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_refresh_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)
    
    while True:
        try:
            await refresh_seed_data()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_refresh_error", error=str(e))
        
        # Get interval from database (default 24 hours)
        interval = get_job_interval_standalone(job_name, 24 * 60 * 60)
        logger.debug("background_refresh_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)

# An ebook download that never shows up in Calibre is marked imported anyway
# after this long, so requests don't hang forever on a misconfigured library.
EBOOK_LIBRARY_WAIT_TIMEOUT = timedelta(days=7)


def reconcile_ebook_library_imports(
    db: Session, library_path: Optional[str] = None
) -> list[int]:
    """Promote completed ebook downloads to 'imported' once Calibre has indexed them.

    The orchestrator parks ebook imports in 'awaiting_library' when a Calibre
    library is configured; this checks that library for each one and flips it to
    'imported' (also marking the book available). Returns the ``book_id``s that
    were just promoted, so the caller can refresh their metadata.
    """
    from app.models import DownloadTask
    from app.routers.calibre import get_active_library_path
    from app.services import calibre_service, calibre_link_service

    tasks = (
        db.query(DownloadTask)
        .filter(
            DownloadTask.format == "ebook",
            DownloadTask.state.in_(["complete", "seeding"]),
            DownloadTask.import_status == "awaiting_library",
        )
        .all()
    )
    if not tasks:
        return []

    if library_path is None:
        library_path = get_active_library_path(db)

    matched_ids: dict[int, Optional[int]] = {}
    if library_path:
        try:
            results = calibre_service.match_books(
                library_path,
                [(t.book.title, t.book.author, t.book.isbn) for t in tasks if t.book],
            )
            for t, mid in zip([t for t in tasks if t.book], results):
                matched_ids[t.id] = mid
        except calibre_service.CalibreError as exc:
            logger.warning("ebook_library_reconcile_lookup_failed", error=str(exc))

    now = datetime.now(timezone.utc)
    promoted: list[int] = []
    for task in tasks:
        found = matched_ids.get(task.id) is not None

        timed_out = False
        if not found:
            completed_at = task.completed_at or task.updated_at or task.created_at
            if completed_at is not None and completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)
            if completed_at is not None and (now - completed_at) >= EBOOK_LIBRARY_WAIT_TIMEOUT:
                timed_out = True

        if not found and not timed_out:
            continue

        task.import_status = "imported"
        task.imported_at = now
        task.import_message = (
            "Indexed by Calibre" if found
            else "Marked imported after waiting for the Calibre library"
        )
        if task.book:
            task.book.ebook_available = True
        if found and task.book:
            # Exact link: this download is this Calibre book.
            try:
                calibre_link_service.upsert_link(
                    db,
                    calibre_book_id=matched_ids[task.id],
                    book_id=task.book_id,
                    source="download",
                    confidence=None,
                    confirmed=True,
                    calibre_isbn=task.book.isbn,
                    calibre_title=task.book.title,
                    commit=False,
                )
            except Exception as exc:  # never block the import promotion
                logger.warning("calibre_link_on_import_failed", task_id=task.id, error=str(exc))
        if task.book_id is not None:
            promoted.append(task.book_id)
        logger.info(
            "ebook_import_confirmed",
            task_id=task.id,
            book_id=task.book_id,
            reason="calibre" if found else "timeout",
        )

    if promoted:
        db.commit()
        logger.info(
            "reconcile_ebook_library_imports_complete",
            promoted=len(promoted),
            checked=len(tasks),
        )
    return promoted


async def _refresh_downloaded_books(db: Session, book_ids) -> None:
    """One-time, best-quality metadata refresh for books that just became available.

    Runs the full merge (Google Books description, Hardcover ratings/series, Open
    Library fallback) with overwrite, so a downloaded book stops carrying
    whatever thin blurb it had from being browsed pre-download. Called only on
    the state transition, so it is cheap on the APIs.
    """
    if not book_ids:
        return

    from app.models import Book
    from app.services import book_metadata

    seen: set[int] = set()
    for bid in book_ids:
        if bid is None or bid in seen:
            continue
        seen.add(bid)
        book = db.query(Book).filter(Book.id == bid).first()
        if book is None:
            continue
        try:
            changed = await book_metadata.enrich_book(
                db, book, overwrite=True, resolve_hardcover=True, use_google=True
            )
            if book.last_refreshed is None:
                book.last_refreshed = datetime.now(timezone.utc)
            db.commit()
            if changed:
                logger.info("downloaded_book_metadata_refreshed", book_id=bid)
        except Exception as exc:
            db.rollback()
            logger.warning("downloaded_book_metadata_refresh_failed", book_id=bid, error=str(exc))
        await asyncio.sleep(0.3)


async def check_processing_requests():
    """Background task to check Booklore and update processing requests"""
    from app.routers.requests import update_processing_requests_status
    db: Session = SessionLocal()
    try:
        # Belt-and-suspenders: reconcile_calibre_library also does this every
        # minute, but keep a slower fallback in case that job is disabled.
        promoted: list[int] = []
        try:
            promoted = reconcile_ebook_library_imports(db)
        except Exception as e:
            logger.error("reconcile_ebook_library_imports_error", error=str(e))
            db.rollback()
        await _refresh_downloaded_books(db, promoted)
        await update_processing_requests_status(db)
        await send_availability_emails()
    except Exception as e:
        logger.error("check_processing_requests_error", error=str(e))
    finally:
        db.close()


async def reconcile_calibre_library():
    """Every minute: treat the Calibre library as the source of truth for ebooks.

    1. Promotes completed ebook downloads that Calibre has now indexed.
    2. Flips any not-yet-available ebook request (pending / approved / processing
       / not_found) to 'available' once its book is in the library — however it
       got there (download, manual add, side-load).
    The whole library is loaded once and every request matched against it.
    """
    from sqlalchemy.orm import joinedload
    from app.models import BookRequest
    from app.routers.calibre import get_active_library_path
    from app.services import calibre_service, calibre_link_service

    db: Session = SessionLocal()
    try:
        library_path = get_active_library_path(db)
        if not library_path:
            return

        promoted: list[int] = []
        try:
            promoted = reconcile_ebook_library_imports(db, library_path=library_path)
        except Exception as e:
            logger.error("reconcile_ebook_library_imports_error", error=str(e))
            db.rollback()

        # Keep the Calibre <-> Book link table healthy, then enrich any
        # linked books not yet filled from Hardcover. The heal/backfill scan is
        # throttled — download and request links are created inline elsewhere,
        # so this only needs to catch side-loads periodically.
        global _LAST_LINK_MAINTENANCE
        now_ts = datetime.now(timezone.utc)
        if (now_ts - _LAST_LINK_MAINTENANCE) >= LINK_MAINTENANCE_INTERVAL:
            _LAST_LINK_MAINTENANCE = now_ts
            try:
                calibre_link_service.heal_stale_links(db, library_path)
                calibre_link_service.backfill_fuzzy_links(db, library_path)
                calibre_link_service.sync_availability_flags(db, library_path)
            except Exception as e:
                logger.error("calibre_link_maintenance_error", error=str(e))
                db.rollback()
        try:
            await _enrich_linked_calibre_books(db)
        except Exception as e:
            logger.error("calibre_link_enrich_error", error=str(e))
            db.rollback()

        reqs = (
            db.query(BookRequest)
            .options(joinedload(BookRequest.book))
            .filter(
                BookRequest.format == "ebook",
                BookRequest.status.in_(["pending", "approved", "processing", "not_found"]),
            )
            .all()
        )
        reqs = [r for r in reqs if r.book]

        matches = []
        if reqs:
            try:
                matches = calibre_service.match_books(
                    library_path, [(r.book.title, r.book.author, r.book.isbn) for r in reqs]
                )
            except calibre_service.CalibreError as exc:
                logger.warning("reconcile_calibre_library_lookup_failed", error=str(exc))

        now = datetime.now(timezone.utc)
        updated = 0
        for req, calibre_id in zip(reqs, matches):
            if calibre_id is None:
                continue
            prev = req.status
            req.status = "available"
            req.updated_at = now
            req.book.ebook_available = True
            updated += 1
            promoted.append(req.book_id)
            # A request that resolved to a library book is a strong link.
            try:
                calibre_link_service.upsert_link(
                    db,
                    calibre_book_id=calibre_id,
                    book_id=req.book_id,
                    source="download" if req.edition_id or req.book.hardcover_id else "fuzzy",
                    confidence=None,
                    confirmed=bool(req.edition_id or req.book.hardcover_id),
                    calibre_isbn=req.book.isbn,
                    calibre_title=req.book.title,
                    commit=False,
                )
            except Exception as exc:
                logger.warning("calibre_link_on_request_failed", request_id=req.id, error=str(exc))
            logger.info(
                "request_available_from_calibre",
                request_id=req.id,
                book_id=req.book_id,
                calibre_id=calibre_id,
                previous_status=prev,
            )
        if updated:
            db.commit()
            logger.info("reconcile_calibre_library_complete", updated=updated, checked=len(reqs))

        await _refresh_downloaded_books(db, promoted)

        # An ebook that just landed in the library may have a waiting request —
        # email it now, and drop the request cache so the book page stops showing
        # the stale status (and "cancel my request" button), rather than waiting
        # for the next periodic run / the 5 min cache TTL.
        if updated or promoted:
            from app.cache import clear_cache_pattern
            await clear_cache_pattern("requests_by_hardcover:*")
            await clear_cache_pattern("requests_by_hardcover_batch:*")
            await send_availability_emails()
    except Exception as e:
        logger.error("reconcile_calibre_library_error", error=str(e))
        db.rollback()
    finally:
        db.close()


# Books enriched per reconcile run, to stay within Hardcover's rate limits.
CALIBRE_ENRICH_BATCH = 10

# The full library <-> Book match scan is expensive; run it at most this often.
LINK_MAINTENANCE_INTERVAL = timedelta(minutes=10)
_LAST_LINK_MAINTENANCE = datetime.min.replace(tzinfo=timezone.utc)


# Cap the daily/startup Calibre metadata sweep so one run stays polite to the
# upstream metadata APIs on a very large library. Whatever is left over is
# picked up on the next run (and by the per-minute reconcile batch in between).
CALIBRE_METADATA_SCAN_LIMIT = 400


async def _enrich_calibre_metadata(db: Session, *, limit: int) -> int:
    """Fill in metadata for linked Calibre books that are missing it.

    Shared by the per-minute reconcile pass (small batch) and the
    ``sync_calibre_metadata`` job (full sweep). Targets linked books with a gap
    in the fields the overlay shows — description, cover, genres — or that have
    never been refreshed. Metadata comes from Open Library plus Hardcover (for
    linked books); Google Books is left for the post-download refresh so this
    sweep does not spend its daily quota. Honors the overlay toggle. One book at
    a time with a short pause between lookups.
    """
    from app.routers.calibre import _bool_setting, OVERLAY_ENABLED_KEY
    from app.services import book_metadata, calibre_link_service

    if not _bool_setting(db, OVERLAY_ENABLED_KEY, True):
        return 0

    rows = calibre_link_service.books_missing_metadata(db, limit=limit)
    if not rows:
        return 0

    enriched = 0
    for link in rows:
        book = link.book
        try:
            ok = await book_metadata.enrich_book(db, book)
        except Exception as exc:
            logger.warning("calibre_book_enrich_failed", book_id=book.id, error=str(exc))
            db.rollback()
            continue
        # Stamp last_refreshed even on a no-op so we do not retry it every run.
        if book.last_refreshed is None:
            book.last_refreshed = datetime.now(timezone.utc)
        try:
            db.commit()
            if ok:
                enriched += 1
        except Exception as exc:
            db.rollback()
            logger.warning("calibre_book_enrich_commit_failed", book_id=book.id, error=str(exc))
        await asyncio.sleep(0.5)

    if enriched:
        logger.info("calibre_book_enrich_complete", enriched=enriched, checked=len(rows))
    return enriched


async def _enrich_linked_calibre_books(db: Session) -> int:
    """Per-minute slice of the Calibre metadata backfill (see sync_calibre_metadata)."""
    return await _enrich_calibre_metadata(db, limit=CALIBRE_ENRICH_BATCH)


async def _import_unlinked_calibre_books(
    db: Session, library_path: str, *, limit: int
) -> int:
    """Give a ``Book`` row + metadata to Calibre books that have neither.

    ``backfill_fuzzy_links`` only links library books that already match a row in
    our ``books`` table, so a side-loaded book we have never seen stays
    "metadata from Calibre only" forever. For each such book this creates the
    ``Book`` row from the Calibre identity, enriches it (Open Library, then
    Hardcover), and — only if something was actually found — links it. Bounded
    per run.
    """
    from app.services import book_metadata, calibre_service, calibre_link_service
    from app.models import Book, CalibreBookLink

    try:
        library_ids = calibre_service.existing_book_ids(library_path)
    except calibre_service.CalibreError as exc:
        logger.warning("calibre_metadata_probe_failed", error=str(exc))
        return 0

    linked = {r[0] for r in db.query(CalibreBookLink.calibre_book_id).all()}
    todo = sorted(library_ids - linked)[:limit]
    if not todo:
        return 0

    try:
        identities = calibre_service.book_identities(library_path, todo)
        fmt_map = calibre_service.formats_for_ids(library_path, todo)
    except calibre_service.CalibreError as exc:
        logger.warning("calibre_metadata_identities_failed", error=str(exc))
        return 0

    created = 0
    for cal_id, title, author, isbn in identities:
        if not title:
            continue

        kinds = {calibre_service.classify_format(f) for f in fmt_map.get(cal_id, [])}

        book = db.query(Book).filter(Book.isbn == isbn).first() if isbn else None
        fresh_row = book is None
        if book is None:
            book = Book(
                title=title,
                author=author or "Unknown Author",
                isbn=isbn or None,
                ebook_available="ebook" in kinds,
                audiobook_available="audiobook" in kinds,
            )
            db.add(book)
            try:
                db.flush()
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "calibre_metadata_book_create_failed",
                    calibre_id=cal_id,
                    title=title,
                    error=str(exc),
                )
                continue

        try:
            found = await book_metadata.enrich_book(db, book, resolve_hardcover=True)
        except Exception as exc:
            logger.warning("calibre_metadata_enrich_failed", calibre_id=cal_id, error=str(exc))
            db.rollback()
            continue

        if fresh_row and not found and not book.hardcover_id:
            # Nothing to show for this book — leave it "Calibre only" rather than
            # keeping a bare linked row that looks enriched but is not.
            db.rollback()
            await asyncio.sleep(0.5)
            continue

        calibre_link_service.upsert_link(
            db,
            calibre_book_id=cal_id,
            book_id=book.id,
            source="fuzzy",
            confidence=None,
            confirmed=False,
            calibre_isbn=isbn,
            calibre_title=title,
            commit=False,
        )
        if book.last_refreshed is None:
            book.last_refreshed = datetime.now(timezone.utc)
        try:
            db.commit()
            created += 1
        except Exception as exc:
            db.rollback()
            logger.warning("calibre_metadata_commit_failed", calibre_id=cal_id, error=str(exc))
        await asyncio.sleep(0.5)

    if created:
        logger.info(
            "calibre_metadata_imported_unlinked", created=created, checked=len(identities)
        )
    return created


async def sync_calibre_metadata() -> None:
    """Batch-fetch missing metadata for Calibre library books.

    Runs on startup and once a day (and can be triggered from the admin Jobs
    page). Metadata comes from Open Library first (no API key, no rate limit),
    then Hardcover for series / ratings / anything still missing. Three passes,
    each bounded per run:

    1. Link library books that match a ``Book`` row we already have.
    2. Give a ``Book`` row + metadata to library books that have neither,
       then link them (this is what clears "metadata from Calibre only").
    3. Refresh linked books whose local metadata is absent or stale-incomplete.
    """
    from app.routers.calibre import get_active_library_path, _bool_setting, OVERLAY_ENABLED_KEY
    from app.services import calibre_link_service

    db: Session = SessionLocal()
    try:
        library_path = get_active_library_path(db)
        if not library_path:
            logger.info("sync_calibre_metadata_skipped", reason="no_calibre_library")
            return
        if not _bool_setting(db, OVERLAY_ENABLED_KEY, True):
            logger.info("sync_calibre_metadata_skipped", reason="overlay_disabled")
            return

        try:
            calibre_link_service.heal_stale_links(db, library_path)
            calibre_link_service.backfill_fuzzy_links(db, library_path)
        except Exception as e:
            logger.error("sync_calibre_metadata_link_error", error=str(e))
            db.rollback()

        imported = 0
        try:
            imported = await _import_unlinked_calibre_books(
                db, library_path, limit=CALIBRE_METADATA_SCAN_LIMIT
            )
        except Exception as e:
            logger.error("sync_calibre_metadata_import_error", error=str(e))
            db.rollback()

        enriched = await _enrich_calibre_metadata(db, limit=CALIBRE_METADATA_SCAN_LIMIT)
        logger.info(
            "sync_calibre_metadata_complete", imported=imported, enriched=enriched
        )
    except Exception as e:
        logger.error("sync_calibre_metadata_error", error=str(e))
        db.rollback()
    finally:
        db.close()


# Give up auto-emailing a request after this many failed SMTP attempts.
MAX_AUTO_EMAIL_ATTEMPTS = 5


async def send_availability_emails():
    """Notify users when a book they requested (with the flag set) is available.

    Picks up requests flagged ``auto_email_when_available`` that have reached
    ``available`` status. eBooks are emailed as a file attachment pulled from the
    Calibre library (retried across runs until the file turns up or SMTP attempts
    are exhausted). Audiobooks get a plain "it's available" notification with no
    attachment.
    """
    from app.models import BookRequest
    from app.routers.calibre import get_active_library_path
    from app.services import calibre_service
    from app.services.email_service import (
        send_book_email,
        send_availability_notification,
        get_smtp_config,
        EmailError,
    )

    db: Session = SessionLocal()
    try:
        pending = (
            db.query(BookRequest)
            .filter(
                BookRequest.auto_email_when_available.is_(True),
                BookRequest.auto_email_sent_at.is_(None),
                BookRequest.status == "available",
            )
            .all()
        )
        if not pending:
            return

        if not get_smtp_config(db).configured:
            logger.info("send_availability_emails_skipped_no_smtp", pending=len(pending))
            return

        library_path = get_active_library_path(db)

        def _note_email_failure(req, exc: EmailError) -> None:
            """Log a failed attempt and give up once the retry budget is spent."""
            logger.warning(
                "availability_email_failed",
                request_id=req.id,
                attempt=req.auto_email_attempts,
                error=str(exc),
            )
            if req.auto_email_attempts >= MAX_AUTO_EMAIL_ATTEMPTS:
                req.auto_email_sent_at = datetime.now(timezone.utc)
                logger.error("availability_email_gave_up", request_id=req.id)

        sent = 0
        for req in pending:
            user = req.user
            book = req.book
            if not user or not book or not (user.book_delivery_email or "").strip():
                continue

            # Audiobooks: a notification only — we never attach the audio files.
            if req.format == "audiobook":
                req.auto_email_attempts = (req.auto_email_attempts or 0) + 1
                try:
                    send_availability_notification(
                        db, user, book_title=book.title, book_format="audiobook"
                    )
                    req.auto_email_sent_at = datetime.now(timezone.utc)
                    sent += 1
                    logger.info("availability_email_sent", request_id=req.id, user_id=user.id, book_id=book.id)
                except EmailError as exc:
                    _note_email_failure(req, exc)
                db.commit()
                continue

            # eBooks: attach the file once it shows up in the Calibre library.
            if not library_path:
                continue

            try:
                match_id = calibre_service.find_book_match(
                    library_path, book.title, book.author, book.isbn
                )
            except calibre_service.CalibreError as exc:
                logger.warning("availability_email_match_failed", request_id=req.id, error=str(exc))
                continue

            if match_id is None:
                # Not in the library yet — try again on a later run.
                continue

            fmt = calibre_service.pick_format(library_path, match_id, req.format)
            if not fmt:
                continue

            file_result = calibre_service.format_file(library_path, match_id, fmt)
            if file_result is None:
                continue
            path, download_name, media_type = file_result

            req.auto_email_attempts = (req.auto_email_attempts or 0) + 1
            try:
                send_book_email(
                    db,
                    user,
                    file_path=path,
                    download_name=download_name,
                    media_type=media_type,
                    book_title=book.title,
                    book_format=fmt,
                )
                req.auto_email_sent_at = datetime.now(timezone.utc)
                sent += 1
                logger.info("availability_email_sent", request_id=req.id, user_id=user.id, book_id=book.id)
            except EmailError as exc:
                _note_email_failure(req, exc)
            db.commit()

        if sent:
            logger.info("send_availability_emails_complete", sent=sent, pending=len(pending))
    except Exception as e:
        logger.error("send_availability_emails_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def promote_and_email() -> None:
    """Promote freshly-completed requests to ``available`` and send their
    availability emails right now, instead of waiting for the periodic jobs.

    Both steps are idempotent and self-selecting (they scan for work and no-op
    when there is none), so this is safe to call after any download finishes and
    is purely additive to ``check_processing_requests`` / ``send_availability_emails``.
    """
    from app.routers.requests import update_processing_requests_status

    db: Session = SessionLocal()
    try:
        await update_processing_requests_status(db)
    except Exception as e:
        logger.error("promote_and_email_promote_error", error=str(e))
        db.rollback()
    finally:
        db.close()

    try:
        await send_availability_emails()
    except Exception as e:
        logger.error("promote_and_email_send_error", error=str(e))


def _parse_booklore_instant(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


async def sync_from_booklore():
    """
    Sync book availability from Booklore.
    - Imports books from Booklore into the local database
    - Creates "available" requests for them marked as "booklore_import"
    - Looks up books on Hardcover API to get full metadata
    """
    from app.routers.booklore import get_default_booklore_server, get_booklore_books
    from app.routers.hardcover import lookup_book_by_slug, lookup_book_by_title_author
    from app.models import BookRequest, User
    
    db: Session = SessionLocal()
    try:
        # Get Booklore server
        booklore_server = get_default_booklore_server(db)
        
        if not booklore_server:
            logger.info("sync_from_booklore_skipped", reason="no_booklore_server")
            return
        
        logger.info("sync_from_booklore_starting", server_name=booklore_server.name)
        
        # Fetch all books from Booklore
        booklore_books = await get_booklore_books(booklore_server, db)
        
        if not booklore_books:
            logger.info("sync_from_booklore_skipped", reason="no_books_in_booklore")
            return
        
        logger.info("sync_from_booklore_fetched", count=len(booklore_books))
        
        # Get admin user for creating import requests (use first admin or first user)
        admin_user = db.query(User).filter(User.is_admin == True).first()
        if not admin_user:
            admin_user = db.query(User).first()
        
        if not admin_user:
            logger.warning("sync_from_booklore_skipped", reason="no_users_in_system")
            return
        
        updated_count = 0  # Existing requests updated to available
        skipped_count = 0  # Books skipped (no hardcover ID)
        books_created = 0  # New books created in DB
        books_updated = 0  # Existing books updated in DB
        
        # Log first book structure for debugging
        if booklore_books:
            first_book = booklore_books[0]
            first_metadata = first_book.get("metadata", {})
            logger.info("booklore_sample_book",
                       title=first_book.get("title"),
                       metadata_keys=list(first_metadata.keys()) if first_metadata else [],
                       hardcover_id=first_metadata.get("hardcoverId"),
                       isbn13=first_metadata.get("isbn13"),
                       goodreads_id=first_metadata.get("goodreadsId"))
        
        for bl_book in booklore_books:
            metadata = bl_book.get("metadata") or {}
            booklore_id = bl_book.get("id")
            booklore_added_on = _parse_booklore_instant(bl_book.get("addedOn"))
            hardcover_id_raw = metadata.get("hardcoverId")
            
            # Extract basic info from Booklore
            title = bl_book.get("title") or metadata.get("title") or "Unknown Title"
            authors_list = metadata.get("authors") or []
            author = ", ".join(authors_list) if authors_list else "Unknown Author"
            
            # Determine format from Booklore book type and library mapping
            book_type = bl_book.get("bookType", "").lower()
            bl_library_id = bl_book.get("libraryId")
            if "audio" in book_type:
                format_type = "audiobook"
            elif bl_library_id and booklore_server.audiobook_library_id and bl_library_id == booklore_server.audiobook_library_id:
                format_type = "audiobook"
            elif bl_library_id and booklore_server.ebook_library_id and bl_library_id == booklore_server.ebook_library_id:
                format_type = "ebook"
            else:
                format_type = "ebook"

            # Fast-path: if we've already imported this Booklore book, skip Hardcover lookups
            if booklore_id:
                existing_by_booklore = db.query(Book).filter(Book.booklore_id == booklore_id).first()
                if existing_by_booklore:
                    if booklore_added_on:
                        existing_by_booklore.booklore_added_on = booklore_added_on
                    if format_type == "audiobook":
                        existing_by_booklore.audiobook_available = True
                    else:
                        existing_by_booklore.ebook_available = True
                    existing_by_booklore.last_refreshed = datetime.now(timezone.utc)
                    db.add(existing_by_booklore)

                    existing_request = db.query(BookRequest).filter(
                        BookRequest.book_id == existing_by_booklore.id,
                        BookRequest.format == format_type
                    ).first()
                    if existing_request and existing_request.status in ("processing", "approved", "pending"):
                        existing_request.status = "available"
                        existing_request.updated_at = datetime.now(timezone.utc)
                        updated_count += 1
                        logger.info("booklore_request_updated_to_available",
                                  hardcover_id=existing_by_booklore.hardcover_id,
                                  request_id=existing_request.id,
                                  title=existing_by_booklore.title,
                                  format=format_type)
                    try:
                        db.commit()
                    except Exception as commit_error:
                        logger.warning("booklore_book_commit_failed",
                                     title=existing_by_booklore.title,
                                     error=str(commit_error))
                        db.rollback()
                    continue

            # hardcoverId in Booklore can be either a numeric ID or a slug
            hardcover_id_int = None
            hardcover_slug = None
            hardcover_book_data = None
            
            if hardcover_id_raw:
                try:
                    # Try to parse as integer first
                    hardcover_id_int = int(hardcover_id_raw)
                except (ValueError, TypeError):
                    # It's a slug (e.g., "dogs-of-war-2017")
                    hardcover_slug = str(hardcover_id_raw)
                    
                    # Look up the full book data from Hardcover using the slug
                    try:
                        hardcover_book_data = await lookup_book_by_slug(hardcover_slug, db)
                        # Small delay to avoid rate limiting (0.5 seconds between requests)
                        await asyncio.sleep(0.5)
                        if hardcover_book_data:
                            hardcover_id_int = hardcover_book_data.get("id")
                            logger.info("hardcover_lookup_success",
                                      slug=hardcover_slug,
                                      hardcover_id=hardcover_id_int,
                                      title=hardcover_book_data.get("title"))
                    except Exception as e:
                        logger.warning("hardcover_lookup_failed", slug=hardcover_slug, error=str(e))
            
            # If still no hardcover identifier, try searching by title/author
            if not hardcover_id_int and not hardcover_slug:
                try:
                    hardcover_book_data = await lookup_book_by_title_author(title, author, db)
                    # Small delay to avoid rate limiting
                    await asyncio.sleep(0.5)
                    if hardcover_book_data:
                        hardcover_id_int = hardcover_book_data.get("id")
                        hardcover_slug = hardcover_book_data.get("slug")
                        logger.info("hardcover_search_success",
                                  title=title,
                                  author=author,
                                  hardcover_id=hardcover_id_int)
                except Exception as e:
                    logger.warning("hardcover_search_failed", title=title, error=str(e))
            
            # If still no hardcover ID, skip this book
            if not hardcover_id_int:
                skipped_count += 1
                logger.debug("booklore_book_skipped_no_hardcover_id",
                           title=title,
                           isbn=metadata.get("isbn13") or metadata.get("isbn10"))
                continue
            
            # Use data from Hardcover if available, otherwise fall back to Booklore
            if hardcover_book_data:
                # Use Hardcover data for better quality
                description = hardcover_book_data.get("description") or metadata.get("description")
                cached_image = hardcover_book_data.get("cached_image")
                cover_url = cached_image.get("url") if isinstance(cached_image, dict) else None
                page_count = hardcover_book_data.get("pages") or metadata.get("pageCount")
                published_date = hardcover_book_data.get("release_date") or str(hardcover_book_data.get("release_year", "")) or str(metadata.get("publishedDate") or "")
                rating = hardcover_book_data.get("rating")
                ratings_count = hardcover_book_data.get("ratings_count")
                users_count = hardcover_book_data.get("users_count")
                
                # Get series info
                book_series = hardcover_book_data.get("book_series", [])
                if book_series and len(book_series) > 0:
                    first_series = book_series[0]
                    series_info = first_series.get("series", {})
                    series_name = series_info.get("name")
                    series_id = series_info.get("id")
                    series_number = first_series.get("position")
                else:
                    series_name = metadata.get("seriesName")
                    series_id = None
                    series_number = metadata.get("seriesNumber")
                
                # Get genres from taggings
                taggings = hardcover_book_data.get("taggings", [])
                genres = ", ".join([t.get("tag", {}).get("tag", "") for t in taggings if t.get("tag", {}).get("tag")])
                
                # Get contributors
                contributions = hardcover_book_data.get("contributions", [])
                if contributions:
                    author_names = [c.get("author", {}).get("name") for c in contributions if c.get("author", {}).get("name")]
                    if author_names:
                        author = ", ".join(author_names)
            else:
                # Use Booklore data
                description = metadata.get("description")
                booklore_book_id = bl_book.get("id")
                cover_url = metadata.get("thumbnailUrl")
                if not cover_url and booklore_book_id and booklore_server:
                    base_url = booklore_server.url.rstrip('/')
                    cover_url = f"{base_url}/api/v1/book/{booklore_book_id}/cover"
                page_count = metadata.get("pageCount")
                published_date = str(metadata.get("publishedDate") or "")
                series_name = metadata.get("seriesName")
                series_id = None
                series_number = metadata.get("seriesNumber")
                rating = None
                ratings_count = None
                users_count = None
                genres = None
            
            isbn = metadata.get("isbn13") or metadata.get("isbn10")
            
            # Check if we have this book locally - try multiple identifiers for upsert
            db_book = None
            
            # Try hardcover_id first (most reliable)
            if hardcover_id_int:
                db_book = db.query(Book).filter(Book.hardcover_id == hardcover_id_int).first()
            
            # Try slug if not found
            if not db_book and hardcover_slug:
                db_book = db.query(Book).filter(Book.hardcover_slug == hardcover_slug).first()
            
            # Try ISBN if still not found
            if not db_book and isbn:
                db_book = db.query(Book).filter(Book.isbn == isbn).first()
            
            # Try title + author match as last resort to prevent duplicates
            if not db_book and title:
                db_book = db.query(Book).filter(
                    Book.title == title,
                    Book.author == author
                ).first()
                if db_book:
                    logger.debug("book_matched_by_title_author",
                               title=title,
                               author=author,
                               book_id=db_book.id)
            
            if db_book:
                # UPDATE existing book with new data
                db_book.title = title
                db_book.author = author
                db_book.description = description or db_book.description
                db_book.cover_url = cover_url or db_book.cover_url
                
                # Only update ISBN if it won't cause a conflict
                if isbn and isbn != db_book.isbn:
                    existing_with_isbn = db.query(Book).filter(
                        Book.isbn == isbn,
                        Book.id != db_book.id
                    ).first()
                    if not existing_with_isbn:
                        db_book.isbn = isbn
                
                db_book.page_count = page_count or db_book.page_count
                db_book.published_date = published_date or db_book.published_date
                db_book.hardcover_id = hardcover_id_int  # Update with proper ID
                db_book.hardcover_slug = hardcover_slug or db_book.hardcover_slug
                if booklore_id:
                    db_book.booklore_id = booklore_id
                if booklore_added_on:
                    db_book.booklore_added_on = booklore_added_on
                db_book.series = series_name or db_book.series
                if hardcover_book_data and series_id:
                    db_book.series_id = series_id
                db_book.series_position = series_number or db_book.series_position
                if rating is not None:
                    db_book.rating = rating
                if ratings_count is not None:
                    db_book.ratings_count = ratings_count
                if users_count is not None:
                    db_book.users_count = users_count
                if genres:
                    db_book.genres = genres
                # Set format-specific availability based on book type from Booklore
                if format_type == "audiobook":
                    db_book.audiobook_available = True
                else:
                    db_book.ebook_available = True
                db_book.last_refreshed = datetime.now(timezone.utc)
                db.add(db_book)
                
                try:
                    db.flush()
                    books_updated += 1
                    logger.info("booklore_book_updated",
                              hardcover_id=hardcover_id_int,
                              hardcover_slug=hardcover_slug,
                              title=title,
                              has_hardcover_data=bool(hardcover_book_data))
                except Exception as flush_error:
                    db.rollback()
                    logger.warning("booklore_book_update_failed",
                                 hardcover_id=hardcover_id_int,
                                 title=title,
                                 error=str(flush_error))
                    # Re-fetch the book after rollback
                    db_book = db.query(Book).filter(Book.hardcover_id == hardcover_id_int).first()
                    if not db_book:
                        continue
            else:
                # CREATE new book - but first do final duplicate checks
                
                # Final check: ensure hardcover_id doesn't already exist
                if hardcover_id_int:
                    existing_by_hc_id = db.query(Book).filter(Book.hardcover_id == hardcover_id_int).first()
                    if existing_by_hc_id:
                        db_book = existing_by_hc_id
                        if booklore_id:
                            db_book.booklore_id = booklore_id
                        if booklore_added_on:
                            db_book.booklore_added_on = booklore_added_on
                        books_updated += 1
                        logger.info("booklore_book_found_by_hardcover_id_final_check",
                                  hardcover_id=hardcover_id_int,
                                  title=title,
                                  existing_id=existing_by_hc_id.id)
                        # Skip to request handling
                    else:
                        # Check ISBN conflict
                        if isbn:
                            existing_with_isbn = db.query(Book).filter(Book.isbn == isbn).first()
                            if existing_with_isbn:
                                # Use existing book instead of creating duplicate
                                db_book = existing_with_isbn
                                db_book.hardcover_id = hardcover_id_int
                                db_book.hardcover_slug = hardcover_slug
                                if booklore_id:
                                    db_book.booklore_id = booklore_id
                                if booklore_added_on:
                                    db_book.booklore_added_on = booklore_added_on
                                db.add(db_book)
                                db.flush()
                                books_updated += 1
                                logger.info("booklore_book_linked_by_isbn",
                                          hardcover_id=hardcover_id_int,
                                          isbn=isbn,
                                          title=title)
                            else:
                                # Create new book with ISBN
                                db_book = create_book_from_booklore_data(
                                    title=title,
                                    author=author,
                                    description=description,
                                    cover_url=cover_url,
                                    isbn=isbn,
                                    page_count=page_count,
                                    published_date=published_date,
                                    hardcover_id=hardcover_id_int,
                                    hardcover_slug=hardcover_slug,
                                    booklore_id=booklore_id,
                                    booklore_added_on=booklore_added_on,
                                    series_name=series_name,
                                    series_id=series_id if hardcover_book_data else None,
                                    series_number=series_number,
                                    rating=rating,
                                    ratings_count=ratings_count,
                                    users_count=users_count,
                                    genres=genres,
                                    format_type=format_type,
                                )
                                db.add(db_book)
                                db.flush()
                                books_created += 1
                                logger.info("booklore_book_created",
                                          hardcover_id=hardcover_id_int,
                                          hardcover_slug=hardcover_slug,
                                          title=title,
                                          format_type=format_type,
                                          has_hardcover_data=bool(hardcover_book_data))
                        else:
                            # Create new book without ISBN
                            db_book = create_book_from_booklore_data(
                                title=title,
                                author=author,
                                description=description,
                                cover_url=cover_url,
                                isbn=None,
                                page_count=page_count,
                                published_date=published_date,
                                hardcover_id=hardcover_id_int,
                                hardcover_slug=hardcover_slug,
                                booklore_id=booklore_id,
                                booklore_added_on=booklore_added_on,
                                series_name=series_name,
                                series_id=series_id if hardcover_book_data else None,
                                series_number=series_number,
                                rating=rating,
                                ratings_count=ratings_count,
                                users_count=users_count,
                                genres=genres,
                                format_type=format_type,
                            )
                            db.add(db_book)
                            db.flush()
                            books_created += 1
                            logger.info("booklore_book_created",
                                      hardcover_id=hardcover_id_int,
                                      hardcover_slug=hardcover_slug,
                                      title=title,
                                      has_hardcover_data=bool(hardcover_book_data))
                else:
                    # No hardcover_id - skip creating book without proper identifier
                    logger.warning("booklore_book_skipped_no_hardcover_id_for_creation",
                                 title=title,
                                 author=author)
                    skipped_count += 1
                    continue
            
            # Check if we have an existing request for this book matching the format
            # Update to "available" if the book is now in Booklore
            existing_request = db.query(BookRequest).filter(
                BookRequest.book_id == db_book.id,
                BookRequest.format == format_type  # Match the format from Booklore
            ).first()
            
            if existing_request and existing_request.status in ("processing", "approved", "pending"):
                existing_request.status = "available"
                existing_request.updated_at = datetime.now(timezone.utc)
                updated_count += 1
                logger.info("booklore_request_updated_to_available",
                          hardcover_id=hardcover_id_int,
                          request_id=existing_request.id,
                          title=title,
                          format=format_type)
            
            # Commit after each book to avoid long-running transactions and database locks
            try:
                db.commit()
            except Exception as commit_error:
                logger.warning("booklore_book_commit_failed",
                             title=title,
                             error=str(commit_error))
                db.rollback()
        
        logger.info("sync_from_booklore_complete",
                   booklore_books=len(booklore_books),
                   books_created=books_created,
                   books_updated=books_updated,
                   requests_updated=updated_count,
                   skipped=skipped_count)
        
    except Exception as e:
        logger.error("sync_from_booklore_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def run_background_request_check():
    """Background task to check processing requests periodically"""
    job_name = "check_processing_requests"
    
    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_request_check_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)
    
    while True:
        try:
            await check_processing_requests()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_request_check_error", error=str(e))
        
        # Get interval from database (default 5 minutes)
        interval = get_job_interval_standalone(job_name, 5 * 60)
        logger.debug("background_request_check_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)


async def run_background_booklore_sync():
    """Background task to sync from Booklore periodically"""
    job_name = "sync_from_booklore"
    
    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_booklore_sync_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)
    
    while True:
        try:
            await sync_from_booklore()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_booklore_sync_error", error=str(e))
        
        # Get interval from database (default 24 hours)
        interval = get_job_interval_standalone(job_name, 24 * 60 * 60)
        logger.debug("background_booklore_sync_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)


async def sync_from_audiobookshelf():
    """
    Sync audiobook availability from Audiobookshelf.
    - Imports audiobooks from Audiobookshelf into the local database
    - Updates existing books with audiobookshelf_id links
    - Marks matching requests as "available"
    """
    from app.routers.audiobookshelf import (
        get_default_audiobookshelf_server,
        get_all_audiobookshelf_items,
        match_book_to_abs_item,
    )
    from app.routers.hardcover import lookup_book_by_title_author
    from app.models import BookRequest, User

    db: Session = SessionLocal()
    try:
        abs_server = get_default_audiobookshelf_server(db)

        if not abs_server:
            logger.info("sync_from_audiobookshelf_skipped", reason="no_audiobookshelf_server")
            return

        logger.info("sync_from_audiobookshelf_starting", server_name=abs_server.name)

        items = await get_all_audiobookshelf_items(abs_server)

        if not items:
            logger.info("sync_from_audiobookshelf_skipped", reason="no_items_in_audiobookshelf")
            return

        logger.info("sync_from_audiobookshelf_fetched", count=len(items))

        updated_count = 0
        skipped_count = 0
        books_created = 0
        books_updated = 0

        for item in items:
            item_id = item.get("id")
            media = item.get("media", {})
            metadata = media.get("metadata", {})

            title = metadata.get("title") or "Unknown Title"
            author = metadata.get("authorName") or "Unknown Author"
            isbn = metadata.get("isbn")

            # Fast path: already linked by audiobookshelf_id
            if item_id:
                existing_by_abs_id = db.query(Book).filter(Book.audiobookshelf_id == item_id).first()
                if existing_by_abs_id:
                    existing_by_abs_id.audiobook_available = True
                    existing_by_abs_id.last_refreshed = datetime.now(timezone.utc)
                    db.add(existing_by_abs_id)

                    existing_request = db.query(BookRequest).filter(
                        BookRequest.book_id == existing_by_abs_id.id,
                        BookRequest.format == "audiobook"
                    ).first()
                    if existing_request and existing_request.status in ("processing", "approved", "pending"):
                        existing_request.status = "available"
                        existing_request.updated_at = datetime.now(timezone.utc)
                        updated_count += 1

                    try:
                        db.commit()
                    except Exception as commit_error:
                        logger.warning("audiobookshelf_book_commit_failed",
                                     title=existing_by_abs_id.title,
                                     error=str(commit_error))
                        db.rollback()
                    continue

            # Try ISBN match
            db_book = None
            if isbn:
                db_book = db.query(Book).filter(Book.isbn == isbn).first()

            # Try title + author match. Fast exact path first, then the
            # normalized matcher (tolerates stripped punctuation / a missing
            # subtitle / "Last, First" authors) over a surname-narrowed set.
            if not db_book and title and author:
                db_book = db.query(Book).filter(
                    sa_func.lower(Book.title) == title.lower(),
                    sa_func.lower(Book.author) == author.lower()
                ).first()

            if not db_book and title and author:
                parts = author.replace(",", " ").split()
                surname = parts[-1] if parts else ""
                if len(surname) >= 2:
                    candidates = db.query(Book).filter(
                        sa_func.lower(Book.author).like(f"%{surname.lower()}%")
                    ).all()
                    db_book = next(
                        (b for b in candidates if match_book_to_abs_item(b, item)),
                        None,
                    )

            # Try Hardcover lookup by title+author
            if not db_book:
                try:
                    hardcover_data = await lookup_book_by_title_author(title, author, db)
                    await asyncio.sleep(0.5)

                    if hardcover_data:
                        hardcover_id_int = hardcover_data.get("id")
                        if hardcover_id_int:
                            db_book = db.query(Book).filter(Book.hardcover_id == hardcover_id_int).first()

                            if not db_book:
                                # Create new book from Hardcover data
                                description = hardcover_data.get("description")
                                cached_image = hardcover_data.get("cached_image")
                                cover_url = cached_image.get("url") if isinstance(cached_image, dict) else None
                                page_count = hardcover_data.get("pages")
                                published_date = hardcover_data.get("release_date") or str(hardcover_data.get("release_year", ""))
                                rating = hardcover_data.get("rating")
                                hardcover_slug = hardcover_data.get("slug")

                                # Series info
                                series_name = None
                                series_id = None
                                series_position = None
                                book_series = hardcover_data.get("book_series", [])
                                if book_series:
                                    first_series = book_series[0]
                                    series_info = first_series.get("series", {})
                                    series_name = series_info.get("name")
                                    series_id = series_info.get("id")
                                    series_position = first_series.get("position")

                                # Contributors
                                contributions = hardcover_data.get("contributions", [])
                                if contributions:
                                    author_names = [c.get("author", {}).get("name") for c in contributions if c.get("author", {}).get("name")]
                                    if author_names:
                                        author = ", ".join(author_names)

                                # Genres
                                taggings = hardcover_data.get("taggings", [])
                                genres = ", ".join([t.get("tag", {}).get("tag", "") for t in taggings if t.get("tag", {}).get("tag")])

                                db_book = Book(
                                    title=title,
                                    author=author,
                                    description=description,
                                    cover_url=cover_url,
                                    isbn=isbn,
                                    page_count=page_count,
                                    published_date=published_date,
                                    hardcover_id=hardcover_id_int,
                                    hardcover_slug=hardcover_slug,
                                    audiobookshelf_id=item_id,
                                    series=series_name,
                                    series_id=series_id,
                                    series_position=series_position,
                                    rating=rating,
                                    ratings_count=hardcover_data.get("ratings_count"),
                                    users_count=hardcover_data.get("users_count"),
                                    genres=genres or None,
                                    audiobook_available=True,
                                )
                                db.add(db_book)
                                try:
                                    db.flush()
                                    books_created += 1
                                    logger.info("audiobookshelf_book_created",
                                              hardcover_id=hardcover_id_int,
                                              title=title)
                                except Exception as flush_error:
                                    db.rollback()
                                    logger.warning("audiobookshelf_book_create_failed",
                                                 title=title,
                                                 error=str(flush_error))
                                    continue
                except Exception as e:
                    logger.warning("audiobookshelf_hardcover_lookup_failed",
                                 title=title,
                                 error=str(e))

            if not db_book:
                skipped_count += 1
                continue

            # Update existing book
            if item_id:
                db_book.audiobookshelf_id = item_id
            db_book.audiobook_available = True
            db_book.last_refreshed = datetime.now(timezone.utc)
            db.add(db_book)
            books_updated += 1

            # Update matching requests
            existing_request = db.query(BookRequest).filter(
                BookRequest.book_id == db_book.id,
                BookRequest.format == "audiobook"
            ).first()

            if existing_request and existing_request.status in ("processing", "approved", "pending"):
                existing_request.status = "available"
                existing_request.updated_at = datetime.now(timezone.utc)
                updated_count += 1

            try:
                db.commit()
            except Exception as commit_error:
                logger.warning("audiobookshelf_book_commit_failed",
                             title=title,
                             error=str(commit_error))
                db.rollback()

        if updated_count or books_created or books_updated:
            from app.cache import clear_cache_pattern
            await clear_cache_pattern("requests_by_hardcover:*")
            await clear_cache_pattern("requests_by_hardcover_batch:*")

        logger.info("sync_from_audiobookshelf_complete",
                   audiobookshelf_items=len(items),
                   books_created=books_created,
                   books_updated=books_updated,
                   requests_updated=updated_count,
                   skipped=skipped_count)

    except Exception as e:
        logger.error("sync_from_audiobookshelf_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def run_background_metadata_sync():
    """Background task to sync missing metadata periodically"""
    job_name = "sync_missing_metadata"
    
    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_metadata_sync_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)
    
    while True:
        try:
            await sync_missing_metadata()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_metadata_sync_error", error=str(e))
        
        # Get interval from database (default 6 hours)
        interval = get_job_interval_standalone(job_name, 6 * 60 * 60)
        logger.debug("background_metadata_sync_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)


def get_job_interval(job_name: str, db: Session) -> int:
    """Get job interval from database, falling back to defaults"""
    from app.models import JobSchedule
    
    defaults = {
        "refresh_seed_data": 24 * 60 * 60,
        "check_processing_requests": 5 * 60,
        "sync_from_booklore": 24 * 60 * 60,
        "sync_from_audiobookshelf": 24 * 60 * 60,
        "sync_missing_metadata": 6 * 60 * 60,
    }
    
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule:
            return schedule.interval_seconds
    except Exception:
        pass
    
    return defaults.get(job_name, 3600)


async def sync_missing_metadata():
    """
    Find books in the database that are missing metadata (no hardcover_id, 
    no cover, no rating, etc.) and look them up on Hardcover.
    """
    from app.routers.hardcover import lookup_book_by_slug, lookup_book_by_title_author
    
    db: Session = SessionLocal()
    try:
        # Find books missing key metadata
        # Priority 1: Books with hardcover_slug but no hardcover_id (numeric)
        # Priority 2: Books without cover_url
        # Priority 3: Books without rating
        
        books_with_slug_no_id = db.query(Book).filter(
            Book.hardcover_slug.isnot(None),
            Book.hardcover_id.is_(None)
        ).all()
        
        books_without_cover = db.query(Book).filter(
            Book.cover_url.is_(None),
            Book.hardcover_id.is_(None)
        ).limit(50).all()  # Limit to avoid too many API calls
        
        books_without_rating = db.query(Book).filter(
            Book.rating.is_(None),
            Book.hardcover_id.isnot(None)
        ).limit(50).all()
        
        # Books with hardcover_id but missing series_id (and have a series name or position)
        books_without_series_id = db.query(Book).filter(
            Book.hardcover_id.isnot(None),
            Book.series_id.is_(None),
            or_(Book.series.isnot(None), Book.series_position.isnot(None))
        ).limit(50).all()
        
        # Combine and dedupe
        all_books = {b.id: b for b in books_with_slug_no_id + books_without_cover + books_without_rating + books_without_series_id}
        
        if not all_books:
            logger.info("sync_missing_metadata_skipped", reason="no_books_need_update")
            return
        
        logger.info("sync_missing_metadata_starting", 
                   books_count=len(all_books),
                   with_slug_no_id=len(books_with_slug_no_id),
                   without_cover=len(books_without_cover),
                   without_rating=len(books_without_rating),
                   without_series_id=len(books_without_series_id))
        
        updated_count = 0
        failed_count = 0
        skipped_duplicates = 0
        
        for book_id, book in all_books.items():
            try:
                hardcover_data = None
                
                # Try slug first
                if book.hardcover_slug:
                    hardcover_data = await lookup_book_by_slug(book.hardcover_slug, db)
                    await asyncio.sleep(0.5)  # Rate limit protection
                
                # Try title/author if no data yet
                if not hardcover_data and book.title:
                    hardcover_data = await lookup_book_by_title_author(book.title, book.author, db)
                    await asyncio.sleep(0.5)  # Rate limit protection
                
                if hardcover_data:
                    new_hardcover_id = hardcover_data.get("id")
                    
                    # Check if another book already has this hardcover_id
                    if new_hardcover_id:
                        existing = db.query(Book).filter(
                            Book.hardcover_id == new_hardcover_id,
                            Book.id != book.id
                        ).first()
                        
                        if existing:
                            # Another book has this ID - skip this one (it's a duplicate)
                            logger.info("book_skipped_duplicate_hardcover_id",
                                      book_id=book.id,
                                      hardcover_id=new_hardcover_id,
                                      title=book.title,
                                      existing_book_id=existing.id,
                                      existing_title=existing.title)
                            skipped_duplicates += 1
                            continue
                    
                    # Update book with Hardcover data
                    book.hardcover_id = new_hardcover_id
                    book.hardcover_slug = hardcover_data.get("slug") or book.hardcover_slug
                    
                    # Update cover
                    cached_image = hardcover_data.get("cached_image")
                    if cached_image and isinstance(cached_image, dict):
                        book.cover_url = cached_image.get("url") or book.cover_url
                    
                    # Update other metadata
                    book.description = hardcover_data.get("description") or book.description
                    book.page_count = hardcover_data.get("pages") or book.page_count
                    book.rating = hardcover_data.get("rating") or book.rating
                    book.ratings_count = hardcover_data.get("ratings_count") or book.ratings_count
                    book.users_count = hardcover_data.get("users_count") or book.users_count
                    
                    # Update series info
                    book_series = hardcover_data.get("book_series", [])
                    if book_series and len(book_series) > 0:
                        first_series = book_series[0]
                        series_info = first_series.get("series", {})
                        book.series = series_info.get("name") or book.series
                        book.series_id = series_info.get("id") or book.series_id
                        book.series_position = first_series.get("position") or book.series_position
                    
                    # Update genres
                    taggings = hardcover_data.get("taggings", [])
                    if taggings:
                        genres = ", ".join([t.get("tag", {}).get("tag", "") for t in taggings if t.get("tag", {}).get("tag")])
                        if genres:
                            book.genres = genres
                    
                    book.last_refreshed = datetime.now(timezone.utc)
                    db.add(book)
                    
                    try:
                        db.commit()
                        updated_count += 1
                        logger.debug("book_metadata_updated",
                                   book_id=book.id,
                                   hardcover_id=book.hardcover_id,
                                   title=book.title)
                    except Exception as commit_error:
                        db.rollback()
                        logger.warning("book_metadata_commit_failed",
                                     book_id=book.id,
                                     error=str(commit_error))
                        failed_count += 1
                else:
                    logger.debug("book_not_found_on_hardcover",
                               book_id=book.id,
                               title=book.title,
                               slug=book.hardcover_slug)
                    failed_count += 1
                    
            except Exception as e:
                logger.warning("sync_missing_metadata_book_error",
                             book_id=book.id,
                             title=book.title,
                             error=str(e))
                failed_count += 1
                db.rollback()
        
        logger.info("sync_missing_metadata_complete",
                   total_books=len(all_books),
                   updated=updated_count,
                   skipped_duplicates=skipped_duplicates,
                   failed=failed_count)

    except Exception as e:
        logger.error("sync_missing_metadata_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def sync_download_states():
    """
    Background task to sync download states from download clients.
    Updates orphaned downloads that lost their handler threads after backend restart.
    """
    from app.models import DownloadTask, DownloadClient
    from app.downloads.clients.qbittorrent import QBittorrentClient
    from app.downloads.clients.nzbget import NZBGetClient

    db: Session = SessionLocal()
    try:
        logger.info("sync_download_states_starting")

        # Get all active download tasks that might need syncing
        tasks = db.query(DownloadTask).filter(
            DownloadTask.state.in_(['downloading', 'queued', 'checking', 'paused'])
        ).all()

        if not tasks:
            logger.info("sync_download_states_no_tasks")
            return

        logger.info("sync_download_states_found_tasks", count=len(tasks))

        # Group tasks by protocol
        torrent_tasks = [t for t in tasks if t.protocol == 'torrent']
        usenet_tasks = [t for t in tasks if t.protocol == 'usenet']

        updated_count = 0

        # Sync torrent downloads
        if torrent_tasks:
            updated_count += await _sync_torrent_downloads(db, torrent_tasks)

        # Sync usenet downloads
        if usenet_tasks:
            updated_count += await _sync_usenet_downloads(db, usenet_tasks)

        logger.info("sync_download_states_complete",
                   total_tasks=len(tasks),
                   updated=updated_count)

    except Exception as e:
        logger.error("sync_download_states_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def _sync_torrent_downloads(db: Session, tasks: list) -> int:
    """Sync torrent download states from the configured torrent client"""
    from app.models import DownloadClient
    from app.downloads.clients.qbittorrent import QBittorrentClient
    from app.downloads.clients.transmission import TransmissionClient

    try:
        # Get highest-priority enabled torrent client
        client_config = db.query(DownloadClient).filter(
            DownloadClient.protocol == 'torrent',
            DownloadClient.enabled == True
        ).order_by(DownloadClient.priority.desc()).first()

        if not client_config:
            logger.warning("sync_torrents_no_client")
            return 0

        if client_config.type == 'transmission':
            return await _sync_transmission_downloads(db, tasks, client_config)

        # Connect to client
        client = QBittorrentClient(
            host=client_config.host,
            port=client_config.port,
            username=client_config.username,
            password=client_config.password,
            use_ssl=client_config.use_ssl,
            url_base=client_config.url_base,
        )

        if not client.test_connection():
            logger.error("sync_torrents_connection_failed")
            return 0

        # Get all torrents from client
        all_torrents = client.client.torrents_info()
        torrent_map = {t.hash.lower(): t for t in all_torrents}

        logger.info("sync_torrents_fetched", count=len(all_torrents))

        updated = 0
        for task in tasks:
            if not task.info_hash:
                continue

            torrent_hash = task.info_hash.lower()
            if torrent_hash in torrent_map:
                torrent = torrent_map[torrent_hash]

                # Map qBittorrent state to our state
                old_state = task.state
                old_client_state = task.client_state

                qb_state = torrent.state.lower()
                if 'error' in qb_state or 'missing' in qb_state:
                    task.state = 'error'
                    task.message = f"qBittorrent error: {torrent.state}"
                elif qb_state in ['pauseddl', 'pausedup']:
                    task.state = 'paused'
                elif qb_state in ['queueddl', 'queuedup']:
                    task.state = 'queued'
                elif qb_state in ['checkingdl', 'checkingup', 'checkingresumedata']:
                    task.state = 'checking'
                elif qb_state in ['downloading', 'metadl', 'forceddl']:
                    task.state = 'downloading'
                elif qb_state in ['uploading', 'forcedup', 'stalledup']:
                    task.state = 'seeding'
                elif torrent.progress >= 1.0:
                    task.state = 'complete'
                    if not task.completed_at:
                        task.completed_at = datetime.now(timezone.utc)

                # Update client_state and progress
                task.client_state = torrent.state
                task.progress = torrent.progress * 100

                if old_state != task.state or old_client_state != task.client_state:
                    logger.info("sync_torrent_updated",
                               task_id=task.id,
                               old_state=old_state,
                               new_state=task.state,
                               old_client_state=old_client_state,
                               new_client_state=task.client_state,
                               progress=task.progress)
                    updated += 1

        db.commit()
        return updated

    except Exception as e:
        logger.error("sync_torrents_error", error=str(e))
        return 0


async def _sync_transmission_downloads(db: Session, tasks: list, client_config) -> int:
    """Sync torrent download states from Transmission"""
    from app.downloads.clients.transmission import TransmissionClient
    from app.downloads import DownloadState

    # Map normalized DownloadState -> our task.state string
    state_map = {
        DownloadState.DOWNLOADING: 'downloading',
        DownloadState.SEEDING: 'seeding',
        DownloadState.COMPLETE: 'complete',
        DownloadState.PAUSED: 'paused',
        DownloadState.CHECKING: 'checking',
        DownloadState.QUEUED: 'queued',
        DownloadState.ERROR: 'error',
    }

    try:
        client = TransmissionClient(
            host=client_config.host,
            port=client_config.port,
            username=client_config.username,
            password=client_config.password,
            use_ssl=client_config.use_ssl,
            url_base=client_config.url_base,
        )

        if not client.test_connection():
            logger.error("sync_transmission_connection_failed")
            return 0

        updated = 0
        for task in tasks:
            if not task.info_hash:
                continue

            status = client.get_download_status(task.info_hash)
            client_state = status.get("client_state")
            if not client_state:
                # Torrent not found in Transmission - leave the task untouched
                continue

            old_state = task.state
            old_client_state = task.client_state

            progress = status.get("progress", 0.0)
            new_state = state_map.get(status.get("state"), task.state)

            task.state = new_state
            task.client_state = client_state
            task.progress = progress

            if new_state == 'complete' and not task.completed_at:
                task.completed_at = datetime.now(timezone.utc)

            if old_state != task.state or old_client_state != task.client_state:
                logger.info("sync_transmission_updated",
                            task_id=task.id,
                            old_state=old_state,
                            new_state=task.state,
                            old_client_state=old_client_state,
                            new_client_state=task.client_state,
                            progress=task.progress)
                updated += 1

        db.commit()
        return updated

    except Exception as e:
        logger.error("sync_transmission_error", error=str(e))
        return 0


async def _sync_usenet_downloads(db: Session, tasks: list) -> int:
    """Sync usenet download states from NZBGet"""
    from app.models import DownloadClient
    from app.downloads.clients.nzbget import NZBGetClient

    try:
        # Get enabled NZBGet client
        client_config = db.query(DownloadClient).filter(
            DownloadClient.type == 'nzbget',
            DownloadClient.enabled == True
        ).first()

        if not client_config:
            logger.warning("sync_usenet_no_client")
            return 0

        # Connect to client
        client = NZBGetClient(
            host=client_config.host,
            port=client_config.port,
            username=client_config.username,
            password=client_config.password,
            use_ssl=client_config.use_ssl,
            url_base=client_config.url_base,
        )

        if not client.test_connection():
            logger.error("sync_usenet_connection_failed")
            return 0

        # Get all downloads from client
        all_downloads = client.get_all_downloads()
        download_map = {d['nzb_id']: d for d in all_downloads}

        logger.info("sync_usenet_fetched", count=len(all_downloads))

        updated = 0
        for task in tasks:
            if not task.info_hash:
                continue

            if task.info_hash in download_map:
                download = download_map[task.info_hash]

                # Map NZBGet state to our state
                old_state = task.state
                old_client_state = task.client_state

                nzbget_state = download['state']
                if nzbget_state in ['ERROR', 'FAILED']:
                    task.state = 'error'
                    task.message = download.get('message', 'NZBGet error')
                elif nzbget_state == 'PAUSED':
                    task.state = 'paused'
                elif nzbget_state == 'QUEUED':
                    task.state = 'queued'
                elif nzbget_state == 'DOWNLOADING':
                    task.state = 'downloading'
                elif nzbget_state in ['POST_PROCESSING', 'EXTRACTING']:
                    task.state = 'checking'
                elif nzbget_state == 'SUCCESS':
                    task.state = 'complete'
                    if not task.completed_at:
                        task.completed_at = datetime.now(timezone.utc)

                # Update client_state and progress
                task.client_state = nzbget_state
                task.progress = download['progress']

                if old_state != task.state or old_client_state != task.client_state:
                    logger.info("sync_usenet_updated",
                               task_id=task.id,
                               old_state=old_state,
                               new_state=task.state,
                               old_client_state=old_client_state,
                               new_client_state=task.client_state,
                               progress=task.progress)
                    updated += 1

        db.commit()
        return updated

    except Exception as e:
        logger.error("sync_usenet_error", error=str(e))
        return 0


# ---------------------------------------------------------------------------
# Hardcover list / to-read sync
# ---------------------------------------------------------------------------

_TO_READ_QUERY = """
{
  me {
    user_books(where: {status_id: {_eq: 1}}) {
      book {
        id
        title
      }
    }
  }
}
"""

_LIST_BOOKS_QUERY = """
query GetListBooks($list_id: Int!) {
  list_books(where: {list_id: {_eq: $list_id}}) {
    book_id
  }
}
"""

_GET_BOOK_QUERY = """
query GetBook($id: Int!) {
  books_by_pk(id: $id) {
    id
    title
    slug
    release_year
    release_date
    pages
    description
    cached_image
    cached_contributors
    rating
    ratings_count
    users_count
    activities_count
    default_ebook_edition_id
    default_audio_edition_id
    default_physical_edition_id
    book_series {
      position
      series {
        id
        name
      }
    }
    contributions {
      author {
        id
        name
        slug
      }
    }
    taggings(limit: 10) {
      tag {
        tag
      }
    }
  }
}
"""


async def _hardcover_graphql(query: str, variables: dict, token: str) -> dict:
    """Execute a Hardcover GraphQL query with a specific token."""
    import httpx
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://api.hardcover.app/v1/graphql",
            headers=headers,
            json={"query": query, "variables": variables or {}},
        )
    if not response.is_success:
        logger.warning("hardcover_sync_request_failed", status=response.status_code)
        return {}
    data = response.json()
    if "errors" in data:
        logger.warning("hardcover_sync_graphql_errors", errors=data["errors"])
        return {}
    return data.get("data", {})


async def _ensure_book_in_db(hardcover_id: int, token: str, db: Session) -> Optional[Any]:
    """Return the local Book for a Hardcover ID, creating it from the API if needed."""
    from app.routers.hardcover import _parse_hardcover_book

    existing = db.query(Book).filter(Book.hardcover_id == hardcover_id).first()
    if existing:
        return existing

    # Fetch from Hardcover
    data = await _hardcover_graphql(_GET_BOOK_QUERY, {"id": hardcover_id}, token)
    book_data = data.get("books_by_pk")
    if not book_data:
        return None

    try:
        parsed = _parse_hardcover_book(book_data)
    except Exception as e:
        logger.warning("hardcover_sync_parse_failed", hardcover_id=hardcover_id, error=str(e))
        return None

    cover_url = None
    if parsed.cached_image and isinstance(parsed.cached_image, dict):
        cover_url = parsed.cached_image.get("url")

    author = None
    if parsed.contributions:
        author = parsed.contributions[0].author.name if parsed.contributions else None

    genres = ",".join(t.tag.tag for t in (parsed.taggings or []) if t.tag) or None

    db_book = Book(
        title=parsed.title,
        author=author,
        hardcover_id=parsed.id,
        hardcover_slug=parsed.slug,
        cover_url=cover_url,
        description=parsed.description,
        page_count=parsed.pages,
        rating=parsed.rating,
        ratings_count=parsed.ratings_count,
        users_count=parsed.users_count,
        genres=genres,
        release_year=parsed.release_year,
        is_seed_data=False,
    )
    db.add(db_book)
    try:
        db.commit()
        db.refresh(db_book)
        logger.info("hardcover_sync_book_created", hardcover_id=hardcover_id, title=parsed.title)
        return db_book
    except Exception as e:
        db.rollback()
        logger.warning("hardcover_sync_book_create_failed", hardcover_id=hardcover_id, error=str(e))
        return None


async def sync_hardcover_lists_for_user(user_id: int) -> None:
    """Sync Hardcover to-read / lists for a single user and create pending requests."""
    from app.models import UserHardcoverSync, BookRequest, User
    from app.encryption import decrypt_value
    import json

    db: Session = SessionLocal()
    try:
        config = db.query(UserHardcoverSync).filter(
            UserHardcoverSync.user_id == user_id
        ).first()
        if not config or not config.is_enabled:
            return

        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.is_active:
            return

        # Use personal token if set, otherwise fall back to the global app token
        if config.hardcover_api_token:
            token = decrypt_value(config.hardcover_api_token)
        else:
            from app.routers.settings import get_hardcover_token as _get_global_token
            token, _ = _get_global_token(db)
        if not token:
            logger.warning("hardcover_sync_no_token", user_id=user_id)
            return
        hardcover_ids: set = set()

        # Collect to-read books
        if config.sync_to_read:
            data = await _hardcover_graphql(_TO_READ_QUERY, {}, token)
            for ub in data.get("me", [])[0].get("user_books", []):
                bid = ub.get("book", {}).get("id")
                if bid:
                    hardcover_ids.add(int(bid))

        # Collect list books
        list_ids = json.loads(config.sync_list_ids or "[]")
        for list_id in list_ids:
            data = await _hardcover_graphql(_LIST_BOOKS_QUERY, {"list_id": list_id}, token)
            for lb in data.get("list_books", []):
                bid = lb.get("book_id")
                if bid:
                    hardcover_ids.add(int(bid))

        formats = (
            ["ebook", "audiobook"] if config.default_format == "both"
            else [config.default_format or "ebook"]
        )

        requested = 0
        skipped = 0

        for hc_id in hardcover_ids:
            db_book = await _ensure_book_in_db(hc_id, token, db)
            if not db_book:
                skipped += 1
                continue

            for fmt in formats:
                # Skip if user lacks permission
                if fmt == "ebook" and not user.can_request_ebook:
                    continue
                if fmt == "audiobook" and not user.can_request_audiobook:
                    continue

                # Skip if already in library
                if fmt == "ebook" and db_book.ebook_available:
                    continue
                if fmt == "audiobook" and db_book.audiobook_available:
                    continue

                # Skip if a non-denied request already exists
                existing = db.query(BookRequest).filter(
                    BookRequest.book_id == db_book.id,
                    BookRequest.format == fmt,
                    BookRequest.status != "denied",
                ).first()
                if existing:
                    continue

                initial_status = "approved" if (
                    (fmt == "ebook" and user.auto_approve_ebooks) or
                    (fmt == "audiobook" and user.auto_approve_audiobooks)
                ) else "pending"

                db_request = BookRequest(
                    book_id=db_book.id,
                    user_id=user.id,
                    format=fmt,
                    status=initial_status,
                    source="hardcover_sync",
                )
                db.add(db_request)
                try:
                    db.commit()
                    db.refresh(db_request)
                    requested += 1
                    logger.info(
                        "hardcover_sync_request_created",
                        user_id=user.id,
                        book_id=db_book.id,
                        hardcover_id=hc_id,
                        format=fmt,
                        status=initial_status,
                    )
                except Exception as e:
                    db.rollback()
                    logger.warning("hardcover_sync_request_failed", book_id=db_book.id, error=str(e))

            await asyncio.sleep(0.2)  # gentle rate limit

        # Update last synced timestamp
        config.last_synced_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            "hardcover_sync_user_complete",
            user_id=user_id,
            hardcover_ids=len(hardcover_ids),
            requested=requested,
            skipped=skipped,
        )
    except Exception as e:
        logger.error("hardcover_sync_user_error", user_id=user_id, error=str(e))
    finally:
        db.close()


async def sync_hardcover_lists() -> None:
    """Global job: sync Hardcover lists for all users with sync enabled."""
    from app.models import UserHardcoverSync

    db: Session = SessionLocal()
    try:
        configs = db.query(UserHardcoverSync).filter(
            UserHardcoverSync.is_enabled == True,
        ).all()
        user_ids = [c.user_id for c in configs]
    finally:
        db.close()

    logger.info("hardcover_sync_job_starting", user_count=len(user_ids))
    for uid in user_ids:
        await sync_hardcover_lists_for_user(uid)
    logger.info("hardcover_sync_job_complete", user_count=len(user_ids))


async def refresh_nyt_bestsellers() -> None:
    """Pre-warm the NYT Best Sellers cache used by the Discover page."""
    from app.routers.discover import build_bestsellers_payload
    from app.services.nyt_bestsellers import get_nyt_api_key

    if not get_nyt_api_key():
        logger.info("nyt_bestsellers_job_skipped", reason="no_api_key")
        return

    db: Session = SessionLocal()
    try:
        payload = await build_bestsellers_payload(db)
        logger.info(
            "nyt_bestsellers_job_complete",
            list_count=len(payload.lists),
            book_count=sum(len(lst.books) for lst in payload.lists),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("nyt_bestsellers_job_error", error=str(e))
    finally:
        db.close()
