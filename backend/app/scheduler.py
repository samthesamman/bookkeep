"""
APScheduler-based job scheduler for Book Hound.
Handles all background tasks with proper scheduling, persistence, and dynamic updates.
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

logger = structlog.get_logger(__name__)

# Global scheduler instance
scheduler: Optional[AsyncIOScheduler] = None

# Job definitions with their default intervals (in seconds)
JOB_DEFINITIONS = {
    "refresh_seed_data": {
        "default_interval": 24 * 60 * 60,  # 24 hours
        "description": "Fetch new books from Hardcover API",
        "type": "PROCESS",
    },
    "check_processing_requests": {
        "default_interval": 5 * 60,  # 5 minutes
        "description": "Check download tasks for completed requests",
        "type": "PROCESS",
    },
    "sync_from_booklore": {
        "default_interval": 24 * 60 * 60,  # 24 hours
        "description": "Import books from Booklore library",
        "type": "PROCESS",
    },
    "import_audiobookshelf_books": {
        "default_interval": 24 * 60 * 60,  # 24 hours
        "description": "Import audiobooks from Audiobookshelf library",
        "type": "PROCESS",
    },
    "sync_audiobook_metadata": {
        "default_interval": 6 * 60 * 60,  # 6 hours
        "description": "Fetch missing Hardcover metadata for non-Calibre-linked books (mainly audiobooks)",
        "type": "PROCESS",
    },
    "sync_download_states": {
        "default_interval": 2 * 60,  # 2 minutes
        "description": "Sync download states from download clients",
        "type": "PROCESS",
    },
    "sync_hardcover_lists": {
        "default_interval": 6 * 60 * 60,  # 6 hours
        "description": "Sync Hardcover to-read/list books and auto-request them",
        "type": "PROCESS",
    },
    "refresh_nyt_bestsellers": {
        "default_interval": 24 * 60 * 60,  # 24 hours
        "description": "Refresh NYT Best Sellers lists shown on the Discover page",
        "type": "PROCESS",
    },
    "send_availability_emails": {
        "default_interval": 5 * 60,  # 5 minutes
        "description": "Email available books to users who opted in when requesting",
        "type": "PROCESS",
    },
    "sync_ebook_availability": {
        "default_interval": 24 * 60 * 60,  # 24 hours
        "description": "Mark ebook requests available once they appear in the Calibre library",
        "type": "PROCESS",
    },
    "import_calibre_books": {
        "default_interval": 24 * 60 * 60,  # 24 hours
        "description": "Heal Calibre links and import side-loaded books not yet in Bookworms",
        "type": "PROCESS",
        "run_on_startup": True,
        "startup_delay_seconds": 30,
    },
    "sync_calibre_metadata": {
        "default_interval": 24 * 60 * 60,  # 24 hours
        "description": "Batch-fetch missing metadata for Calibre library books",
        "type": "PROCESS",
        "run_on_startup": True,
        # A little after import_calibre_books, so books it links today are
        # usually enriched the same day instead of waiting for tomorrow's run.
        "startup_delay_seconds": 90,
    },
}


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the global scheduler instance"""
    global scheduler
    if scheduler is None:
        scheduler = AsyncIOScheduler(
            jobstores={'default': MemoryJobStore()},
            job_defaults={
                'coalesce': True,  # Combine missed runs into one
                'max_instances': 1,  # Only one instance of each job at a time
                'misfire_grace_time': 60 * 60,  # 1 hour grace time
            },
            timezone='UTC',  # Explicitly use UTC for consistency
        )
    return scheduler


def start_scheduler():
    """Start the scheduler"""
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        logger.info("scheduler_started")
        
        # Add event listeners
        sched.add_listener(on_job_executed, EVENT_JOB_EXECUTED)
        sched.add_listener(on_job_error, EVENT_JOB_ERROR)
        sched.add_listener(on_job_missed, EVENT_JOB_MISSED)


def on_job_executed(event):
    """Called when a job is executed successfully"""
    logger.info("job_executed", job_id=event.job_id)
    update_job_in_db(event.job_id)


def on_job_error(event):
    """Called when a job raises an exception"""
    logger.error("job_error", job_id=event.job_id, error=str(event.exception))


def on_job_missed(event):
    """Called when a job's execution was missed"""
    logger.warning("job_missed", job_id=event.job_id)


