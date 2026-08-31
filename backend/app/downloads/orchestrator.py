"""
Download orchestrator.

Manages the complete download workflow from search to completion.
"""
import os
import shutil
import threading
from pathlib import Path
from typing import Optional, List, Dict
from threading import Event
from datetime import datetime, timezone
import structlog
from sqlalchemy.orm import Session

from . import (
    Release,
    DownloadState,
    DownloadStatus,
    get_source,
    get_handler,
    list_sources,
    list_handlers,
)
from ..models import Book, DownloadTask, AppSettings, DownloadClient, DirectDownloadSettings, CalibreSettings
from ..database import SessionLocal

logger = structlog.get_logger()


def _calibre_library_active(db: Session) -> bool:
    """True when a Calibre library is configured and enabled.

    Ebook imports wait for the file to show up in that library before they are
    considered 'imported'; without a library there is nothing to wait for.
    """
    row = db.query(CalibreSettings).first()
    return bool(row and row.enabled and row.library_path)


# Extensions treated as audiobook tracks when laying out an import. Everything
# else in a release (artwork, .nfo, .cue, samples) is left behind.
AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".aac", ".wma",
}


def _sanitize_path_component(name: str) -> str:
    """Make a string safe to use as a single file or folder name."""
    import re

    name = (name or "").strip()
    # Drop characters that are illegal or awkward on common filesystems
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Windows dislikes trailing dots/spaces
    name = name.rstrip(". ")
    return name or "Unknown"


