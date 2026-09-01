"""Send book files to users over SMTP.

SMTP configuration lives in ``app_settings`` (see ``routers/settings.py``); the
password is stored encrypted. Sending is done synchronously with the stdlib
``smtplib`` — fine for the low volume this feature sees on a self-hosted box.
"""
from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

import structlog
from sqlalchemy.orm import Session

from app import models
from app.routers.settings import get_setting_value

logger = structlog.get_logger(__name__)

# app_settings keys
SMTP_KEYS = (
    "smtp_host",
    "smtp_port",
    "smtp_encryption",  # "none" | "ssl" | "starttls"
    "smtp_username",
    "smtp_from_address",
    "smtp_password",
)

# Attachments larger than this are rejected before we try to send.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


class EmailError(Exception):
    """Raised when an email cannot be sent (misconfiguration or SMTP failure)."""


class SmtpConfig:
    def __init__(
        self,
        host: str,
        port: int,
        encryption: str,
        username: Optional[str],
        password: Optional[str],
        from_address: str,
    ):
        self.host = host
        self.port = port
        self.encryption = encryption
        self.username = username or None
        self.password = password or None
        self.from_address = from_address

    @property
    def configured(self) -> bool:
        return bool(self.host and self.port and self.from_address)


def get_smtp_config(db: Session) -> SmtpConfig:
    """Load the SMTP configuration from settings (env vars win, see get_setting_value)."""
    host = get_setting_value(db, "smtp_host") or ""
    port_raw = get_setting_value(db, "smtp_port") or ""
    encryption = (get_setting_value(db, "smtp_encryption") or "starttls").lower()
    username = get_setting_value(db, "smtp_username")
    password = get_setting_value(db, "smtp_password")
    from_address = get_setting_value(db, "smtp_from_address") or username or ""

    try:
        port = int(port_raw) if port_raw else (465 if encryption == "ssl" else 587)
    except (TypeError, ValueError):
        port = 587

    return SmtpConfig(host, port, encryption, username, password, from_address)


def _deliver(config: SmtpConfig, message: EmailMessage) -> None:
    """Open a connection and send a prepared message, translating errors."""
    timeout = 30
    try:
        if config.encryption == "ssl":
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(config.host, config.port, timeout=timeout, context=context) as server:
                if config.username and config.password:
                    server.login(config.username, config.password)
                server.send_message(message)
        else:
            with smtplib.SMTP(config.host, config.port, timeout=timeout) as server:
                server.ehlo()
                if config.encryption == "starttls":
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if config.username and config.password:
                    server.login(config.username, config.password)
                server.send_message(message)
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        raise EmailError(f"SMTP delivery failed: {exc}") from exc


def send_test_email(db: Session, recipient: str) -> None:
    """Send a short diagnostic email. Raises EmailError on any problem."""
    config = get_smtp_config(db)
    if not config.configured:
        raise EmailError("SMTP is not configured. Set the host, port and sender address first.")
    if not recipient:
        raise EmailError("No recipient address to send the test email to.")

    message = EmailMessage()
    message["Subject"] = "Bookkeep SMTP test"
    message["From"] = config.from_address
    message["To"] = recipient
    message.set_content(
        "This is a test message from Bookkeep. "
        "If you received it, your SMTP settings are working."
    )
    _deliver(config, message)
    logger.info("smtp_test_email_sent", recipient=recipient)


def send_availability_notification(
    db: Session,
    user: models.User,
    book_title: str,
    book_format: str,
) -> models.EmailLog:
    """Tell the user a requested title is now available — no file attached.

    Sent for every request (ebook or audiobook) once it becomes available.
    Because nothing is delivered here, it goes to the user's account email
    address rather than the configured book-delivery address (that address is
    reserved for actual ebook file delivery, see ``send_book_email``). Always
    writes an EmailLog row and returns it; raises EmailError if the message
    could not be sent.
    """
    recipient = (user.email or "").strip()
    label = (book_format or "book").strip() or "book"
    subject = f'"{book_title}" is now available' if book_title else "Your request is now available"

    log = models.EmailLog(
        user_id=user.id,
        recipient=recipient or "(not set)",
        subject=subject,
        book_title=book_title or None,
        book_format=book_format or None,
        status="success",
    )

    def _fail(msg: str) -> None:
        log.status = "error"
        log.error_message = msg
        db.add(log)
        db.commit()
        db.refresh(log)
        raise EmailError(msg)

    if not recipient:
        _fail("The user has no account email address to notify.")

    config = get_smtp_config(db)
    if not config.configured:
        _fail("SMTP is not configured. Ask an administrator to set it up under Settings.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.from_address
    message["To"] = recipient
    message.set_content(
        f'Good news — the {label} "{book_title or "you requested"}" is now '
        f'available in the library.'
    )
    try:
        _deliver(config, message)
    except EmailError as exc:
        _fail(str(exc))

    db.add(log)
    db.commit()
    db.refresh(log)
    logger.info(
        "availability_notification_sent",
        user_id=user.id,
        recipient=recipient,
        book_title=book_title,
        book_format=book_format,
    )
    return log


def send_book_email(
    db: Session,
    user: models.User,
    file_path: str,
    download_name: str,
    media_type: str,
    book_title: str,
    book_format: str,
) -> models.EmailLog:
    """Email a book file to the user's configured delivery address.

    Always writes an EmailLog row (success or error) and returns it. Raises
    EmailError if the message could not be sent.
    """
    recipient = (user.book_delivery_email or "").strip()
    subject = f"{book_title}" if book_title else download_name

    log = models.EmailLog(
        user_id=user.id,
        recipient=recipient or "(not set)",
        subject=subject,
        book_title=book_title or None,
        book_format=book_format or None,
        status="success",
    )

    def _fail(msg: str) -> None:
        log.status = "error"
        log.error_message = msg
        db.add(log)
        db.commit()
        db.refresh(log)
        raise EmailError(msg)

    if not recipient:
        _fail("No delivery email address is set. Add one under Settings.")

    config = get_smtp_config(db)
    if not config.configured:
        _fail("SMTP is not configured. Ask an administrator to set it up under Settings.")

    try:
        size = os.path.getsize(file_path)
    except OSError as exc:
        _fail(f"Could not read the book file: {exc}")
        return log  # unreachable, keeps type-checkers happy

    if size > MAX_ATTACHMENT_BYTES:
        _fail(
            f"The {book_format} file is {size / 1024 / 1024:.1f} MB, over the "
            f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB email attachment limit."
        )

    maintype, _, subtype = (media_type or "application/octet-stream").partition("/")
    if not subtype:
        guessed, _ = mimetypes.guess_type(download_name)
        maintype, _, subtype = (guessed or "application/octet-stream").partition("/")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.from_address
    message["To"] = recipient
    message.set_content(
        f'Attached is "{book_title or download_name}" ({book_format}) from your library.'
    )
    try:
        with open(file_path, "rb") as fh:
            message.add_attachment(
                fh.read(),
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=download_name,
            )
    except OSError as exc:
        _fail(f"Could not attach the book file: {exc}")

    try:
        _deliver(config, message)
    except EmailError as exc:
        _fail(str(exc))

    db.add(log)
    db.commit()
    db.refresh(log)
    logger.info(
        "book_email_sent",
        user_id=user.id,
        recipient=recipient,
        book_title=book_title,
        book_format=book_format,
    )
    return log
