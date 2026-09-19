"""
Background tasks for refreshing seed data
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func as sa_func
from app.database import SessionLocal
from app.models import Book
from app import schemas
from app.routers.hardcover import execute_graphql, _parse_hardcover_book
from app.routers.settings import get_hardcover_token
import structlog

logger = structlog.get_logger()


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

# Extra margin added on top of sync_book_availability's own interval before an
# unconfirmed ebook import gets flagged - covers a run that's briefly late/
# skipped without immediately alerting on a single missed cycle.
EBOOK_LIBRARY_WAIT_BUFFER = timedelta(minutes=30)


def _ebook_library_wait_timeout(db: Session) -> timedelta:
    """How long to wait for Calibre to index a completed ebook download before
    flagging it for admin attention - one sync_book_availability cycle (that's
    what actually checks) plus ``EBOOK_LIBRARY_WAIT_BUFFER``, so a normal run
    always gets a fair chance before we give up on it. Re-read on every call
    since the interval is admin-editable at runtime.
    """
    from app.models import JobSchedule

    interval_seconds = 5 * 60  # sync_book_availability's default
    try:
        schedule = (
            db.query(JobSchedule)
            .filter(JobSchedule.job_name == "sync_book_availability")
            .first()
        )
        if schedule and schedule.interval_seconds:
            interval_seconds = schedule.interval_seconds
    except Exception:
        pass
    return timedelta(seconds=interval_seconds) + EBOOK_LIBRARY_WAIT_BUFFER


def _format_wait_duration(td: timedelta) -> str:
    minutes = max(1, round(td.total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


def _alert_admins_of_stuck_import(db: Session, task, wait_timeout: timedelta) -> None:
    """Tell admins a completed ebook download was never confirmed in Calibre.

    Never raises - a broken SMTP config shouldn't block the reconcile loop.
    """
    from app.services.email_service import send_admin_alert

    title = task.book.title if task.book else "Unknown title"
    author = task.book.author if task.book and task.book.author else None
    label = f'"{title}"' + (f" by {author}" if author else "")
    wait_text = _format_wait_duration(wait_timeout)

    try:
        send_admin_alert(
            db,
            subject=f"Ebook import stuck: {label}",
            body=(
                f'The ebook download for {label} completed over {wait_text} ago, but it '
                f"was never confirmed in the Calibre library.\n\n"
                f"This usually means the file wasn't picked up by Calibre-Web or your "
                f"watch folder, or its title/author metadata drifted too far for "
                f"Bookkeep's fuzzy matcher to find it.\n\n"
                f"Download task ID: {task.id}\n"
                f"Book ID: {task.book_id}\n\n"
                f"Check that the file actually made it into your Calibre library. "
                f"Bookkeep will keep checking automatically and will link it as soon as "
                f"it's found - no need to do anything here unless the import is broken."
            ),
        )
    except Exception as exc:
        logger.warning("admin_alert_failed", task_id=task.id, error=str(exc))


def reconcile_ebook_library_imports(
    db: Session, library_path: Optional[str] = None
) -> list[int]:
    """Promote completed ebook downloads to 'imported' once Calibre has indexed them.

    The orchestrator parks ebook imports in 'awaiting_library' when a Calibre
    library is configured; this checks that library for each one and flips it to
    'imported' (also marking the book available). Returns the ``book_id``s that
    were just promoted, so the caller can refresh their metadata.

    A download that never shows up in Calibre within the wait timeout (see
    ``_ebook_library_wait_timeout``) is NOT force-promoted - that would falsely
    tell the requester their book is available when it might not be. Instead,
    admins get a one-time alert email and the task keeps getting checked on
    every future run, so it still heals itself once the underlying Calibre
    import is fixed.
    """
    from app.models import DownloadTask
    from app.routers.calibre import get_active_library_path
    from app.services import calibre_service, calibre_link_service

    wait_timeout = _ebook_library_wait_timeout(db)

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

        # Fuzzy match missed - fall back to a persisted link for the stragglers.
        for t in tasks:
            if t.book and matched_ids.get(t.id) is None:
                matched_ids[t.id] = calibre_link_service.linked_library_book_id(
                    db, library_path, t.book_id
                )

    now = datetime.now(timezone.utc)
    promoted: list[int] = []
    changed = False
    for task in tasks:
        calibre_id = matched_ids.get(task.id)
        if calibre_id is not None:
            task.import_status = "imported"
            task.imported_at = now
            task.import_message = "Indexed by Calibre"
            if task.book:
                task.book.ebook_available = True
                # Exact link: this download is this Calibre book.
                try:
                    calibre_link_service.upsert_link(
                        db,
                        calibre_book_id=calibre_id,
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
            changed = True
            logger.info(
                "ebook_import_confirmed",
                task_id=task.id,
                book_id=task.book_id,
                book_title=task.book.title if task.book else None,
                book_author=task.book.author if task.book else None,
                matched_calibre_id=calibre_id,
            )
            continue

        # Not found in the library yet - check whether it's been long enough
        # to flag instead of quietly waiting forever.
        completed_at = task.completed_at or task.updated_at or task.created_at
        if completed_at is not None and completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        if completed_at is None or (now - completed_at) < wait_timeout:
            continue

        if task.admin_alerted_at is None:
            task.admin_alerted_at = now
            task.import_message = (
                f"Download completed but not confirmed in the Calibre library "
                f"after {_format_wait_duration(wait_timeout)} - admin notified"
            )
            changed = True
            logger.warning(
                "ebook_import_stuck",
                task_id=task.id,
                book_id=task.book_id,
                book_title=task.book.title if task.book else None,
                book_author=task.book.author if task.book else None,
            )
            _alert_admins_of_stuck_import(db, task, wait_timeout)

    if changed:
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
    the state transition, so it is normally cheap on the APIs - but a batch of
    several ``book_ids`` at once (e.g. many stuck requests resolving together)
    hits Apple Books too, whose free tier is only ~20 req/min, so the pause
    between books needs to hold that pace even though the other sources here
    are much more generous.
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
        await asyncio.sleep(3.0)


def _sync_unmatched_ebook_requests(db: Session) -> tuple[list[int], int]:
    """Catch ebook requests a Calibre match hasn't reached yet.

    ``update_processing_requests_status`` already Calibre-matches
    ``processing`` requests one at a time; this batches *every* open ebook
    request (pending / approved / processing / not_found) against the whole
    library in one lookup, so books that show up there without going through
    a Bookkeep download - manual add, side-load - are caught too, and a
    ``processing`` request gets a second shot if its per-request match missed.
    """
    from sqlalchemy.orm import joinedload
    from app.models import BookRequest
    from app.routers.calibre import get_active_library_path
    from app.services import calibre_service, calibre_link_service

    library_path = get_active_library_path(db)
    if not library_path:
        return [], 0

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
    if not reqs:
        return [], 0

    try:
        matches = calibre_service.match_books(
            library_path, [(r.book.title, r.book.author, r.book.isbn) for r in reqs]
        )
    except calibre_service.CalibreError as exc:
        logger.warning("unmatched_ebook_requests_lookup_failed", error=str(exc))
        return [], 0

    now = datetime.now(timezone.utc)
    promoted: list[int] = []
    updated = 0
    for req, calibre_id in zip(reqs, matches):
        if calibre_id is None:
            # Fuzzy match missed - trust a persisted link if the book still
            # carries an ebook format in the library.
            calibre_id = calibre_link_service.linked_library_book_id(
                db, library_path, req.book_id
            )
        if calibre_id is None:
            continue
        prev = req.status
        # A request that resolves to a library book is a strong link - but
        # if another Book already owns that Calibre id with an equal or
        # stronger link, this match is a false positive (e.g. two
        # similarly-titled books colliding in the fuzzy matcher) and must
        # not flip this request to available with nothing actually linked.
        try:
            link = calibre_link_service.upsert_link(
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
            link = None
        if link is None:
            logger.info(
                "request_calibre_match_conflict",
                request_id=req.id,
                book_id=req.book_id,
                book_title=req.book.title,
                book_author=req.book.author,
                calibre_id=calibre_id,
            )
            continue
        req.status = "available"
        req.updated_at = now
        req.book.ebook_available = True
        updated += 1
        promoted.append(req.book_id)
        logger.info(
            "request_available_from_calibre",
            request_id=req.id,
            book_id=req.book_id,
            book_title=req.book.title,
            book_author=req.book.author,
            calibre_id=calibre_id,
            previous_status=prev,
        )
    if updated:
        db.commit()
        logger.info("unmatched_ebook_requests_complete", updated=updated, checked=len(reqs))
    return promoted, updated


async def sync_book_availability():
    """Keep request status in sync with actual download/library state.

    Runs frequently (default every 5 minutes):
    1. Promotes completed ebook downloads that Calibre has now indexed
       (``reconcile_ebook_library_imports``).
    2. Updates ``processing`` requests: Calibre match for ebooks, else
       DownloadTask state (still active / just imported / stalled long
       enough to mark ``not_found``) - covers both ebook and audiobook.
    3. Sweeps every other open ebook request (pending / approved / not_found)
       against the whole Calibre library, so books that appear there without
       going through a Bookkeep download are caught too.

    Whole-library link maintenance (``sync_availability_flags`` /
    ``reopen_stale_available_requests``) lives in ``heal_calibre_links``
    instead - that's slower upkeep, not something this cadence needs to do.
    """
    from app.routers.requests import update_processing_requests_status

    db: Session = SessionLocal()
    try:
        promoted: list[int] = []
        try:
            promoted = reconcile_ebook_library_imports(db)
        except Exception as e:
            logger.error("reconcile_ebook_library_imports_error", error=str(e))
            db.rollback()

        await update_processing_requests_status(db)

        more_promoted, updated = _sync_unmatched_ebook_requests(db)
        promoted.extend(more_promoted)

        await _refresh_downloaded_books(db, promoted)

        if updated:
            # A newly-matched request may have changed what a book page shows
            # (status, "cancel my request" button) - drop the cache now
            # rather than waiting on its TTL.
            from app.cache import clear_cache_pattern
            await clear_cache_pattern("requests_by_hardcover:*")
            await clear_cache_pattern("requests_by_hardcover_batch:*")

        await send_availability_emails()
    except Exception as e:
        logger.error("sync_book_availability_error", error=str(e))
    finally:
        db.close()


async def heal_calibre_links():
    """Daily: repair Calibre links across the whole library.

    Split out from ``sync_book_availability`` because this is whole-library
    maintenance, not something that benefits from a 5-minute cadence.
    """
    from app.routers.calibre import get_active_library_path
    from app.services import calibre_link_service

    db: Session = SessionLocal()
    try:
        library_path = get_active_library_path(db)
        if not library_path:
            return

        calibre_link_service.sync_availability_flags(db, library_path)
        # Catches requests left stuck on "available" from before a link
        # went stale, or that never had a link at all - heal_stale_links
        # only fires at the moment a link breaks, so this is what covers
        # everything already orphaned.
        reopened = calibre_link_service.reopen_stale_available_requests(
            db, library_path
        )
        if reopened:
            # A reopened request may have reset a Book's ebook_available
            # flag - drop the cached request-status views so book pages
            # stop showing it as available right away instead of waiting
            # on the TTL.
            from app.cache import clear_cache_pattern
            await clear_cache_pattern("requests_by_hardcover:*")
            await clear_cache_pattern("requests_by_hardcover_batch:*")
    except Exception as e:
        logger.error("heal_calibre_links_error", error=str(e))
        db.rollback()
    finally:
        db.close()


# Cap the daily/startup Calibre metadata sweep so one run stays polite to the
# upstream metadata APIs on a very large library. Whatever is left over is
# picked up on the next run.
CALIBRE_METADATA_SCAN_LIMIT = 400


async def _enrich_calibre_metadata(db: Session, *, limit: int) -> int:
    """Fill in metadata for linked Calibre books that are missing it.

    Used by the ``sync_calibre_metadata`` job (full daily sweep). Targets
    linked books with a gap in the fields the overlay shows — description,
    cover, genres — or that have never been refreshed. Metadata comes from
    Open Library plus Hardcover (for linked books); Google Books is left for
    the post-download refresh so this sweep does not spend its daily quota.
    Honors the overlay toggle. One book at a time with a short pause between
    lookups.
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
            ok = await book_metadata.enrich_book(db, book, calibre_book_id=link.calibre_book_id)
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


