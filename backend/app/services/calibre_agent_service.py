"""Client for the optional calibre-cli companion service (see calibre-cli/).

bookkeep's own Calibre access is read-only (see calibre_service.py), so pushing
metadata/cover updates back into Calibre goes through this small HTTP client
instead: it talks to a separate service that runs where the Calibre library
actually lives, wrapping calibredb/ebook-convert. Every method here swallows
its own errors and only logs a warning — a push failure must never break the
caller's own metadata update.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
import structlog

from app import models

logger = structlog.get_logger(__name__)


class CalibreAgentClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Optional[models.CalibreSettings]) -> Optional["CalibreAgentClient"]:
        if settings is None or not settings.agent_enabled:
            return None
        if not settings.agent_url or not settings.agent_api_key:
            return None
        return cls(base_url=settings.agent_url, api_key=settings.agent_api_key)

    @property
    def headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key}

    async def health_check(self) -> tuple[bool, Optional[str]]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/health", headers=self.headers)
            if response.status_code == 200:
                return True, None
            return False, f"Agent returned HTTP {response.status_code}"
        except Exception as exc:
            return False, str(exc)

    async def push_metadata(self, calibre_id: int, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/books/{calibre_id}/metadata",
                    headers=self.headers,
                    json={"fields": fields},
                )
            if response.status_code != 200:
                logger.warning(
                    "calibre_agent_metadata_push_failed",
                    calibre_id=calibre_id,
                    status_code=response.status_code,
                    response=response.text[:300],
                )
        except Exception as exc:
            logger.warning("calibre_agent_metadata_push_error", calibre_id=calibre_id, error=str(exc))

    async def push_cover(self, calibre_id: int, cover_url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                cover_response = await client.get(cover_url)
                if cover_response.status_code != 200 or not cover_response.content:
                    logger.warning(
                        "calibre_agent_cover_fetch_failed",
                        calibre_id=calibre_id,
                        cover_url=cover_url,
                        status_code=cover_response.status_code,
                    )
                    return
                response = await client.post(
                    f"{self.base_url}/books/{calibre_id}/cover",
                    headers=self.headers,
                    content=cover_response.content,
                )
            if response.status_code != 200:
                logger.warning(
                    "calibre_agent_cover_push_failed",
                    calibre_id=calibre_id,
                    status_code=response.status_code,
                    response=response.text[:300],
                )
        except Exception as exc:
            logger.warning("calibre_agent_cover_push_error", calibre_id=calibre_id, error=str(exc))

    async def trigger_convert(self, calibre_id: int, target_format: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/books/{calibre_id}/convert",
                    headers=self.headers,
                    json={"target_format": target_format},
                )
            if response.status_code != 200:
                logger.warning(
                    "calibre_agent_convert_failed",
                    calibre_id=calibre_id,
                    target_format=target_format,
                    status_code=response.status_code,
                    response=response.text[:300],
                )
        except Exception as exc:
            logger.warning("calibre_agent_convert_error", calibre_id=calibre_id, error=str(exc))
