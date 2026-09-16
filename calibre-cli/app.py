"""calibre-cli: a small HTTP shim around calibredb / ebook-convert.

Deploy this on (or near) the machine that actually hosts the Calibre library,
with a real running Calibre Content Server (``calibre-server``, auth
enabled) and a *local* (non-network) filesystem path to the library. bookkeep
itself only ever has read-only network access to the library, so it talks to
this service over HTTP instead of writing to metadata.db directly.

Writes (metadata, cover) go through the Content Server via
``calibredb --with-library=http://...`` so Calibre's own DB layer handles
locking/sort-columns/triggers correctly. Conversion needs local file access
(ebook-convert has no remote mode), so it reads the library folder directly
and then registers the converted file back through the Content Server via
``calibredb add_format``.

See README.md for configuration and deployment notes.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("calibre-cli")

CALIBRE_SERVER_URL = os.environ.get("CALIBRE_SERVER_URL", "http://localhost:8081").rstrip("/")
CALIBRE_SERVER_USERNAME = os.environ.get("CALIBRE_SERVER_USERNAME", "")
CALIBRE_SERVER_PASSWORD = os.environ.get("CALIBRE_SERVER_PASSWORD", "")
CALIBRE_LIBRARY_ID = os.environ.get("CALIBRE_LIBRARY_ID", "")
CALIBRE_LIBRARY_LOCAL_PATH = os.environ.get("CALIBRE_LIBRARY_LOCAL_PATH", "/config/Calibre Library")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
CALIBREDB_TIMEOUT_SECONDS = int(os.environ.get("CALIBREDB_TIMEOUT_SECONDS", "60"))
CONVERT_TIMEOUT_SECONDS = int(os.environ.get("CONVERT_TIMEOUT_SECONDS", "300"))
CALIBREDB_BIN = os.environ.get("CALIBREDB_BIN", "calibredb")
EBOOK_CONVERT_BIN = os.environ.get("EBOOK_CONVERT_BIN", "ebook-convert")

if not AGENT_API_KEY:
    sys.exit("AGENT_API_KEY must be set — refusing to start with no shared secret configured.")

# Ebook formats this Calibre library holds, in the order to prefer as a
# conversion source when a book has more than one.
INPUT_FORMAT_PREFERENCE = ["EPUB", "AZW3", "MOBI", "AZW", "PDF", "FB2", "DOCX", "TXT", "RTF"]

COVER_OPF_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata/>
  <guide>
    <reference type="cover" href="cover.jpg" title="Cover"/>
  </guide>
</package>
"""

app = FastAPI(title="calibre-cli")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
async def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not x_api_key or x_api_key != AGENT_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Api-Key")


# ---------------------------------------------------------------------------
# calibredb subprocess helpers
# ---------------------------------------------------------------------------
def _with_library_arg() -> str:
    if CALIBRE_LIBRARY_ID:
        return f"{CALIBRE_SERVER_URL}/#{CALIBRE_LIBRARY_ID}"
    return CALIBRE_SERVER_URL


