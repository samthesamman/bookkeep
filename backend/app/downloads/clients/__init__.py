"""
Download client implementations for Book Hound.

Provides interfaces for torrent and usenet download clients.
"""
from .qbittorrent import QBittorrentClient
from .transmission import TransmissionClient
from .nzbget import NZBGetClient
from .sabnzbd import SabnzbdClient

__all__ = ["QBittorrentClient", "TransmissionClient", "NZBGetClient", "SabnzbdClient"]
