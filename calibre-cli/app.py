"""calibre-cli: a small HTTP shim around calibredb.

Deploy this on (or near) the machine that actually hosts the Calibre library,
with a real running Calibre Content Server (``calibre-server``, auth
enabled). bookkeep itself only ever has read-only network access to the
library, so it talks to this service over HTTP instead of writing to
metadata.db directly.

Every write (metadata, cover, re-embedding metadata into book files) goes
through the Content Server via ``calibredb --with-library=http://...`` so
Calibre's own DB layer handles locking/sort-columns/triggers correctly —
this never opens metadata.db itself.

See README.md for configuration and deployment notes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("calibre-cli")

CALIBRE_SERVER_URL = os.environ.get("CALIBRE_SERVER_URL", "http://localhost:8081").rstrip("/")
CALIBRE_SERVER_USERNAME = os.environ.get("CALIBRE_SERVER_USERNAME", "")
CALIBRE_SERVER_PASSWORD = os.environ.get("CALIBRE_SERVER_PASSWORD", "")
CALIBRE_LIBRARY_ID = os.environ.get("CALIBRE_LIBRARY_ID", "")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
CALIBREDB_TIMEOUT_SECONDS = int(os.environ.get("CALIBREDB_TIMEOUT_SECONDS", "60"))
EMBED_METADATA_TIMEOUT_SECONDS = int(os.environ.get("EMBED_METADATA_TIMEOUT_SECONDS", "300"))
CALIBREDB_BIN = os.environ.get("CALIBREDB_BIN", "calibredb")

if not AGENT_API_KEY:
    sys.exit("AGENT_API_KEY must be set — refusing to start with no shared secret configured.")

OPF_NS = "http://www.idpf.org/2007/opf"
ET.register_namespace("", OPF_NS)
ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
ET.register_namespace("opf", OPF_NS)


def _inject_cover_guide(opf_xml: str) -> bytes:
    """Add/replace a cover reference in an existing OPF, leaving every other
    field exactly as calibre reported it.

    Building a fresh near-empty OPF for just the cover (the previous
    approach) is dangerous: calibre's OPF importer treats title/authors as
    mandatory and defaults them when absent, silently blanking out whatever
    the prior /metadata call had just set. Round-tripping the book's own
    current OPF avoids that entirely.
    """
    root = ET.fromstring(opf_xml)
    guide = root.find(f"{{{OPF_NS}}}guide")
    if guide is None:
        guide = ET.SubElement(root, f"{{{OPF_NS}}}guide")
    for ref in list(guide.findall(f"{{{OPF_NS}}}reference")):
        if ref.get("type") == "cover":
            guide.remove(ref)
    ref = ET.SubElement(guide, f"{{{OPF_NS}}}reference")
    ref.set("type", "cover")
    ref.set("href", "cover.jpg")
    ref.set("title", "Cover")
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8")


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


def _run_calibredb(
    args: List[str], timeout: int = CALIBREDB_TIMEOUT_SECONDS, cwd: Optional[str] = None
) -> subprocess.CompletedProcess:
    cmd = [CALIBREDB_BIN, *args, f"--with-library={_with_library_arg()}"]
    if CALIBRE_SERVER_USERNAME:
        cmd.append(f"--username={CALIBRE_SERVER_USERNAME}")
    if CALIBRE_SERVER_PASSWORD:
        cmd.append(f"--password={CALIBRE_SERVER_PASSWORD}")
    logger.info("running calibredb %s", " ".join(args))
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"calibredb not found on this host: {exc}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="calibredb timed out")


async def _run_calibredb_with_retry(
    args: List[str],
    timeout: int = CALIBREDB_TIMEOUT_SECONDS,
    cwd: Optional[str] = None,
    attempts: int = 3,
    backoff_seconds: float = 2.0,
) -> subprocess.CompletedProcess:
    """Retry a calibredb write a few times before giving up.

    Covers a transient race on writes that change title/author: Calibre
    renames the book's on-disk folder to match, and a write landing right as
    that rename is still settling can fail (seen as "Directory not empty" /
    "No such file or directory"). The same write reliably succeeds seconds
    later, so retry with backoff instead of failing outright.
    """
    result = _run_calibredb(args, timeout=timeout, cwd=cwd)
    attempt = 1
    while result.returncode != 0 and attempt < attempts:
        delay = backoff_seconds * attempt
        logger.warning(
            "calibredb call failed (attempt %d/%d), retrying in %.0fs: %s",
            attempt,
            attempts,
            delay,
            (result.stderr or result.stdout or "")[-300:],
        )
        await asyncio.sleep(delay)
        result = _run_calibredb(args, timeout=timeout, cwd=cwd)
        attempt += 1
    return result


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
# Schemas
# ---------------------------------------------------------------------------
class MetadataRequest(BaseModel):
    fields: Dict[str, Any]


class EmbedMetadataRequest(BaseModel):
    # Historical field name (was "which format to convert to"); now an
    # optional filter for calibredb embed_metadata's --only-formats. Blank
    # means "re-embed into every format this book has".
    target_format: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "calibre_server_configured": bool(CALIBRE_SERVER_URL),
    }


@app.post("/books/{calibre_id}/metadata", dependencies=[Depends(require_api_key)])
async def push_metadata(calibre_id: int, body: MetadataRequest) -> dict:
    args = _field_args(body.fields)
    if not args:
        return {"success": True, "skipped": True}
    result = await _run_calibredb_with_retry(["set_metadata", str(calibre_id), *args])
    _raise_for_failure(result, "set_metadata")
    return {"success": True}


@app.post("/books/{calibre_id}/cover", dependencies=[Depends(require_api_key)])
async def push_cover(calibre_id: int, request: Request) -> dict:
    image_bytes = await request.body()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty request body")

    show_result = _run_calibredb(["show_metadata", str(calibre_id), "--as-opf"])
    _raise_for_failure(show_result, "show_metadata")
    try:
        opf_bytes = _inject_cover_guide(show_result.stdout)
    except ET.ParseError as exc:
        raise HTTPException(status_code=502, detail=f"Could not parse current OPF metadata: {exc}")

    with tempfile.TemporaryDirectory() as tmp:
        cover_path = os.path.join(tmp, "cover.jpg")
        opf_path = os.path.join(tmp, "metadata.opf")
        with open(cover_path, "wb") as f:
            f.write(image_bytes)
        with open(opf_path, "wb") as f:
            f.write(opf_bytes)
        # calibredb resolves the OPF's relative cover.jpg href against its own
        # CWD, not the OPF's directory — without this it looks for cover.jpg
        # wherever the agent process happens to be running from.
        result = await _run_calibredb_with_retry(["set_metadata", str(calibre_id), opf_path], cwd=tmp)
    _raise_for_failure(result, "cover set_metadata")
    return {"success": True}


@app.post("/books/{calibre_id}/convert", dependencies=[Depends(require_api_key)])
async def embed_metadata(calibre_id: int, body: EmbedMetadataRequest) -> dict:
    """Re-embed the book's current calibre metadata into its existing format file(s) in place.

    Endpoint path kept as /convert for compatibility, but this runs
    ``calibredb embed_metadata`` rather than any format conversion — it
    always updates the file(s) on disk, regardless of what formats already
    exist, since the point is baking in metadata that was just pushed via
    /metadata and /cover, not producing a new format.
    """
    args = ["embed_metadata", str(calibre_id)]
    only_format = (body.target_format or "").strip()
    if only_format:
        args += ["--only-formats", only_format.upper().lstrip(".")]
    result = _run_calibredb(args, timeout=EMBED_METADATA_TIMEOUT_SECONDS)
    _raise_for_failure(result, "embed_metadata")
    return {"success": True}