def _run_calibredb(args: List[str], timeout: int = CALIBREDB_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    cmd = [CALIBREDB_BIN, *args, f"--with-library={_with_library_arg()}"]
    if CALIBRE_SERVER_USERNAME:
        cmd.append(f"--username={CALIBRE_SERVER_USERNAME}")
    if CALIBRE_SERVER_PASSWORD:
        cmd.append(f"--password={CALIBRE_SERVER_PASSWORD}")
    logger.info("running calibredb %s", " ".join(args))
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"calibredb not found on this host: {exc}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="calibredb timed out")


def _raise_for_failure(result: subprocess.CompletedProcess, action: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error")[-2000:]
        logger.warning("%s failed: %s", action, detail)
        raise HTTPException(status_code=502, detail=f"{action} failed: {detail}")


def _field_args(fields: Dict[str, Any]) -> List[str]:
    """Convert a JSON field dict into ``calibredb set_metadata --field`` args."""
    args: List[str] = []
    for name, value in fields.items():
        if value is None:
            continue
        if name == "identifiers" and isinstance(value, dict):
            joined = ",".join(f"{k}:{v}" for k, v in value.items() if v)
            if joined:
                args += ["--field", f"identifiers:{joined}"]
        elif isinstance(value, list):
            joined = ",".join(str(v) for v in value if v)
            if joined:
                args += ["--field", f"{name}:{joined}"]
        elif str(value).strip():
            args += ["--field", f"{name}:{value}"]
    return args


# ---------------------------------------------------------------------------
# Local library reads (for locating a book's existing format file to convert)
# ---------------------------------------------------------------------------
def _book_dir_and_formats(calibre_id: int) -> Tuple[str, List[Tuple[str, str]]]:
    if not CALIBRE_LIBRARY_LOCAL_PATH:
        raise HTTPException(status_code=500, detail="CALIBRE_LIBRARY_LOCAL_PATH is not configured")
    db_path = os.path.join(CALIBRE_LIBRARY_LOCAL_PATH, "metadata.db")
    if not os.path.isfile(db_path):
        raise HTTPException(status_code=500, detail=f"metadata.db not found at {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute("SELECT path FROM books WHERE id = ?", (calibre_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Book not found in local library")
        book_dir = os.path.join(CALIBRE_LIBRARY_LOCAL_PATH, row[0])
        formats = conn.execute(
            "SELECT format, name FROM data WHERE book = ?", (calibre_id,)
        ).fetchall()
    finally:
        conn.close()
    return book_dir, [(fmt, name) for fmt, name in formats]


def _pick_input_file(book_dir: str, formats: List[Tuple[str, str]], target_format: str) -> str:
    target_upper = target_format.upper().lstrip(".")
    available = {fmt.upper(): name for fmt, name in formats}
    if target_upper in available:
        raise HTTPException(status_code=400, detail=f"Book already has a {target_upper} format")
    for fmt in INPUT_FORMAT_PREFERENCE:
        if fmt in available:
            return os.path.join(book_dir, f"{available[fmt]}.{fmt.lower()}")
    if available:
        fmt, name = next(iter(available.items()))
        return os.path.join(book_dir, f"{name}.{fmt.lower()}")
    raise HTTPException(status_code=404, detail="No existing format file found to convert from")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class MetadataRequest(BaseModel):
    fields: Dict[str, Any]


class ConvertRequest(BaseModel):
    target_format: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "calibre_server_configured": bool(CALIBRE_SERVER_URL),
        "local_library_configured": bool(CALIBRE_LIBRARY_LOCAL_PATH),
    }


@app.post("/books/{calibre_id}/metadata", dependencies=[Depends(require_api_key)])
async def push_metadata(calibre_id: int, body: MetadataRequest) -> dict:
    args = _field_args(body.fields)
    if not args:
        return {"success": True, "skipped": True}
    result = _run_calibredb(["set_metadata", str(calibre_id), *args])
    _raise_for_failure(result, "set_metadata")
    return {"success": True}


@app.post("/books/{calibre_id}/cover", dependencies=[Depends(require_api_key)])
async def push_cover(calibre_id: int, request: Request) -> dict:
    image_bytes = await request.body()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty request body")
    with tempfile.TemporaryDirectory() as tmp:
        cover_path = os.path.join(tmp, "cover.jpg")
        opf_path = os.path.join(tmp, "metadata.opf")
        with open(cover_path, "wb") as f:
            f.write(image_bytes)
        with open(opf_path, "w") as f:
            f.write(COVER_OPF_TEMPLATE)
        result = _run_calibredb(["set_metadata", str(calibre_id), opf_path])
    _raise_for_failure(result, "cover set_metadata")
    return {"success": True}


@app.post("/books/{calibre_id}/convert", dependencies=[Depends(require_api_key)])
async def convert_book(calibre_id: int, body: ConvertRequest) -> dict:
    book_dir, formats = _book_dir_and_formats(calibre_id)
    input_path = _pick_input_file(book_dir, formats, body.target_format)
    if not os.path.isfile(input_path):
        raise HTTPException(status_code=404, detail=f"Expected format file not found: {input_path}")

    target_ext = body.target_format.lower().lstrip(".")
    with tempfile.TemporaryDirectory() as tmp:
        output_path = os.path.join(tmp, f"converted.{target_ext}")
        try:
            convert_result = subprocess.run(
                [EBOOK_CONVERT_BIN, input_path, output_path],
                capture_output=True,
                text=True,
                timeout=CONVERT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=f"ebook-convert not found on this host: {exc}")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="ebook-convert timed out")
        if convert_result.returncode != 0:
            detail = (convert_result.stderr or convert_result.stdout or "unknown error")[-2000:]
            logger.warning("ebook-convert failed: %s", detail)
            raise HTTPException(status_code=502, detail=f"ebook-convert failed: {detail}")

        add_result = _run_calibredb(["add_format", str(calibre_id), output_path])
    _raise_for_failure(add_result, "add_format")
    return {"success": True, "target_format": target_ext}
