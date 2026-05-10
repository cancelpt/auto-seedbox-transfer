from types import SimpleNamespace

from utils.qbittorrent_snapshot import QbittorrentSnapshot


class SyncClient:
    def __init__(self):
        self.calls = 0
        self.tracker_calls = []

    def sync_maindata(self, rid=0, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "rid": 1,
                "full_update": True,
                "torrents": {
                    "a": {"name": "A", "category": "cat-a", "progress": 0.25},
                    "b": {"name": "B", "category": "cat-b", "progress": 1.0},
                },
                "torrents_removed": [],
            }

        return {
            "rid": 2,
            "full_update": False,
            "torrents": {
                "a": {"name": "A", "category": "cat-a", "progress": 0.75},
                "c": {"name": "C", "category": "cat-c", "progress": 1.0},
            },
            "torrents_removed": ["b"],
        }

    def torrents_info(self, **kwargs):
        self.tracker_calls.append(kwargs)
        return [SimpleNamespace(hash="a", trackers=[SimpleNamespace(url="udp://tracker.example:80")])]


class FullListClient:
    def __init__(self):
        self.calls = []

    def torrents_info(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("torrent_hashes") == "a" and kwargs.get("include_trackers"):
            return [SimpleNamespace(hash="a", trackers=[SimpleNamespace(url="udp://tracker.example:80")])]
        return [
            SimpleNamespace(
                hash="a",
                name="A",
                category="cat-a",
                progress=1.0,
                trackers=[SimpleNamespace(url="udp://tracker.example:80")] if kwargs.get("include_trackers") else [],
            ),
            SimpleNamespace(hash="b", name="B", category="cat-b", progress=0.5),
        ]


def test_snapshot_uses_sync_updates_and_removed_hashes():
    snapshot = QbittorrentSnapshot(SyncClient())

    snapshot.refresh()
    assert snapshot.hashes() == {"a", "b"}
    assert snapshot.torrent("a").progress == 0.25

    snapshot.refresh()
    assert snapshot.hashes() == {"a", "c"}
    assert snapshot.torrent("a").progress == 0.75
    assert snapshot.torrent("b") is None


def test_snapshot_requests_trackers_during_full_list_refresh():
    client = FullListClient()
    snapshot = QbittorrentSnapshot(client)

    snapshot.refresh()
    trackers = snapshot.get_trackers("a")

    assert len(trackers) == 1
    assert trackers[0].url == "udp://tracker.example:80"
    assert client.calls == [{"include_trackers": True}]


def test_snapshot_fetches_trackers_on_demand_after_sync_refresh():
    client = SyncClient()
    snapshot = QbittorrentSnapshot(client)

    snapshot.refresh()
    trackers = snapshot.get_trackers("a")

    assert len(trackers) == 1
    assert trackers[0].url == "udp://tracker.example:80"
    assert client.tracker_calls == [{"torrent_hashes": "a", "include_trackers": True}]
