import threading
from pathlib import Path
from types import SimpleNamespace

import managers.home_manager as home_manager_module
from managers.home_manager import HomeManager
from managers.state_manager import StateManager
from transfer.torrent_transfer import TorrentTransfer
from utils.config import Config, Downloader, SeedBox, Transfer


class FakeHomeClient:
    def __init__(self):
        self.add_calls = []
        self.delete_calls = []
        self.recheck_calls = []
        self.start_calls = []

    def torrents_info(self, torrent_hashes=None):
        if torrent_hashes is None:
            return [SimpleNamespace(hash="bt-hash", progress=1)]
        if torrent_hashes == "bt-hash":
            return [SimpleNamespace(hash="bt-hash", progress=1)]
        return []

    def torrents_add(self, **kwargs):
        self.add_calls.append(kwargs)
        return "Fails."

    def torrents_delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return None

    def torrents_set_category(self, **kwargs):
        return None

    def torrents_add_peers(self, **kwargs):
        return None

    def torrents_recheck(self, **kwargs):
        self.recheck_calls.append(kwargs)
        return None

    def torrents_start(self, **kwargs):
        self.start_calls.append(kwargs)
        return None


class LaggingHomeClient(FakeHomeClient):
    def __init__(self, snapshots):
        super().__init__()
        self.snapshots = list(snapshots)
        self.read_count = 0

    def torrents_info(self, torrent_hashes=None):
        if torrent_hashes is None:
            idx = min(self.read_count, len(self.snapshots) - 1)
            self.read_count += 1
            hashes = self.snapshots[idx]
            return [SimpleNamespace(hash=h, progress=1) for h in hashes]
        return []


class IncompleteHomeClient(FakeHomeClient):
    def torrents_info(self, torrent_hashes=None):
        if torrent_hashes is None:
            return [SimpleNamespace(hash="bt-hash", progress=0.2)]
        if torrent_hashes == "bt-hash":
            return [SimpleNamespace(hash="bt-hash", progress=0.2)]
        return []


def make_config(tmp_path):
    return Config(
        transfer=Transfer(
            original_torrent_path=str(tmp_path / "downloads"),
            bt_path=str(tmp_path / "bt"),
            torrent_info_path=str(tmp_path / "state.json"),
            bt_trackers=[],
            seedbox_origin_data_missing_policy="pause_transfer",
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


def test_home_origin_add_failures_persist_and_eventually_skip(tmp_path, monkeypatch):
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

    shared_client = FakeHomeClient()
    monkeypatch.setattr(
        home_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=shared_client),
    )

    for _ in range(4):
        state_manager = StateManager(config.transfer.torrent_info_path)
        manager = HomeManager(
            config,
            state_manager,
            "seedbox",
            "home",
            "/downloads/home",
        )
        manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.home_add_retry_count == 3
    assert final_state.is_skipped is True
    assert "home" in final_state.skip_reason.lower()
    assert len(shared_client.add_calls) == 3


def test_home_does_not_readd_bt_when_state_already_marked_in_home(tmp_path, monkeypatch):
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
        )
    )

    lagging_client = LaggingHomeClient([set(), set()])
    monkeypatch.setattr(
        home_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=lagging_client),
    )

    manager = HomeManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        "/downloads/home",
    )
    manager.run()

    assert len(lagging_client.add_calls) == 0


def test_home_resets_stale_bt_state_when_home_task_disappeared(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "origin.torrent").write_text("origin", encoding="utf-8")
    bt_path = Path(tmp_path / "bt.torrent")
    bt_path.write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(bt_path),
            is_bt_in_seed_box=True,
            is_bt_in_home_dl=True,
        )
    )

    client = LaggingHomeClient([set()])
    monkeypatch.setattr(
        home_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = HomeManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        "/downloads/home",
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_home_dl is False
    assert len(client.add_calls) == 0


def test_home_adds_bt_as_started(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "origin.torrent").write_text("origin", encoding="utf-8")
    bt_path = Path(tmp_path / "bt.torrent")
    bt_path.write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(bt_path),
            is_bt_in_seed_box=True,
        )
    )

    client = LaggingHomeClient([set()])
    client.torrents_add = lambda **kwargs: client.add_calls.append(kwargs) or "Ok."
    monkeypatch.setattr(
        home_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = HomeManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        "/downloads/home",
    )
    manager.run()

    assert client.add_calls == [
        {
            "torrent_files": str(bt_path),
            "save_path": "/downloads/home",
            "category": config.transfer.home_bt_category,
            "is_paused": False,
        }
    ]


def test_home_starts_paused_existing_bt(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "origin.torrent").write_text("origin", encoding="utf-8")
    bt_path = Path(tmp_path / "bt.torrent")
    bt_path.write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(bt_path),
            is_bt_in_seed_box=True,
            is_bt_in_home_dl=True,
        )
    )

    client = FakeHomeClient()

    def torrents_info(torrent_hashes=None):
        if torrent_hashes is None:
            return [SimpleNamespace(hash="bt-hash", progress=0, state="pausedDL")]
        if torrent_hashes == "bt-hash":
            return [SimpleNamespace(hash="bt-hash", progress=0, state="pausedDL")]
        return []

    client.torrents_info = torrents_info
    monkeypatch.setattr(
        home_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = HomeManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        "/downloads/home",
    )
    manager.run()

    assert client.start_calls == [{"torrent_hashes": "bt-hash"}]


def test_home_blocks_bt_when_seedbox_origin_is_missing_files(tmp_path, monkeypatch):
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
            is_bt_in_seed_box=False,
            is_bt_in_home_dl=True,
            is_torrent_in_home_dl=False,
            seedbox_origin_data_status="missing_files",
        )
    )

    client = IncompleteHomeClient()
    monkeypatch.setattr(
        home_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = HomeManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        "/downloads/home",
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_home_dl is True
    assert client.add_calls == []
    assert client.delete_calls == []


def test_home_can_remove_broken_bt_when_policy_is_force_recheck_and_rebuild(tmp_path, monkeypatch):
    config = Config(
        transfer=Transfer(
            original_torrent_path=str(tmp_path / "downloads"),
            bt_path=str(tmp_path / "bt"),
            torrent_info_path=str(tmp_path / "state.json"),
            bt_trackers=[],
            seedbox_origin_data_missing_policy="force_recheck_and_rebuild_bt",
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
            is_bt_in_seed_box=False,
            is_bt_in_home_dl=True,
            is_torrent_in_home_dl=False,
            seedbox_origin_data_status="missing_files",
        )
    )

    client = IncompleteHomeClient()
    monkeypatch.setattr(
        home_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = HomeManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        "/downloads/home",
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_home_dl is False
    assert client.delete_calls == [{"torrent_hashes": "bt-hash", "delete_files": False}]


def test_home_force_rebuild_does_not_repeat_delete_after_state_is_reset(tmp_path, monkeypatch):
    config = Config(
        transfer=Transfer(
            original_torrent_path=str(tmp_path / "downloads"),
            bt_path=str(tmp_path / "bt"),
            torrent_info_path=str(tmp_path / "state.json"),
            bt_trackers=[],
            seedbox_origin_data_missing_policy="force_recheck_and_rebuild_bt",
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
            is_bt_in_seed_box=False,
            is_bt_in_home_dl=False,
            is_torrent_in_home_dl=False,
            seedbox_origin_data_status="recheck_requested",
        )
    )

    client = IncompleteHomeClient()
    monkeypatch.setattr(
        home_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = HomeManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        "/downloads/home",
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_home_dl is False
    assert client.delete_calls == []
