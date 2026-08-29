"""
Transmission client implementation.

Provides downloading capabilities for torrent files via the Transmission RPC API.

Mirrors the public interface of :class:`QBittorrentClient` so the torrent
download handler and background sync tasks can use either client interchangeably.
"""
import os
import time
from datetime import timedelta
from typing import Optional, Dict, Any, List
import structlog

from .qbittorrent import extract_info_hash_from_torrent
from .. import DownloadState

try:
    from transmission_rpc import Client as TransmissionRPCClient
    HAS_TRANSMISSION = True
except ImportError:  # pragma: no cover - exercised only when dependency missing
    HAS_TRANSMISSION = False
    TransmissionRPCClient = None

try:
    from transmission_rpc.error import (
        TransmissionError,
        TransmissionConnectError,
        TransmissionAuthError,
    )
except ImportError:  # pragma: no cover - older/newer layouts or missing dependency
    TransmissionError = Exception
    TransmissionConnectError = Exception
    TransmissionAuthError = Exception

import requests

logger = structlog.get_logger()

# Default RPC path used by Transmission's web/RPC endpoint
DEFAULT_RPC_PATH = "/transmission/rpc"


class TransmissionClient:
    """
    Transmission RPC API client for torrent downloads.

    Features:
    - Add torrents via URL, magnet, or file
    - Monitor download progress
    - Get completed file paths
    - Label ("category") management
    - Path mapping for Docker environments
    """

    def __init__(
        self,
        host: str,
        port: int = 9091,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_ssl: bool = False,
        url_base: Optional[str] = None,
        category: Optional[str] = None,
        ebook_category: Optional[str] = None,
        audiobook_category: Optional[str] = None,
        path_mappings: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize Transmission client.

        Args:
            host: Transmission host (e.g., "localhost", "transmission")
            port: Transmission RPC port (default: 9091)
            username: RPC username
            password: RPC password
            use_ssl: Use HTTPS connection
            url_base: RPC path override for reverse proxy setups
                (defaults to "/transmission/rpc")
            category: Default/fallback label for downloads
            ebook_category: Label for ebook downloads (falls back to category)
            audiobook_category: Label for audiobook downloads (falls back to category)
            path_mappings: Docker path mappings {"container_path": "host_path"}
        """
        if not HAS_TRANSMISSION:
            raise ImportError(
                "transmission-rpc package is required. "
                "Install with: pip install transmission-rpc"
            )

        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.url_base = url_base
        self.category = category
        self.ebook_category = ebook_category
        self.audiobook_category = audiobook_category
        self.path_mappings = path_mappings or {}

        self.client: Optional[TransmissionRPCClient] = None
        self._connect()

    def get_category_for_format(self, format_type: Optional[str] = None) -> Optional[str]:
        """
        Get the appropriate label based on download format.

        Args:
            format_type: "ebook", "audiobook", or None

        Returns:
            The format-specific label, or the default category as fallback
        """
        if format_type == "ebook" and self.ebook_category:
            return self.ebook_category
        elif format_type == "audiobook" and self.audiobook_category:
            return self.audiobook_category
        return self.category

    def _rpc_path(self) -> str:
        """Resolve the RPC path, honoring an optional url_base override."""
        if not self.url_base:
            return DEFAULT_RPC_PATH
        base = "/" + self.url_base.strip("/")
        # Allow the user to pass either the proxy prefix or the full RPC path
        if base.endswith("/rpc"):
            return base
        return f"{base}/rpc"

    def _connect(self):
        """Establish connection to Transmission"""
        try:
            protocol = "https" if self.use_ssl else "http"

            self.client = TransmissionRPCClient(
                protocol=protocol,
                host=self.host,
                port=self.port,
                path=self._rpc_path(),
                username=self.username or None,
                password=self.password or None,
            )

            # Test connection / trigger auth
            session = self.client.get_session()

            logger.info(
                "transmission_connected",
                host=self.host,
                port=self.port,
                version=getattr(session, "version", None),
            )

        except TransmissionAuthError as e:
            logger.error("transmission_login_failed", host=self.host, error=str(e))
            raise
        except TransmissionConnectError as e:
            logger.error(
                "transmission_connection_failed",
                host=self.host,
                port=self.port,
                error=str(e),
            )
            raise

    def test_connection(self) -> bool:
        """
        Test connection to Transmission.

        Returns:
            True if connection successful
        """
        try:
            if not self.client:
                self._connect()

            session = self.client.get_session()
            version = getattr(session, "version", None)
            logger.info("transmission_test_success", version=version)
            return True

        except Exception as e:
            logger.warning("transmission_test_failed", error=str(e))
            return False

    def _download_torrent_file(self, url: str, timeout: int = 30) -> Optional[bytes]:
        """
        Download a .torrent file from a URL.

        Args:
            url: URL to the torrent file (e.g., Prowlarr download URL)
            timeout: Request timeout in seconds

        Returns:
            Torrent file bytes, or None if download fails
        """
        try:
            logger.info("transmission_downloading_torrent_file", url=url[:100])

            response = requests.get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": "Bookkeep/1.0",
                    "Accept": "application/x-bittorrent, */*",
                },
                allow_redirects=True,
            )
            response.raise_for_status()

            content = response.content
            if not content or content[0:1] != b"d":
                logger.warning(
                    "transmission_torrent_file_invalid_content",
                    first_bytes=content[:20] if content else None,
                )
                return None

            logger.info("transmission_torrent_file_downloaded", size=len(content))
            return content

        except requests.RequestException as e:
            logger.error(
                "transmission_torrent_file_download_failed", url=url[:100], error=str(e)
            )
            return None

    def _extract_hash_from_magnet(self, magnet: str) -> Optional[str]:
        """Extract a 40-char hex info_hash from a magnet link."""
        if not magnet or "btih:" not in magnet.lower():
            return None

        try:
            hash_start = magnet.lower().find("btih:") + 5
            hash_end = magnet.find("&", hash_start)
            info_hash = magnet[hash_start:] if hash_end == -1 else magnet[hash_start:hash_end]

            if len(info_hash) == 32:
                import base64

                try:
                    info_hash = base64.b32decode(info_hash.upper()).hex()
                except Exception:
                    pass

            if len(info_hash) == 40:
                return info_hash.lower()
        except Exception as e:
            logger.warning("transmission_magnet_hash_extraction_failed", error=str(e))

        return None

    def add_torrent(
        self,
        url: Optional[str] = None,
        magnet: Optional[str] = None,
        torrent_file: Optional[bytes] = None,
        save_path: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Add a torrent to Transmission.

        Args:
            url: Torrent file URL
            magnet: Magnet link
            torrent_file: Torrent file bytes
            save_path: Download save path
            category: Torrent label
            tags: List of labels to apply

        Returns:
            Torrent hash (info_hash) or None if failed
        """
        if not any([url, magnet, torrent_file]):
            raise ValueError("Must provide url, magnet, or torrent_file")

        if category is None:
            category = self.category

        # Build the label list (Transmission's equivalent of qBittorrent categories/tags)
        labels: List[str] = []
        if category:
            labels.append(category)
        if tags:
            labels.extend(t for t in tags if t and t not in labels)

        # --- Extract the hash up-front for reliable tracking ---
        info_hash: Optional[str] = None
        torrent_data = torrent_file

        if magnet:
            info_hash = self._extract_hash_from_magnet(magnet)
        elif url:
            torrent_data = self._download_torrent_file(url)
            if torrent_data:
                info_hash = extract_info_hash_from_torrent(torrent_data)
        elif torrent_file:
            info_hash = extract_info_hash_from_torrent(torrent_file)

        # --- Add to Transmission ---
        add_kwargs: Dict[str, Any] = {}
        if save_path:
            add_kwargs["download_dir"] = self._map_path_to_container(save_path)
        if labels:
            add_kwargs["labels"] = labels

        try:
            if magnet:
                logger.info("transmission_add_magnet", labels=labels, hash=info_hash)
                torrent = self.client.add_torrent(magnet, **add_kwargs)
            elif torrent_data:
                logger.info("transmission_add_file", labels=labels, hash=info_hash)
                torrent = self.client.add_torrent(torrent_data, **add_kwargs)
            elif url:
                logger.info("transmission_add_url", url=url[:100], labels=labels)
                torrent = self.client.add_torrent(url, **add_kwargs)
            else:
                logger.error("transmission_no_torrent_source")
                return None

            # Transmission returns a Torrent object (also for duplicates)
            resolved_hash = (getattr(torrent, "hashString", None) or info_hash)
            if resolved_hash:
                resolved_hash = resolved_hash.lower()
                if self._wait_for_torrent(resolved_hash):
                    logger.info("transmission_torrent_added", info_hash=resolved_hash)
                else:
                    logger.warning(
                        "transmission_torrent_not_verified", info_hash=resolved_hash
                    )
                return resolved_hash

            logger.warning("transmission_torrent_hash_not_found")
            return None

        except TransmissionError as e:
            logger.error("transmission_add_failed", error=str(e))
            return None
        except Exception as e:
            logger.error("transmission_add_error", error=str(e))
            return None

    def _wait_for_torrent(
        self, info_hash: str, max_attempts: int = 10, delay: float = 0.1
    ) -> bool:
        """Poll for a torrent with the known hash to appear after adding it."""
        for attempt in range(max_attempts):
            try:
                self.client.get_torrent(info_hash)
                return True
            except Exception:
                pass

            if attempt < max_attempts - 1:
                time.sleep(delay)

        logger.warning(
            "transmission_torrent_verification_timeout",
            hash=info_hash,
            max_attempts=max_attempts,
        )
        return False

    def _get_torrent(self, info_hash: str):
        """Fetch a single torrent by hash, returning None if not found."""
        try:
            return self.client.get_torrent(info_hash)
        except Exception:
            return None

    def get_download_status(self, info_hash: str) -> Dict[str, Any]:
        """
        Get download status for a torrent.

        Returns:
            Dict with normalized status information (see QBittorrentClient).
        """
        try:
            torrent = self._get_torrent(info_hash)
            if torrent is None:
                logger.warning("transmission_torrent_not_found", info_hash=info_hash)
                return {
                    "state": DownloadState.ERROR,
                    "progress": 0.0,
                    "message": "Torrent not found",
                }

            progress = float(getattr(torrent, "progress", 0.0) or 0.0)
            state = self._map_state(torrent)

            files = []
            try:
                for f in torrent.get_files():
                    size = getattr(f, "size", 0) or 0
                    completed = getattr(f, "completed", 0) or 0
                    files.append(
                        {
                            "name": getattr(f, "name", ""),
                            "size": size,
                            "progress": (completed / size) if size else 0.0,
                        }
                    )
            except Exception:
                pass

            download_dir = getattr(torrent, "download_dir", "") or ""

            return {
                "state": state,
                "progress": progress,
                "download_speed": getattr(torrent, "rate_download", 0),
                "upload_speed": getattr(torrent, "rate_upload", 0),
                "eta": self._normalize_eta(getattr(torrent, "eta", None)),
                "name": getattr(torrent, "name", ""),
                "save_path": self._map_path_from_container(download_dir),
                "total_size": getattr(torrent, "total_size", 0),
                "downloaded": getattr(torrent, "downloaded_ever", 0),
                "seeders": getattr(torrent, "peers_sending_to_us", 0),
                "peers": getattr(torrent, "peers_getting_from_us", 0),
                "ratio": getattr(torrent, "ratio", 0.0),
                "files": files,
                "client_state": str(getattr(torrent, "status", "")),
            }

        except Exception as e:
            logger.error(
                "transmission_get_status_failed", info_hash=info_hash, error=str(e)
            )
            return {
                "state": DownloadState.ERROR,
                "progress": 0.0,
                "message": str(e),
            }

    @staticmethod
    def _normalize_eta(eta: Any) -> Optional[int]:
        """Transmission returns eta as a timedelta (or None); normalize to seconds."""
        if eta is None:
            return None
        if isinstance(eta, timedelta):
            return int(eta.total_seconds())
        try:
            return int(eta)
        except (TypeError, ValueError):
            return None

    def _map_state(self, torrent) -> DownloadState:
        """Map a Transmission torrent's status to DownloadState"""
        # A non-zero error code trumps the reported status
        if getattr(torrent, "error", 0):
            return DownloadState.ERROR

        status = str(getattr(torrent, "status", "")).lower()
        progress = float(getattr(torrent, "progress", 0.0) or 0.0)

        state_map = {
            "downloading": DownloadState.DOWNLOADING,
            "download pending": DownloadState.QUEUED,
            "seeding": DownloadState.SEEDING,
            "seed pending": DownloadState.SEEDING,
            "checking": DownloadState.CHECKING,
            "check pending": DownloadState.CHECKING,
        }

        if status in state_map:
            return state_map[status]

        if status == "stopped":
            # Stopped after finishing == complete, otherwise it's paused
            return DownloadState.SEEDING if progress >= 100 else DownloadState.PAUSED

        return DownloadState.QUEUED

    def get_completed_download_path(self, info_hash: str) -> Optional[str]:
        """
        Get the path to a completed download.

        Returns:
            Path to downloaded file/folder or None
        """
        try:
            torrent = self._get_torrent(info_hash)
            if torrent is None:
                return None

            if float(getattr(torrent, "progress", 0.0) or 0.0) < 100:
                logger.warning(
                    "transmission_download_incomplete",
                    info_hash=info_hash,
                    progress=getattr(torrent, "progress", 0.0),
                )
                return None

            download_dir = getattr(torrent, "download_dir", "") or ""
            name = getattr(torrent, "name", "") or ""
            content_path = os.path.join(download_dir, name) if name else download_dir

            return self._map_path_from_container(content_path)

        except Exception as e:
            logger.error(
                "transmission_get_path_failed", info_hash=info_hash, error=str(e)
            )
            return None

    def remove_torrent(self, info_hash: str, delete_files: bool = False) -> bool:
        """Remove a torrent from Transmission."""
        try:
            self.client.remove_torrent(info_hash, delete_data=delete_files)
            logger.info(
                "transmission_torrent_removed",
                info_hash=info_hash,
                delete_files=delete_files,
            )
            return True
        except Exception as e:
            logger.error(
                "transmission_remove_failed", info_hash=info_hash, error=str(e)
            )
            return False

    def pause_torrent(self, info_hash: str) -> bool:
        """Pause (stop) a torrent"""
        try:
            self.client.stop_torrent(info_hash)
            return True
        except Exception as e:
            logger.error("transmission_pause_failed", info_hash=info_hash, error=str(e))
            return False

    def resume_torrent(self, info_hash: str) -> bool:
        """Resume (start) a stopped torrent"""
        try:
            self.client.start_torrent(info_hash)
            return True
        except Exception as e:
            logger.error("transmission_resume_failed", info_hash=info_hash, error=str(e))
            return False

    def find_existing_download(
        self,
        info_hash: Optional[str] = None,
        name: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Optional[str]:
        """Find an existing download by hash or name."""
        try:
            if info_hash:
                torrent = self._get_torrent(info_hash)
                if torrent is not None:
                    return getattr(torrent, "hashString", info_hash).lower()

            if name:
                for torrent in self.client.get_torrents():
                    if getattr(torrent, "name", None) == name:
                        if category:
                            labels = getattr(torrent, "labels", []) or []
                            if category not in labels:
                                continue
                        return getattr(torrent, "hashString", "").lower() or None

            return None

        except Exception as e:
            logger.error("transmission_find_failed", error=str(e))
            return None

    def _map_path_from_container(self, container_path: str) -> str:
        """Map a path from inside a Docker container to the host."""
        if not self.path_mappings or not container_path:
            return container_path

        for container_prefix, host_prefix in self.path_mappings.items():
            if container_path.startswith(container_prefix):
                mapped = container_path.replace(container_prefix, host_prefix, 1)
                logger.debug(
                    "transmission_path_mapped", container=container_path, host=mapped
                )
                return mapped

        return container_path

    def _map_path_to_container(self, host_path: str) -> str:
        """Map a path from the host into a Docker container."""
        if not self.path_mappings or not host_path:
            return host_path

        for container_prefix, host_prefix in self.path_mappings.items():
            if host_path.startswith(host_prefix):
                mapped = host_path.replace(host_prefix, container_prefix, 1)
                logger.debug(
                    "transmission_path_mapped", host=host_path, container=mapped
                )
                return mapped

        return host_path

    def get_client_info(self) -> Dict[str, Any]:
        """Get Transmission client information (version, RPC version, etc.)."""
        try:
            session = self.client.get_session()
            return {
                "version": getattr(session, "version", None),
                "api_version": getattr(session, "rpc_version", None),
            }
        except Exception as e:
            logger.error("transmission_get_info_failed", error=str(e))
            return {}
