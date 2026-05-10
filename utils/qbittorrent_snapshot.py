from __future__ import annotations

import copy
from types import SimpleNamespace


class QbittorrentSnapshot:
    def __init__(self, client):
        self.client = client
        self._rid = 0
        self._supports_sync = hasattr(client, "sync_maindata")
        self._torrents_by_hash: dict[str, SimpleNamespace] = {}

    def refresh(self):
        if self._supports_sync:
            try:
                self._refresh_from_sync()
                return self
            except Exception:
                self._supports_sync = False

        self._refresh_from_full_list()
        return self

    def torrents(self):
        return [copy.deepcopy(torrent) for torrent in self._torrents_by_hash.values()]

    def hashes(self):
        return set(self._torrents_by_hash.keys())

    def torrent(self, torrent_hash: str):
        torrent = self._torrents_by_hash.get(torrent_hash)
        return copy.deepcopy(torrent) if torrent else None

    def by_category(self, category: str):
        return [
            copy.deepcopy(torrent)
            for torrent in self._torrents_by_hash.values()
            if getattr(torrent, "category", None) == category
        ]

    def get_trackers(self, torrent_hash: str):
        torrent = self._torrents_by_hash.get(torrent_hash)
        if torrent and getattr(torrent, "trackers", None):
            return copy.deepcopy(torrent.trackers)

        try:
            torrents = self.client.torrents_info(torrent_hashes=torrent_hash, include_trackers=True)
        except TypeError:
            torrents = self.client.torrents_info(torrent_hashes=torrent_hash)
        if not torrents:
            return []

        trackers = self._normalize_value(getattr(torrents[0], "trackers", []))
        if torrent is None:
            torrent = self._normalize_torrent(torrent_hash, torrents[0])
            self._torrents_by_hash[torrent_hash] = torrent
        else:
            torrent.trackers = trackers
        return copy.deepcopy(trackers)

    def _refresh_from_sync(self):
        response = self.client.sync_maindata(rid=self._rid)
        if not isinstance(response, dict):
            raise TypeError("sync_maindata response must be a dict")

        self._rid = response.get("rid", self._rid)
        if response.get("full_update"):
            self._torrents_by_hash = {}

        for torrent_hash in response.get("torrents_removed", []) or []:
            self._torrents_by_hash.pop(torrent_hash, None)

        torrents = response.get("torrents", {}) or {}
        for torrent_hash, torrent_data in torrents.items():
            self._torrents_by_hash[torrent_hash] = self._normalize_torrent(torrent_hash, torrent_data)

    def _refresh_from_full_list(self):
        try:
            torrents = self.client.torrents_info(include_trackers=True)
        except TypeError:
            torrents = self.client.torrents_info()
        self._torrents_by_hash = {}
        for torrent in torrents or []:
            torrent_hash = getattr(torrent, "hash", None)
            if not torrent_hash:
                continue
            self._torrents_by_hash[torrent_hash] = self._normalize_torrent(torrent_hash, torrent)

    def _normalize_torrent(self, torrent_hash: str, payload):
        torrent = self._normalize_value(payload)
        if not isinstance(torrent, SimpleNamespace):
            torrent = SimpleNamespace(**getattr(torrent, "__dict__", {}))
        if not getattr(torrent, "hash", None):
            torrent.hash = torrent_hash
        return torrent

    def _normalize_value(self, value):
        if isinstance(value, SimpleNamespace):
            return copy.deepcopy(value)
        if isinstance(value, dict):
            return SimpleNamespace(**{key: self._normalize_nested(val) for key, val in value.items()})
        if isinstance(value, list):
            return [self._normalize_nested(item) for item in value]
        return copy.deepcopy(value)

    def _normalize_nested(self, value):
        if isinstance(value, dict):
            return self._normalize_value(value)
        if isinstance(value, list):
            return [self._normalize_nested(item) for item in value]
        if isinstance(value, SimpleNamespace):
            return copy.deepcopy(value)
        return copy.deepcopy(value)