def update_job_in_db(job_name: str):
    """Update job execution times in database after a run"""
    from app.database import SessionLocal
    from app.models import JobSchedule
    
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule:
            schedule.last_execution = datetime.now()
            # Get next run time from scheduler
            sched = get_scheduler()
            job = sched.get_job(job_name)
            if job and job.next_run_time:
                schedule.next_execution = job.next_run_time.replace(tzinfo=None)
            else:
                interval = schedule.interval_seconds or JOB_DEFINITIONS.get(job_name, {}).get("default_interval", 3600)
                schedule.next_execution = datetime.now() + timedelta(seconds=interval)
            db.commit()
    except Exception as e:
        logger.warning("update_job_in_db_error", job_name=job_name, error=str(e))
        db.rollback()
    finally:
        db.close()


def add_job(job_name: str, func: Callable, interval_seconds: int):
    """Add or replace a job with the given interval.

    ``interval_seconds <= 0`` means "manual only": the job is left out of the
    scheduler entirely (nothing to auto-fire), but the admin Jobs page's "Run
    Now" doesn't go through the scheduler at all - it calls the job function
    directly - so manual runs keep working either way.
    """
    sched = get_scheduler()

    # Remove existing job if it exists
    if sched.get_job(job_name):
        sched.remove_job(job_name)

    if interval_seconds <= 0:
        logger.info("job_manual_only", job_name=job_name)
        return

    # Add the job with interval trigger
    trigger = IntervalTrigger(seconds=interval_seconds)
    sched.add_job(
        func,
        trigger=trigger,
        id=job_name,
        name=job_name,
        replace_existing=True,
    )

    logger.info("job_added", job_name=job_name, interval_seconds=interval_seconds)

    # Update next_execution in database
    update_next_execution_in_db(job_name)


def update_next_execution_in_db(job_name: str):
    """Update the next_execution in DB from scheduler"""
    from app.database import SessionLocal
    from app.models import JobSchedule
    
    sched = get_scheduler()
    job = sched.get_job(job_name)
    
    if not job:
        return
    
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule and job.next_run_time:
            schedule.next_execution = job.next_run_time.replace(tzinfo=None)
            db.commit()
    except Exception as e:
        logger.warning("update_next_execution_error", job_name=job_name, error=str(e))
        db.rollback()
    finally:
        db.close()


def reschedule_job(job_name: str, interval_seconds: int):
    """Reschedule a job with a new interval, including into/out of "manual only"
    (``interval_seconds <= 0``, meaning: not auto-scheduled, only run via the
    admin Jobs page's "Run Now")."""
    from app.database import SessionLocal
    from app.models import JobSchedule

    sched = get_scheduler()
    job = sched.get_job(job_name)
    next_run = None

    if interval_seconds <= 0:
        if job:
            sched.remove_job(job_name)
        logger.info("job_rescheduled_manual_only", job_name=job_name)
    else:
        trigger = IntervalTrigger(seconds=interval_seconds)
        if job:
            sched.reschedule_job(job_name, trigger=trigger)
        else:
            # Was manual-only (so not in the scheduler at all) - add it back.
            func = _job_functions().get(job_name)
            if func:
                sched.add_job(
                    func, trigger=trigger, id=job_name, name=job_name, replace_existing=True
                )
            else:
                logger.warning("job_function_not_found_for_reschedule", job_name=job_name)

        updated_job = sched.get_job(job_name)
        if updated_job and updated_job.next_run_time:
            next_run = updated_job.next_run_time.replace(tzinfo=None)

        logger.info("job_rescheduled",
                   job_name=job_name,
                   interval_seconds=interval_seconds,
                   next_run_time=next_run.isoformat() if next_run else None)

    # Update database directly
    db = SessionLocal()
    try:
        schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
        if schedule:
            schedule.interval_seconds = interval_seconds
            schedule.next_execution = next_run
            db.commit()
            logger.info("job_db_updated", job_name=job_name, next_execution=next_run)
    except Exception as e:
        logger.error("job_db_update_failed", job_name=job_name, error=str(e))
        db.rollback()
    finally:
        db.close()


