import threading
from pathlib import Path
from types import SimpleNamespace

import managers.seedbox_manager as seedbox_manager_module
from managers.seedbox_manager import SeedBoxManager
from managers.state_manager import StateManager
from transfer.torrent_transfer import TorrentTransfer
from utils.config import Config, Downloader, SeedBox, Transfer


class FakeSeedboxClient:
    def __init__(self, torrents, add_response="Fails."):
        self._torrents = list(torrents)
        self.add_response = add_response
        self.add_calls = []

    def torrents_info(self, status=None, category=None, torrent_hashes=None):
        result = list(self._torrents)
        if status == "completed":
            result = [torrent for torrent in result if getattr(torrent, "progress", 0) == 1]
        if torrent_hashes is not None:
            result = [torrent for torrent in result if torrent.hash == torrent_hashes]
        if category is not None:
            result = [torrent for torrent in result if torrent.category == category]
        return result

    def torrents_add(self, **kwargs):
        self.add_calls.append(kwargs)
        return self.add_response

    def torrents_delete(self, **kwargs):
        return None

    def torrents_set_category(self, **kwargs):
        return None


class MissingTorrentSFTPClient:
    def __init__(self, **_kwargs):
        pass

    def connect(self):
        return None

    def download(self, *_args, **_kwargs):
        raise FileNotFoundError("remote torrent missing")

    def close(self):
        return None


def make_config(tmp_path, auto_dl_torrent_from_seedbox=False):
    return Config(
        transfer=Transfer(
            original_torrent_path=str(tmp_path / "downloads"),
            bt_path=str(tmp_path / "bt"),
            torrent_info_path=str(tmp_path / "state.json"),
            bt_trackers=[],
            auto_dl_torrent_from_seedbox=auto_dl_torrent_from_seedbox,
        ),
        seed_box=[
            SeedBox(
                name="seedbox",
                ssh_host="seed.example",
                incoming_port=60000,
                ssh_user="user",
                ssh_password="pass",
                torrents_path="/remote/torrents",
            )
        ],
        downloaders=[
            Downloader(
                name="seedbox",
                url="http://seedbox:8080",
                username="user",
                password="pass",
                want_torrent_category="To",
            ),
            Downloader(
                name="home",
                url="http://home:8080",
                username="user",
                password="pass",
            ),
        ],
    )


def make_completed_torrent():
    return SimpleNamespace(
        hash="origin-hash",
        category="To",
        completion_on=0,
        progress=1,
        name="Origin Torrent",
        save_path="/downloads/origin",
        trackers=[],
    )


def make_torrent(hash_value, category, progress):
    return SimpleNamespace(
        hash=hash_value,
        category=category,
        completion_on=0,
        progress=progress,
        name=f"Torrent-{hash_value}",
        save_path="/downloads/origin",
        trackers=[],
    )


def test_seedbox_add_failures_persist_across_runs_and_eventually_skip(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "origin.torrent").write_text("origin", encoding="utf-8")
    Path(tmp_path / "bt.torrent").write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
        )
    )

    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=FakeSeedboxClient([make_completed_torrent()])),
    )

    for _ in range(3):
        state_manager = StateManager(config.transfer.torrent_info_path)
        manager = SeedBoxManager(
            config,
            state_manager,
            "seedbox",
            "home",
            threading.Event(),
            async_downloads=False,
        )
        manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.seedbox_add_retry_count == 3
    assert final_state.is_skipped is True
    assert "seedbox" in final_state.skip_reason.lower()


def test_seedbox_skipped_state_stops_adding_bt(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "origin.torrent").write_text("origin", encoding="utf-8")
    Path(tmp_path / "bt.torrent").write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
        )
    )

    shared_client = FakeSeedboxClient([make_completed_torrent()], add_response="Fails.")
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=shared_client),
    )

    for _ in range(4):
        state_manager = StateManager(config.transfer.torrent_info_path)
        manager = SeedBoxManager(
            config,
            state_manager,
            "seedbox",
            "home",
            threading.Event(),
            async_downloads=False,
        )
        manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.seedbox_add_retry_count == 3
    assert final_state.is_skipped is True
    assert len(shared_client.add_calls) == 3


def test_seedbox_sync_uses_all_torrents_for_presence_not_only_completed(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "origin.torrent").write_text("origin", encoding="utf-8")
    Path(tmp_path / "bt.torrent").write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
            is_bt_in_seed_box=True,
            missing_origin_retry_count=2,
        )
    )

    seedbox_torrents = [
        make_torrent("origin-hash", "To", 0.4),
        make_torrent("bt-hash", "BT", 0.2),
    ]
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=FakeSeedboxClient(seedbox_torrents, add_response="Ok.")),
    )

    state_manager = StateManager(config.transfer.torrent_info_path)
    manager = SeedBoxManager(
        config,
        state_manager,
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_seed_box is True
    assert final_state.missing_origin_retry_count == 0
    assert final_state.is_skipped is False


def test_missing_origin_keeps_counting_when_bt_exists_but_local_origin_is_missing(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "bt.torrent").write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "missing-origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
            is_bt_in_seed_box=True,
            is_bt_in_home_dl=True,
            missing_origin_retry_count=2,
        )
    )

    seedbox_torrents = [make_torrent("bt-hash", "BT", 0.2)]
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=FakeSeedboxClient(seedbox_torrents, add_response="Ok.")),
    )

    state_manager = StateManager(config.transfer.torrent_info_path)
    manager = SeedBoxManager(
        config,
        state_manager,
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.missing_origin_retry_count == 3
    assert final_state.is_skipped is True
    assert "origin" in final_state.skip_reason.lower()


def test_missing_remote_origin_is_not_failure_when_local_origin_file_exists(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "origin.torrent").write_text("origin", encoding="utf-8")
    Path(tmp_path / "bt.torrent").write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
            is_bt_in_seed_box=True,
            is_bt_in_home_dl=True,
            missing_origin_retry_count=2,
        )
    )

    seedbox_torrents = [make_torrent("bt-hash", "BT", 0.2)]
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=FakeSeedboxClient(seedbox_torrents, add_response="Ok.")),
    )

    state_manager = StateManager(config.transfer.torrent_info_path)
    manager = SeedBoxManager(
        config,
        state_manager,
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.missing_origin_retry_count == 0
    assert final_state.is_skipped is False


def test_missing_remote_torrent_file_eventually_skips_placeholder_state(tmp_path, monkeypatch):
    config = make_config(tmp_path, auto_dl_torrent_from_seedbox=True)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=FakeSeedboxClient([make_completed_torrent()], add_response="Ok.")),
    )
    monkeypatch.setattr(seedbox_manager_module, "SFTPClient", MissingTorrentSFTPClient)

    for _ in range(3):
        state_manager = StateManager(config.transfer.torrent_info_path)
        manager = SeedBoxManager(
            config,
            state_manager,
            "seedbox",
            "home",
            threading.Event(),
            async_downloads=False,
        )
        manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state is not None
    assert final_state.download_retry_count == 3
    assert final_state.is_skipped is True
    assert "seedbox" in final_state.skip_reason.lower()