class DownloadOrchestrator:
    """
    Orchestrates book downloads from search to completion.

    Workflow:
    1. Search for releases via configured sources (Prowlarr, etc.)
    2. Select best release based on quality score
    3. Create download task
    4. Execute download via appropriate handler (torrent/usenet)
    5. Monitor progress
    6. Post-process downloaded files
    7. Update book availability
    """

    def __init__(self, db_session: Optional[Session] = None):
        """
        Initialize orchestrator.

        Args:
            db_session: Database session (will create if not provided)
        """
        self.db_session = db_session
        self._active_downloads: Dict[int, Event] = {}  # task_id -> cancel_event
        self._download_threads: Dict[int, threading.Thread] = {}

    def get_available_protocols(self, db: Optional[Session] = None) -> List[str]:
        """
        Get list of protocols that have enabled download clients configured.

        Args:
            db: Database session

        Returns:
            List of available protocols (e.g., ["torrent"], ["torrent", "usenet", "direct"])
        """
        session = db or self.db_session or SessionLocal()
        close_session = db is None and self.db_session is None

        try:
            # Query distinct protocols from enabled download clients
            enabled_clients = session.query(DownloadClient.protocol).filter(
                DownloadClient.enabled == True
            ).distinct().all()

            protocols = [client.protocol for client in enabled_clients if client.protocol]

            # Check if direct downloads are enabled
            direct_settings = session.query(DirectDownloadSettings).first()
            if direct_settings and direct_settings.enabled:
                protocols.append("direct")

            logger.debug(
                "orchestrator_available_protocols",
                protocols=protocols
            )

            return protocols

        except Exception as e:
            logger.error("orchestrator_get_protocols_failed", error=str(e))
            # Default to torrent if we can't determine (safer fallback)
            return ["torrent"]
        finally:
            if close_session:
                session.close()

    def search_releases(
        self,
        book: Book,
        format_type: str = "ebook",
        source_name: str = "prowlarr"
    ) -> List[Release]:
        """
        Search for book releases.

        Args:
            book: Book to search for
            format_type: "ebook" or "audiobook"
            source_name: Source to use (default: "prowlarr")

        Returns:
            List of Release objects sorted by quality
        """
        db = self.db_session or SessionLocal()
        try:
            source = get_source(source_name, db_session=db)

            logger.info(
                "orchestrator_search",
                book_id=book.id,
                title=book.title,
                author=book.author,
                format_type=format_type,
                source=source_name
            )

            releases = source.search(
                title=book.title,
                author=book.author,
                isbn=book.isbn,
                format_type=format_type
            )

            # Filter releases to only include protocols with configured clients
            available_protocols = self.get_available_protocols(db)
            total_before_filter = len(releases)

            if available_protocols:
                for r in releases:
                    if r.protocol not in available_protocols:
                        logger.info(
                            "orchestrator_drop_protocol",
                            title=r.title,
                            protocol=r.protocol,
                            available_protocols=available_protocols,
                        )
                releases = [r for r in releases if r.protocol in available_protocols]

            filtered_count = total_before_filter - len(releases)

            logger.info(
                "orchestrator_search_complete",
                book_id=book.id,
                releases_found=len(releases),
                releases_filtered=filtered_count,
                available_protocols=available_protocols,
                top_quality=releases[0].quality_score if releases else 0
            )

            return releases

        except Exception as e:
            logger.error(
                "orchestrator_search_failed",
                book_id=book.id,
                error=str(e)
            )
            return []
        finally:
            if not self.db_session:
                db.close()

    def create_download_task(
        self,
        book: Book,
        release: Release,
        format_type: str
    ) -> Optional[DownloadTask]:
        """
        Create a download task from a release.

        Args:
            book: Book to download
            release: Selected release
            format_type: "ebook" or "audiobook"

        Returns:
            Created DownloadTask or None
        """
        db = self.db_session or SessionLocal()

        try:
            # Store release data as JSON
            import json
            from datetime import datetime

            # Custom JSON encoder to handle datetime objects
            def json_serializer(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                raise TypeError(f"Type {type(obj)} not serializable")

            release_data = {
                "source": release.source,
                "title": release.title,
                "download_url": release.download_url,
                "protocol": release.protocol,
                "size_bytes": release.size_bytes,
                "seeders": release.seeders,
                "leechers": release.leechers,
                "indexer": release.indexer,
                "indexer_id": release.indexer_id,
                "category": release.category,
                "format": release.format,
                "language": release.language,
                "quality_score": release.quality_score,
                "publish_date": release.publish_date.isoformat() if release.publish_date else None,
                "metadata": release.metadata,
            }

            # Compute hash from download URL for tracking
            import hashlib
            info_hash = hashlib.sha256(release.download_url.encode()).hexdigest()[:16]

            task = DownloadTask(
                book_id=book.id,
                format=format_type,
                source=release.source,
                release_title=release.title,
                download_url=release.download_url,
                protocol=release.protocol,
                state="queued",
                progress=0.0,
                release_data_json=json.dumps(release_data, default=json_serializer),
                info_hash=info_hash,
            )

            db.add(task)
            db.commit()
            db.refresh(task)

            logger.info(
                "orchestrator_task_created",
                task_id=task.id,
                book_id=book.id,
                format=format_type,
                protocol=release.protocol,
                quality=release.quality_score
            )

            return task

        except Exception as e:
            logger.error(
                "orchestrator_create_task_failed",
                book_id=book.id,
                error=str(e)
            )
            db.rollback()
            return None

        finally:
            if not self.db_session:
                db.close()

    def start_download(self, task_id: int) -> bool:
        """
        Start a download task in a background thread.

        Args:
            task_id: Download task ID

        Returns:
            True if download started successfully
        """
        db = self.db_session or SessionLocal()

        try:
            task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
            if not task:
                logger.error("orchestrator_task_not_found", task_id=task_id)
                return False

            # Check if already downloading
            if task_id in self._active_downloads:
                logger.warning("orchestrator_already_downloading", task_id=task_id)
                return False

            # Create cancel event
            cancel_event = Event()
            self._active_downloads[task_id] = cancel_event

            # Start download thread
            thread = threading.Thread(
                target=self._execute_download,
                args=(task_id, cancel_event),
                daemon=True
            )
            self._download_threads[task_id] = thread
            thread.start()

            logger.info("orchestrator_download_started", task_id=task_id)
            return True

        except Exception as e:
            logger.error("orchestrator_start_failed", task_id=task_id, error=str(e))
            return False

        finally:
            if not self.db_session:
                db.close()

    def _execute_download(self, task_id: int, cancel_event: Event):
        """
        Execute download in background thread.

        Args:
            task_id: Download task ID
            cancel_event: Event to signal cancellation
        """
        db = SessionLocal()

        try:
            task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
            if not task:
                logger.error("orchestrator_execute_task_not_found", task_id=task_id)
                return

            # Update state to downloading
            task.state = "downloading"
            db.commit()

            # Get appropriate handler based on protocol
            if task.protocol == "torrent":
                handler_name = "torrent"
            elif task.protocol == "usenet":
                handler_name = "usenet"
            elif task.protocol == "direct":
                handler_name = "direct"
            else:
                logger.error("orchestrator_unknown_protocol", task_id=task_id, protocol=task.protocol)
                task.state = "error"
                task.message = f"Unknown protocol: {task.protocol}"
                db.commit()
                return

            HandlerClass = get_handler(handler_name)
            handler = HandlerClass(db_session=db)

            # Progress callback
            def progress_callback(progress: float):
                task.progress = progress
                db.commit()

            # Status callback
            def status_callback(status: DownloadStatus):
                task.state = status.state.value
                task.progress = status.progress
                if status.message:
                    task.message = status.message
                if status.client_state:
                    task.client_state = status.client_state
                db.commit()

                logger.info(
                    "orchestrator_download_progress",
                    task_id=task_id,
                    state=status.state.value,
                    progress=status.progress,
                    message=status.message,
                    client_state=status.client_state
                )

            # Execute download
            download_path = handler.download(
                task=task,
                cancel_flag=cancel_event,
                progress_callback=progress_callback,
                status_callback=status_callback,
            )

            if download_path:
                # Success - copy/hardlink to destination
                dest_path = self._copy_to_destination(task, download_path, db)

                # Update task with final path (use dest_path if copy succeeded, otherwise source)
                final_path = dest_path if dest_path else download_path

                task.state = "complete"
                task.progress = 100.0
                task.download_path = final_path
                db.commit()

                # Cleanup
                handler.cleanup(task, success=True)

                # Only update book availability if import succeeded (dest_path is not None)
                # This ensures books are only marked as available after successful import
                if dest_path:
                    self._update_book_availability(task, db)

                    # Audiobook imports run their own follow-up (Audiobookshelf
                    # match + email) from _import_audiobook. For an ebook that is
                    # already imported (no Calibre wait), promote + email now;
                    # Calibre-gated ebooks are handled by reconcile_calibre_library.
                    if task.format == "ebook" and task.import_status == "imported":
                        self._send_availability_email(task.book_id)

                logger.info(
                    "orchestrator_download_complete",
                    task_id=task_id,
                    source_path=download_path,
                    final_path=final_path
                )

            else:
                # Failed
                if task.state != "paused":  # Don't override paused state
                    task.state = "error"
                db.commit()

                # Cleanup
                handler.cleanup(task, success=False)

                logger.error("orchestrator_download_failed", task_id=task_id)

        except Exception as e:
            logger.error("orchestrator_execute_error", task_id=task_id, error=str(e))

            # Update task state
            try:
                task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
                if task:
                    task.state = "error"
                    db.commit()
            except:
                pass

        finally:
            # Clean up
            if task_id in self._active_downloads:
                del self._active_downloads[task_id]
            if task_id in self._download_threads:
                del self._download_threads[task_id]

            db.close()

    def _copy_to_destination(
        self,
        task: DownloadTask,
        source_path: str,
        db: Session,
        notify_audiobookshelf: bool = True,
    ) -> Optional[str]:
        """
        Post-process a completed download.

        - **Ebooks** are left wherever the download client saved them (its
          per-format "Ebook Download Path"); Calibre imports from that location.
          Nothing is moved.
        - **Audiobooks** are hardlinked/copied from the client's save path into
          the configured "Audiobook Media Path" using an Audiobookshelf-style
          layout (see :meth:`_import_audiobook`).

        Args:
            task: Download task
            source_path: Path where the file was downloaded by the client
            db: Database session

        Returns:
            Final path if successful, None otherwise
        """
        # Mark import as starting
        task.import_status = 'importing'
        task.import_message = 'Starting import...'
        db.commit()

        try:
            # Ebooks stay in the download client's path — no relocation step.
            if task.format == "ebook":
                self._set_import_result(
                    task, db,
                    message=f'Left in download client path: {Path(source_path).name}',
                )
                return source_path

            # --- Audiobooks: import into the Audiobook Media Path ---
            setting = db.query(AppSettings).filter(
                AppSettings.key == "audiobook_download_path"
            ).first()

            if not setting or not setting.value:
                logger.warning(
                    "orchestrator_no_destination_configured",
                    task_id=task.id,
                    format=task.format,
                )
                task.import_status = 'failed'
                task.import_message = 'No Audiobook Media Path configured'
                db.commit()
                return None

            dest_base = setting.value
            if not os.path.exists(dest_base):
                logger.error(
                    "orchestrator_destination_not_found",
                    task_id=task.id,
                    dest_base=dest_base,
                )
                task.import_status = 'failed'
                task.import_message = f'Audiobook Media Path does not exist: {dest_base}'
                db.commit()
                return None

            source = Path(source_path)
            if not source.exists():
                logger.error(
                    "orchestrator_source_not_found",
                    task_id=task.id,
                    source=source_path,
                )
                task.import_status = 'failed'
                task.import_message = f'Source file not found: {source_path}'
                db.commit()
                return None

            return self._import_audiobook(
                task, source, dest_base, db, self._use_hardlinks(task, db),
                notify_audiobookshelf=notify_audiobookshelf,
            )

        except Exception as e:
            logger.error(
                "orchestrator_copy_error",
                task_id=task.id,
                error=str(e)
            )
            # Mark import as failed
            task.import_status = 'failed'
            task.import_message = f'Import failed: {str(e)}'
            db.commit()
            return None

    def _use_hardlinks(self, task: DownloadTask, db: Session) -> bool:
        """Resolve the hardlink preference for this task's format.

        A format-specific setting (``use_hardlinks_ebook`` / ``use_hardlinks_audiobook``)
        wins; otherwise the global ``use_hardlinks`` applies. Default: on.
        """
        format_setting = db.query(AppSettings).filter(
            AppSettings.key == f"use_hardlinks_{task.format}"
        ).first()
        if format_setting is not None:
            return format_setting.value != "false"
        global_setting = db.query(AppSettings).filter(
            AppSettings.key == "use_hardlinks"
        ).first()
        return not (global_setting and global_setting.value == "false")

    def _import_audiobook(
        self,
        task: DownloadTask,
        source: Path,
        dest_base: str,
        db: Session,
        use_hardlinks: bool,
        notify_audiobookshelf: bool = True,
    ) -> Optional[str]:
        """Lay an audiobook download out Audiobookshelf-style under ``dest_base``:

            {dest_base}/{Author}/{Book Title}/{Author} - {Book Title}{ (NN)}{ext}

        Only audio files are carried over; a single-file book gets no ``(NN)``
        suffix, multi-file books are numbered in filename order, zero-padded.
        Returns the book folder path on success, ``None`` on failure.
        """
        book = db.query(Book).filter(Book.id == task.book_id).first()
        if not book:
            task.import_status = 'failed'
            task.import_message = 'Book not found for this download task'
            db.commit()
            return None

        # Gather audio tracks from the release (recursively for a folder).
        if source.is_file():
            tracks = [source] if source.suffix.lower() in AUDIO_EXTENSIONS else []
        else:
            tracks = sorted(
                (p for p in source.rglob("*")
                 if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS),
                key=lambda p: str(p).lower(),
            )

        if not tracks:
            logger.error(
                "orchestrator_audiobook_no_audio",
                task_id=task.id,
                source=str(source),
            )
            task.import_status = 'failed'
            task.import_message = f'No audio files found in download: {source.name}'
            db.commit()
            return None

        title = _sanitize_path_component(book.title)
        author = _sanitize_path_component(book.author)
        book_dir = Path(dest_base) / author / title
        book_dir.mkdir(parents=True, exist_ok=True)

        multi = len(tracks) > 1
        width = max(2, len(str(len(tracks))))

        imported = 0
        for idx, src_file in enumerate(tracks, start=1):
            suffix = f" ({idx:0{width}d})" if multi else ""
            dest_file = book_dir / f"{author} - {title}{suffix}{src_file.suffix.lower()}"

            if dest_file.exists():
                logger.info(
                    "orchestrator_audiobook_track_exists",
                    task_id=task.id,
                    dest=str(dest_file),
                )
                imported += 1
                continue

            try:
                if use_hardlinks:
                    try:
                        os.link(str(src_file), str(dest_file))
                    except (OSError, PermissionError) as e:
                        logger.info(
                            "orchestrator_audiobook_hardlink_failed_copying",
                            task_id=task.id,
                            error=str(e),
                        )
                        shutil.copy2(str(src_file), str(dest_file))
                else:
                    shutil.copy2(str(src_file), str(dest_file))
                imported += 1
            except Exception as e:
                logger.error(
                    "orchestrator_audiobook_track_error",
                    task_id=task.id,
                    src=str(src_file),
                    error=str(e),
                )

        if imported == 0:
            task.import_status = 'failed'
            task.import_message = 'Failed to import any audio files'
            db.commit()
            return None

        logger.info(
            "orchestrator_audiobook_imported",
            task_id=task.id,
            book_dir=str(book_dir),
            tracks=imported,
        )
        self._set_import_result(
            task, db,
            message=f'Imported {imported} audio file(s) to: {book_dir.name}',
        )

        # Follow-up runs on a daemon thread so the download thread isn't blocked:
        # Audiobookshelf rescan + match/link the ingested item, then promote the
        # request and send its availability email. Skipped when the caller runs
        # its own Audiobookshelf notify (it emails afterwards too).
        if notify_audiobookshelf:
            self._after_audiobook_import(book.id, db)

        return str(book_dir)

    def _after_audiobook_import(self, book_id: int, db: Session) -> None:
        """Background follow-up after an audiobook import.

        1. If an Audiobookshelf server is configured: trigger a rescan, then
           poll/match the newly ingested item and link it to the book.
        2. Promote the matching request to ``available`` and email the user now,
           rather than waiting for the periodic jobs.

        Runs on a daemon thread — the Audiobookshelf match polls with sleeps —
        so the download thread returns immediately.
        """
        try:
            from ..routers.audiobookshelf import get_default_audiobookshelf_server
            abs_configured = get_default_audiobookshelf_server(db) is not None
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(
                "orchestrator_abs_notify_setup_failed", book_id=book_id, error=str(e)
            )
            abs_configured = False

        def _run() -> None:
            import asyncio
            from datetime import datetime, timezone
            from ..database import SessionLocal

            async def _work() -> None:
                if abs_configured:
                    try:
                        from ..routers.audiobookshelf import (
                            get_default_audiobookshelf_server,
                            get_audiobookshelf_max_added_at,
                            trigger_audiobookshelf_scan,
                            link_and_match_new_audiobook,
                        )
                        inner_db = SessionLocal()
                        try:
                            server = get_default_audiobookshelf_server(inner_db)
                            server_id = server.id if server else None
                            if server is not None:
                                baseline_ms = await get_audiobookshelf_max_added_at(server)
                                if not baseline_ms:
                                    baseline_ms = int(
                                        (datetime.now(timezone.utc).timestamp() - 120) * 1000
                                    )
                                await trigger_audiobookshelf_scan(server)
                        finally:
                            inner_db.close()
                        if server_id is not None:
                            await link_and_match_new_audiobook(server_id, book_id, baseline_ms)
                    except Exception as e:  # pragma: no cover - network/defensive
                        logger.warning(
                            "orchestrator_abs_match_failed", book_id=book_id, error=str(e)
                        )

                # Always run — even if the Audiobookshelf step failed, the
                # download itself succeeded and the request should go available.
                from ..tasks import promote_and_email
                await promote_and_email(book_id, "audiobook")

            try:
                asyncio.run(_work())
            except Exception as e:  # pragma: no cover - network/defensive
                logger.warning(
                    "orchestrator_after_audiobook_import_failed",
                    book_id=book_id, error=str(e),
                )

        threading.Thread(
            target=_run, daemon=True, name=f"after-audiobook-import-{book_id}"
        ).start()

    def _send_availability_email(self, book_id: Optional[int], fmt: str = "ebook") -> None:
        """Promote a freshly-imported request and email the user now, on a daemon
        thread. Idempotent — safe alongside the periodic jobs."""
        def _run() -> None:
            import asyncio
            try:
                from ..tasks import promote_and_email
                asyncio.run(promote_and_email(book_id, fmt))
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "orchestrator_availability_email_failed", book_id=book_id, error=str(e)
                )

        threading.Thread(
            target=_run, daemon=True, name=f"availability-email-{book_id}"
        ).start()

    def _set_import_result(self, task: DownloadTask, db: Session, message: str) -> None:
        """Record the outcome of a successful copy.

        For ebooks, when a Calibre library is configured the task is parked in
        'awaiting_library' until the file is indexed there (see
        tasks.reconcile_ebook_library_imports). Audiobooks — and ebooks with no
        Calibre library — are marked 'imported' immediately.
        """
        from datetime import datetime, timezone

        if task.format == "ebook" and _calibre_library_active(db):
            task.import_status = 'awaiting_library'
            task.import_message = f'{message} — waiting for Calibre to index it'
            task.imported_at = None
        else:
            task.import_status = 'imported'
            task.import_message = message
            task.imported_at = datetime.now(timezone.utc)
        db.commit()

    def _update_book_availability(self, task: DownloadTask, db: Session):
        """
        Update book availability after successful download.

        Args:
            task: Completed download task
            db: Database session
        """
        try:
            book = db.query(Book).filter(Book.id == task.book_id).first()
            if not book:
                return

            # Update availability flags. For ebooks that are still waiting to be
            # indexed by Calibre, hold off until the import is actually confirmed
            # (reconcile_ebook_library_imports flips it and sets this flag then).
            if task.format == "ebook":
                if task.import_status == "imported":
                    book.ebook_available = True
            elif task.format == "audiobook":
                book.audiobook_available = True

            # Store the download hashes for duplicate detection
            import json
            import hashlib

            try:
                hashes = json.loads(book.downloaded_release_hashes) if book.downloaded_release_hashes else []
            except (json.JSONDecodeError, TypeError):
                hashes = []

            hashes_to_add = []

            # Add torrent/NZB info hash if available
            if task.info_hash:
                hashes_to_add.append(task.info_hash)

            # Also add URL-based hash for Prowlarr duplicate detection
            if task.download_url:
                url_hash = hashlib.sha256(task.download_url.encode()).hexdigest()[:16]
                hashes_to_add.append(url_hash)

            # Add new hashes
            for hash_val in hashes_to_add:
                if hash_val not in hashes:
                    hashes.append(hash_val)

            book.downloaded_release_hashes = json.dumps(hashes)

            db.commit()

            logger.info(
                "orchestrator_book_updated",
                book_id=book.id,
                format=task.format,
                available=True,
                hash_stored=task.info_hash is not None
            )

        except Exception as e:
            logger.error(
                "orchestrator_update_book_failed",
                task_id=task.id,
                error=str(e)
            )

    def cancel_download(self, task_id: int) -> bool:
        """
        Cancel an active download.

        Args:
            task_id: Download task ID

        Returns:
            True if cancelled successfully
        """
        if task_id not in self._active_downloads:
            logger.warning("orchestrator_cancel_not_active", task_id=task_id)
            return False

        try:
            # Signal cancellation
            cancel_event = self._active_downloads[task_id]
            cancel_event.set()

            logger.info("orchestrator_download_cancelled", task_id=task_id)
            return True

        except Exception as e:
            logger.error("orchestrator_cancel_failed", task_id=task_id, error=str(e))
            return False

    def pause_download(self, task_id: int) -> bool:
        """
        Pause a download.

        Args:
            task_id: Download task ID

        Returns:
            True if paused successfully
        """
        db = self.db_session or SessionLocal()

        try:
            task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
            if not task:
                return False

            # Get handler
            handler_name = "torrent" if task.protocol == "torrent" else "usenet"
            HandlerClass = get_handler(handler_name)
            handler = HandlerClass(db_session=db)

            # Pause in client
            success = handler.pause(task)

            if success:
                task.state = "paused"
                db.commit()

                # Also cancel the monitoring thread
                if task_id in self._active_downloads:
                    self._active_downloads[task_id].set()

            return success

        except Exception as e:
            logger.error("orchestrator_pause_failed", task_id=task_id, error=str(e))
            return False

        finally:
            if not self.db_session:
                db.close()

    def resume_download(self, task_id: int) -> bool:
        """
        Resume a paused download.

        Args:
            task_id: Download task ID

        Returns:
            True if resumed successfully
        """
        db = self.db_session or SessionLocal()

        try:
            task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
            if not task:
                return False

            # Get handler
            handler_name = "torrent" if task.protocol == "torrent" else "usenet"
            HandlerClass = get_handler(handler_name)
            handler = HandlerClass(db_session=db)

            # Resume in client
            success = handler.resume(task)

            if success:
                # Restart monitoring
                return self.start_download(task_id)

            return False

        except Exception as e:
            logger.error("orchestrator_resume_failed", task_id=task_id, error=str(e))
            return False

        finally:
            if not self.db_session:
                db.close()

    def search_and_download(
        self,
        book: Book,
        format_type: str = "ebook",
        source_name: str = "prowlarr"
    ) -> Optional[DownloadTask]:
        """
        Complete workflow: search, select best release, and start download.

        Args:
            book: Book to download
            format_type: "ebook" or "audiobook"
            source_name: Source to use (default: "prowlarr")

        Returns:
            Created DownloadTask or None
        """
        # Search for releases
        releases = self.search_releases(book, format_type, source_name)

        if not releases:
            logger.warning(
                "orchestrator_no_releases",
                book_id=book.id,
                format=format_type
            )
            return None

        # Select best release (already sorted by quality)
        best_release = releases[0]

        logger.info(
            "orchestrator_selected_release",
            book_id=book.id,
            release_title=best_release.title,
            quality=best_release.quality_score,
            protocol=best_release.protocol
        )

        # Create download task
        task = self.create_download_task(book, best_release, format_type)

        if not task:
            return None

        # Start download
        success = self.start_download(task.id)

        if not success:
            logger.error("orchestrator_start_download_failed", task_id=task.id)
            return None

        return task

    def get_active_downloads(self) -> List[int]:
        """
        Get list of active download task IDs.

        Returns:
            List of task IDs
        """
        return list(self._active_downloads.keys())

    def is_downloading(self, task_id: int) -> bool:
        """
        Check if a task is currently downloading.

        Args:
            task_id: Download task ID

        Returns:
            True if actively downloading
        """
        return task_id in self._active_downloads

    def get_download_count(self) -> int:
        """
        Get number of active downloads.

        Returns:
            Active download count
        """
        return len(self._active_downloads)