def get_job_info(job_name: str) -> Optional[Dict[str, Any]]:
    """Get information about a scheduled job"""
    sched = get_scheduler()
    
    if not sched.running:
        logger.debug("scheduler_not_running")
        return None
    
    job = sched.get_job(job_name)
    
    if not job:
        logger.debug("job_not_in_scheduler", job_name=job_name)
        return None
    
    next_run = job.next_run_time
    if next_run:
        # Convert to naive datetime
        next_run = next_run.replace(tzinfo=None)
    
    # Get interval from trigger
    interval = None
    if hasattr(job.trigger, 'interval'):
        interval = int(job.trigger.interval.total_seconds())
    
    return {
        "name": job_name,
        "next_run_time": next_run.isoformat() if next_run else None,
        "interval_seconds": interval,
    }


def get_all_jobs() -> Dict[str, Dict[str, Any]]:
    """Get information about all scheduled jobs"""
    sched = get_scheduler()
    jobs = {}
    
    for job in sched.get_jobs():
        info = get_job_info(job.id)
        if info:
            jobs[job.id] = info
    
    return jobs


def run_job_now(job_name: str):
    """Trigger a job to run immediately"""
    sched = get_scheduler()
    job = sched.get_job(job_name)
    
    if job:
        # Modify the job to run now
        sched.modify_job(job_name, next_run_time=datetime.now())
        logger.info("job_triggered_now", job_name=job_name)
    else:
        logger.warning("job_not_found_for_run", job_name=job_name)


def pause_job(job_name: str):
    """Pause a job"""
    sched = get_scheduler()
    sched.pause_job(job_name)
    logger.info("job_paused", job_name=job_name)


def resume_job(job_name: str):
    """Resume a paused job"""
    sched = get_scheduler()
    sched.resume_job(job_name)
    logger.info("job_resumed", job_name=job_name)


def _job_functions() -> Dict[str, Callable]:
    """Map job names to their async functions (lazy import to avoid a cycle)."""
    from app.tasks import (
        refresh_seed_data,
        check_processing_requests,
        sync_from_booklore,
        import_audiobookshelf_books,
        sync_audiobook_metadata,
        sync_download_states,
        sync_hardcover_lists,
        send_availability_emails,
        sync_ebook_availability,
        import_calibre_books,
        sync_calibre_metadata,
        refresh_nyt_bestsellers,
    )

    return {
        "refresh_seed_data": refresh_seed_data,
        "check_processing_requests": check_processing_requests,
        "sync_from_booklore": sync_from_booklore,
        "import_audiobookshelf_books": import_audiobookshelf_books,
        "sync_audiobook_metadata": sync_audiobook_metadata,
        "sync_download_states": sync_download_states,
        "sync_hardcover_lists": sync_hardcover_lists,
        "send_availability_emails": send_availability_emails,
        "sync_ebook_availability": sync_ebook_availability,
        "import_calibre_books": import_calibre_books,
        "sync_calibre_metadata": sync_calibre_metadata,
        "refresh_nyt_bestsellers": refresh_nyt_bestsellers,
    }


async def initialize_jobs():
    """Initialize all jobs from database or defaults"""
    from app.database import SessionLocal
    from app.models import JobSchedule

    job_functions = _job_functions()

    db = SessionLocal()
    try:
        for job_name, definition in JOB_DEFINITIONS.items():
            # Get interval from database or use default
            schedule = db.query(JobSchedule).filter(JobSchedule.job_name == job_name).first()
            
            if schedule:
                interval = schedule.interval_seconds
            else:
                interval = definition["default_interval"]
                # Create schedule entry
                schedule = JobSchedule(
                    job_name=job_name,
                    interval_seconds=interval,
                    is_enabled=True,
                )
                db.add(schedule)
            
            # Add job to scheduler
            func = job_functions.get(job_name)
            if func:
                add_job(job_name, func, interval)
                # Manual-only jobs (interval <= 0) aren't in the scheduler at
                # all - nothing to nudge, and "manual only" means startup
                # shouldn't auto-run it either.
                if definition.get("run_on_startup") and interval > 0:
                    # Fire once shortly after boot, then fall back to the interval.
                    delay = definition.get("startup_delay_seconds", 30)
                    try:
                        get_scheduler().modify_job(
                            job_name,
                            next_run_time=datetime.now() + timedelta(seconds=delay),
                        )
                        update_next_execution_in_db(job_name)
                    except Exception as e:
                        logger.warning("job_startup_run_failed", job_name=job_name, error=str(e))
                logger.info("job_initialized", job_name=job_name, interval_seconds=interval)
        
        db.commit()
    except Exception as e:
        logger.error("initialize_jobs_error", error=str(e))
        db.rollback()
    finally:
        db.close()