# A Calibre book we searched Hardcover + Open Library for and found nothing
# isn't re-attempted for this long - without this, an unmatchable book (e.g. a
# title neither catalog has) gets both APIs searched again on every single
# run of import_calibre_books forever, since a failed match is never linked
# and so never leaves the "todo" set on its own.
CALIBRE_IMPORT_RETRY_COOLDOWN = timedelta(days=7)


async def _import_unlinked_calibre_books(
    db: Session, library_path: str, *, limit: int
) -> int:
    """Give a ``Book`` row to Calibre books that have neither, and link the rest.

    ``backfill_fuzzy_links`` only links library books that already match a row in
    our ``books`` table by a straightforward ISBN/title/author match, so a
    side-loaded book we have never seen stays "metadata from Calibre only"
    forever. For each such book:

    * If it reuses an existing unlinked ``Book`` row for the same work (e.g.
      its audiobook already came in from Audiobookshelf) - just link it. That
      row was already resolved through its own path and doesn't need a fresh
      API lookup on our account.
    * Otherwise it's genuinely new to us: create a bare row from the Calibre
      identity, enrich it (Open Library, then Hardcover) since a bare
      title/author copied from Calibre isn't useful on its own, and link it -
      only keeping the row if something was actually found. A book with
      nothing found is recorded in ``CalibreImportAttempt`` and skipped for
      ``CALIBRE_IMPORT_RETRY_COOLDOWN`` instead of being retried every run.

    Bounded per run.
    """
    from app.services import book_metadata, calibre_service, calibre_link_service
    from app.models import Book, CalibreBookLink, CalibreImportAttempt

    try:
        library_ids = await calibre_service.call_with_timeout(
            calibre_service.existing_book_ids, library_path
        )
    except calibre_service.CalibreError as exc:
        logger.warning("calibre_metadata_probe_failed", error=str(exc))
        return 0

    linked = {r[0] for r in db.query(CalibreBookLink.calibre_book_id).all()}
    cooldown_cutoff = datetime.now(timezone.utc) - CALIBRE_IMPORT_RETRY_COOLDOWN
    on_cooldown = {
        r[0]
        for r in db.query(CalibreImportAttempt.calibre_book_id)
        .filter(CalibreImportAttempt.last_attempted_at >= cooldown_cutoff)
        .all()
    }
    todo = sorted(library_ids - linked - on_cooldown)[:limit]
    if not todo:
        return 0

    try:
        identities = await calibre_service.call_with_timeout(
            calibre_service.book_identities, library_path, todo
        )
        fmt_map = await calibre_service.call_with_timeout(
            calibre_service.formats_for_ids, library_path, todo
        )
    except calibre_service.CalibreError as exc:
        logger.warning("calibre_metadata_identities_failed", error=str(exc))
        return 0

    # Loaded once for the whole batch - find_matching_book re-scans this on
    # every call, so re-fetching it per candidate would turn a several-hundred
    # book run into that many full-table scans with no await point between
    # them, freezing the app (single event loop, no worker threads) for the
    # entire run.
    book_rows = calibre_link_service.book_match_candidates(db)

    created = 0
    for cal_id, title, author, isbn in identities:
        if not title:
            continue

        has_ebook = any(
            calibre_service.classify_format(f) == "ebook" for f in fmt_map.get(cal_id, [])
        )

        book = db.query(Book).filter(Book.isbn == isbn).first() if isbn else None
        if book is None:
            # Reuse an existing record for the same work (e.g. its audiobook came
            # in from Audiobookshelf first) as long as nothing else in Calibre is
            # already linked to it (CalibreBookLink is one-to-one).
            cand = calibre_link_service.find_matching_book(
                db, title, author, isbn, candidates=book_rows
            )
            if cand is not None and calibre_link_service.get_link_for_book(db, cand.id) is None:
                book = cand

        if book is not None:
            # Matched an existing row - it was already resolved through its own
            # path (download, request, Audiobookshelf sync). Nothing to enrich,
            # just link the two records together; don't touch last_refreshed,
            # since we haven't actually checked anything here - that would just
            # make sync_calibre_metadata's staleness check think this book was
            # just verified and skip it for the next 7 days.
            if has_ebook and not book.ebook_available:
                book.ebook_available = True
        else:
            # Genuinely new to us - a bare title/author copied from Calibre
            # isn't useful on its own, so this is the one case worth an API call.
            book = Book(
                title=title,
                author=author or "Unknown Author",
                isbn=isbn or None,
                ebook_available=has_ebook,
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
                found = await book_metadata.enrich_book(
                    db, book, resolve_hardcover=True, calibre_book_id=cal_id
                )
            except Exception as exc:
                logger.warning("calibre_metadata_enrich_failed", calibre_id=cal_id, error=str(exc))
                db.rollback()
                continue

            if not found and not book.hardcover_id:
                # Nothing to show for this book — leave it "Calibre only" rather than
                # keeping a bare linked row that looks enriched but is not. Record
                # the attempt so it's not re-searched on every future run.
                db.rollback()
                attempt = db.query(CalibreImportAttempt).filter(
                    CalibreImportAttempt.calibre_book_id == cal_id
                ).first()
                if attempt is None:
                    attempt = CalibreImportAttempt(calibre_book_id=cal_id, attempt_count=0)
                    db.add(attempt)
                attempt.last_attempted_at = datetime.now(timezone.utc)
                attempt.attempt_count += 1
                try:
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    logger.warning(
                        "calibre_import_attempt_record_failed", calibre_id=cal_id, error=str(exc)
                    )
                await asyncio.sleep(0.5)
                continue

            if book.last_refreshed is None:
                book.last_refreshed = datetime.now(timezone.utc)

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


async def import_calibre_books() -> None:
    """Keep the Calibre <-> Book link table healthy and import side-loaded books.

    Runs on startup and once a day (and can be triggered from the admin Jobs
    page). Two passes, each bounded per run:

    1. ``heal_stale_links`` / ``backfill_fuzzy_links`` - re-point or drop links
       whose ``calibre_book_id`` no longer resolves, and link any not-yet-linked
       library book that already matches a ``Book`` row.
    2. ``_import_unlinked_calibre_books`` - give a ``Book`` row + metadata to
       library books that have neither (side-loads Bookworms has never seen);
       this is what clears "metadata from Calibre only".

    Split out from ``sync_calibre_metadata`` (which only fills gaps on books
    already linked here) so the two can be scheduled or disabled independently
    - discovering/linking books is comparatively cheap, while the metadata
    backfill is the heavier, API-call-hungry pass.
    """
    from app.routers.calibre import get_active_library_path, _bool_setting, OVERLAY_ENABLED_KEY
    from app.services import calibre_link_service

    db: Session = SessionLocal()
    try:
        library_path = get_active_library_path(db)
        if not library_path:
            logger.info("import_calibre_books_skipped", reason="no_calibre_library")
            return
        if not _bool_setting(db, OVERLAY_ENABLED_KEY, True):
            logger.info("import_calibre_books_skipped", reason="overlay_disabled")
            return

        # Timed per phase - each of these has turned out to hide an accidental
        # O(library size) sqlite access pattern at least once, and that only
        # shows up as "the app is unresponsive" with nothing else to go on.
        # If it happens again, these durations say which phase to look at
        # instead of re-deriving it from scratch.
        t0 = time.monotonic()
        try:
            healed = await calibre_link_service.heal_stale_links(db, library_path)
        except Exception as e:
            logger.error("import_calibre_books_link_error", phase="heal_stale_links", error=str(e))
            db.rollback()
            healed = None
        t1 = time.monotonic()
        try:
            backfilled = await calibre_link_service.backfill_fuzzy_links(db, library_path)
        except Exception as e:
            logger.error("import_calibre_books_link_error", phase="backfill_fuzzy_links", error=str(e))
            db.rollback()
            backfilled = None
        t2 = time.monotonic()
        logger.info(
            "import_calibre_books_link_phase_complete",
            healed=healed,
            backfilled=backfilled,
            heal_seconds=round(t1 - t0, 2),
            backfill_seconds=round(t2 - t1, 2),
        )

        imported = 0
        try:
            imported = await _import_unlinked_calibre_books(
                db, library_path, limit=CALIBRE_METADATA_SCAN_LIMIT
            )
        except Exception as e:
            logger.error("import_calibre_books_import_error", error=str(e))
            db.rollback()
        t3 = time.monotonic()

        logger.info(
            "import_calibre_books_complete",
            imported=imported,
            heal_seconds=round(t1 - t0, 2),
            backfill_seconds=round(t2 - t1, 2),
            import_seconds=round(t3 - t2, 2),
        )
    except Exception as e:
        logger.error("import_calibre_books_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def sync_calibre_metadata() -> None:
    """Fill in missing metadata (description, cover, genres, ...) for
    already-linked Calibre books.

    Runs on startup and once a day (and can be triggered from the admin Jobs
    page). Metadata comes from Open Library first (no API key, no rate limit),
    then Hardcover for series / ratings / anything still missing. Bounded per
    run (``CALIBRE_METADATA_SCAN_LIMIT``).

    Linking new/side-loaded Calibre books into a ``Book`` row in the first
    place is a separate job (``import_calibre_books``) - see its docstring.
    """
    from app.routers.calibre import get_active_library_path, _bool_setting, OVERLAY_ENABLED_KEY

    db: Session = SessionLocal()
    try:
        library_path = get_active_library_path(db)
        if not library_path:
            logger.info("sync_calibre_metadata_skipped", reason="no_calibre_library")
            return
        if not _bool_setting(db, OVERLAY_ENABLED_KEY, True):
            logger.info("sync_calibre_metadata_skipped", reason="overlay_disabled")
            return

        enriched = await _enrich_calibre_metadata(db, limit=CALIBRE_METADATA_SCAN_LIMIT)
        logger.info("sync_calibre_metadata_complete", enriched=enriched)
    except Exception as e:
        logger.error("sync_calibre_metadata_error", error=str(e))
        db.rollback()
    finally:
        db.close()


# Give up auto-emailing a request after this many failed SMTP attempts.
MAX_AUTO_EMAIL_ATTEMPTS = 5


async def send_availability_emails():
    """Email users when a book they requested reaches ``available`` status.

    Two independent things can happen per request:

    * **Availability notification** — a short "your request is available" email to
      the user's *account* address, for every request (ebook or audiobook), sent
      once. Deduped via ``availability_notified_at``.
    * **eBook file delivery** — for ebook requests, the file itself is attached
      and sent to the user's configured *book-delivery* address (if one is set).
      Pulled from the Calibre library and retried across runs until the file
      turns up or the SMTP attempt budget is spent. Tracked by
      ``auto_email_sent_at`` / ``auto_email_attempts``.
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
                BookRequest.status == "available",
                or_(
                    BookRequest.availability_notified_at.is_(None),
                    and_(
                        BookRequest.format == "ebook",
                        BookRequest.auto_email_sent_at.is_(None),
                    ),
                ),
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
            if not user or not book:
                continue

            # 1. Availability notification -> the user's account email. Every
            #    request, both formats, once. Only failed attempts count against
            #    the shared SMTP retry budget (auto_email_attempts), so a normal
            #    notification leaves the ebook file-send budget untouched.
            if req.availability_notified_at is None:
                try:
                    send_availability_notification(
                        db, user, book_title=book.title, book_format=req.format
                    )
                    req.availability_notified_at = datetime.now(timezone.utc)
                    sent += 1
                    logger.info(
                        "availability_notification_sent",
                        request_id=req.id, user_id=user.id, book_id=book.id,
                    )
                except EmailError as exc:
                    req.auto_email_attempts = (req.auto_email_attempts or 0) + 1
                    logger.warning(
                        "availability_notification_failed",
                        request_id=req.id, attempt=req.auto_email_attempts, error=str(exc),
                    )
                    if req.auto_email_attempts >= MAX_AUTO_EMAIL_ATTEMPTS:
                        req.availability_notified_at = datetime.now(timezone.utc)
                        logger.error("availability_notification_gave_up", request_id=req.id)
                db.commit()

            # 2. eBook file delivery -> the configured book-delivery address.
            #    Audiobooks are never attached; ebooks only when an address is set.
            if req.format != "ebook" or req.auto_email_sent_at is not None:
                continue
            if not (user.book_delivery_email or "").strip():
                continue
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

            fmt = calibre_service.pick_format(library_path, match_id)
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


async def promote_and_email(book_id: Optional[int] = None, fmt: Optional[str] = None) -> None:
    """Promote freshly-completed request(s) to ``available``, drop the request
    cache, and send their availability emails right now instead of waiting for
    the periodic jobs.

    With ``book_id`` (and optionally ``fmt``): flip that book's open request(s)
    directly — used straight after an import + Audiobookshelf match, where the
    generic reconcile might not see the download task as ``imported`` yet.
    Without it: run the full processing-request reconcile.

    Idempotent and self-selecting, so safe to call redundantly.
    """
    from app.models import Book, BookRequest
    from app.routers.requests import update_processing_requests_status

    _OPEN = ("pending", "approved", "processing", "not_found")
    changed = False
    db: Session = SessionLocal()
    try:
        if book_id is not None:
            book = db.query(Book).filter(Book.id == book_id).first()
            q = db.query(BookRequest).filter(
                BookRequest.book_id == book_id,
                BookRequest.status.in_(_OPEN),
            )
            if fmt:
                q = q.filter(BookRequest.format == fmt)
            now = datetime.now(timezone.utc)
            open_reqs = q.all()
            logger.info(
                "promote_and_email_processing_download",
                book_id=book_id,
                book_title=book.title if book else None,
                book_author=book.author if book else None,
                format=fmt,
                open_requests=[r.id for r in open_reqs],
            )
            for req in open_reqs:
                req.status = "available"
                req.updated_at = now
                if book and req.format == "ebook":
                    book.ebook_available = True
                elif book and req.format == "audiobook":
                    book.audiobook_available = True
                changed = True
                logger.info(
                    "request_available_after_download",
                    request_id=req.id,
                    book_id=book_id,
                    format=req.format,
                )
            if changed:
                db.commit()
        else:
            # update_processing_requests_status commits and busts the cache itself.
            await update_processing_requests_status(db)
    except Exception as e:
        logger.error("promote_and_email_promote_error", book_id=book_id, error=str(e))
        db.rollback()
    finally:
        db.close()

    if changed:
        from app.cache import clear_cache_pattern
        await clear_cache_pattern("requests_by_hardcover:*")
        await clear_cache_pattern("requests_by_hardcover_batch:*")

    try:
        await send_availability_emails()
    except Exception as e:
        logger.error("promote_and_email_send_error", error=str(e))


async def run_background_request_check():
    """Background task to check processing requests periodically"""
    job_name = "sync_book_availability"

    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_request_check_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)

    while True:
        try:
            await sync_book_availability()
            update_job_execution(job_name)
        except Exception as e:
            logger.error("background_request_check_error", error=str(e))
        
        # Get interval from database (default 5 minutes)
        interval = get_job_interval_standalone(job_name, 5 * 60)
        logger.debug("background_request_check_sleeping", interval_seconds=interval)
        await asyncio.sleep(interval)


async def import_audiobookshelf_books():
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
            logger.info("import_audiobookshelf_books_skipped", reason="no_audiobookshelf_server")
            return

        logger.info("import_audiobookshelf_books_starting", server_name=abs_server.name)

        items = await get_all_audiobookshelf_items(abs_server)

        if not items:
            logger.info("import_audiobookshelf_books_skipped", reason="no_items_in_audiobookshelf")
            return

        logger.info("import_audiobookshelf_books_fetched", count=len(items))

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

            # Fuzzy title+author over every Book, so an already-catalogued ebook
            # of this work (possibly under a differently-subtitled Hardcover
            # title) is reused rather than a second record created.
            if not db_book and title:
                from app.services import calibre_link_service

                cand = calibre_link_service.find_matching_book(db, title, author, isbn)
                if cand is not None and cand.audiobookshelf_id in (None, item_id):
                    db_book = cand

            # Requested-but-unlinked audiobooks: this item may belong to an
            # in-flight request whose Book already carries the hardcover_id
            # picked when it was requested/approved. Prefer reusing that Book
            # over a fresh identity-less Hardcover search below (which can
            # miss, or create a duplicate row) — reuse the same title/author
            # matcher the direct post-download linker uses, scoped to just
            # the small set of outstanding audiobook requests so a looser
            # match here can't misfire against the whole library.
            if not db_book:
                pending_books = (
                    db.query(Book)
                    .join(BookRequest, BookRequest.book_id == Book.id)
                    .filter(
                        BookRequest.format == "audiobook",
                        BookRequest.status.in_(["processing", "approved"]),
                        Book.audiobookshelf_id.is_(None),
                    )
                    .all()
                )
                db_book = next(
                    (b for b in pending_books if match_book_to_abs_item(b, item)),
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

        logger.info("import_audiobookshelf_books_complete",
                   audiobookshelf_items=len(items),
                   books_created=books_created,
                   books_updated=books_updated,
                   requests_updated=updated_count,
                   skipped=skipped_count)

    except Exception as e:
        logger.error("import_audiobookshelf_books_error", error=str(e))
        db.rollback()
    finally:
        db.close()


async def run_background_metadata_sync():
    """Background task to sync missing metadata periodically"""
    job_name = "sync_audiobook_metadata"

    # Wait until next scheduled execution before first run
    initial_wait = get_seconds_until_next_execution(job_name)
    if initial_wait > 0:
        logger.info("background_metadata_sync_waiting", seconds=initial_wait)
        await asyncio.sleep(initial_wait)

    while True:
        try:
            await sync_audiobook_metadata()
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
        "sync_book_availability": 5 * 60,
        "import_audiobookshelf_books": 24 * 60 * 60,
        "sync_audiobook_metadata": 6 * 60 * 60,
    }
    
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule:
            return schedule.interval_seconds
    except Exception:
        pass
    
    return defaults.get(job_name, 3600)


async def sync_audiobook_metadata():
    """Fill in missing Hardcover metadata for books Calibre doesn't know about.

    Runs every 6 hours. Complements ``sync_calibre_metadata``, which only
    enriches Calibre-linked books - this job covers everything else, which in
    practice is almost entirely audiobook-only ``Book`` rows synced in from
    Audiobookshelf (the filter is "no CalibreBookLink", not "is an audiobook",
    so an ebook that hasn't been picked up by ``import_calibre_books`` yet
    would also qualify, but that's the uncommon case). Targets four gaps, each
    capped per run:

    1. Has a ``hardcover_slug`` but never resolved the numeric ``hardcover_id``.
    2. No cover and no ``hardcover_id`` yet (needs a title/author search).
    3. Has a ``hardcover_id`` but no rating.
    4. Has a ``hardcover_id`` and a series name/position but no ``series_id``.

    Uses the same ``book_metadata.enrich_book`` merge (Open Library +
    Hardcover, matching ``sync_calibre_metadata``'s enrichment) as the rest of
    the app, instead of a separate hand-rolled Hardcover-only merge.
    """
    from app.services import book_metadata
    from app.models import CalibreBookLink

    db: Session = SessionLocal()
    try:
        # Anything Calibre-linked is sync_calibre_metadata's job - excluding it
        # here avoids the two jobs re-fetching the same books from Hardcover.
        linked_ids = db.query(CalibreBookLink.book_id)

        books_with_slug_no_id = db.query(Book).filter(
            Book.hardcover_slug.isnot(None),
            Book.hardcover_id.is_(None),
            Book.id.notin_(linked_ids),
        ).limit(50).all()

        books_without_cover = db.query(Book).filter(
            Book.cover_url.is_(None),
            Book.hardcover_id.is_(None),
            Book.id.notin_(linked_ids),
        ).limit(50).all()  # Limit to avoid too many API calls

        books_without_rating = db.query(Book).filter(
            Book.rating.is_(None),
            Book.hardcover_id.isnot(None),
            Book.id.notin_(linked_ids),
        ).limit(50).all()

        books_without_series_id = db.query(Book).filter(
            Book.hardcover_id.isnot(None),
            Book.series_id.is_(None),
            or_(Book.series.isnot(None), Book.series_position.isnot(None)),
            Book.id.notin_(linked_ids),
        ).limit(50).all()

        # Combine and dedupe
        all_books = {
            b.id: b
            for b in books_with_slug_no_id
            + books_without_cover
            + books_without_rating
            + books_without_series_id
        }

        if not all_books:
            logger.info("sync_audiobook_metadata_skipped", reason="no_books_need_update")
            return

        logger.info(
            "sync_audiobook_metadata_starting",
            books_count=len(all_books),
            with_slug_no_id=len(books_with_slug_no_id),
            without_cover=len(books_without_cover),
            without_rating=len(books_without_rating),
            without_series_id=len(books_without_series_id),
        )

        updated_count = 0
        failed_count = 0

        for book_id, book in all_books.items():
            try:
                changed = await book_metadata.enrich_book(db, book, resolve_hardcover=True)
            except Exception as e:
                logger.warning(
                    "sync_audiobook_metadata_book_error", book_id=book_id, title=book.title, error=str(e)
                )
                db.rollback()
                failed_count += 1
                await asyncio.sleep(0.5)
                continue

            if book.last_refreshed is None:
                book.last_refreshed = datetime.now(timezone.utc)
            try:
                db.commit()
                if changed:
                    updated_count += 1
            except Exception as commit_error:
                db.rollback()
                logger.warning(
                    "sync_audiobook_metadata_commit_failed", book_id=book_id, error=str(commit_error)
                )
                failed_count += 1
            await asyncio.sleep(0.5)  # Rate limit protection

        logger.info(
            "sync_audiobook_metadata_complete",
            total_books=len(all_books),
            updated=updated_count,
            failed=failed_count,
        )

    except Exception as e:
        logger.error("sync_audiobook_metadata_error", error=str(e))
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
