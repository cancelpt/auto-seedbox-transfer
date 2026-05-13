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
        self.delete_calls = []
        self.recheck_calls = []

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
        self.delete_calls.append(kwargs)
        return None

    def torrents_set_category(self, **kwargs):
        return None

    def torrents_recheck(self, **kwargs):
        self.recheck_calls.append(kwargs)
        return None


class AddCreatesMissingFilesSeedboxClient(FakeSeedboxClient):
    def torrents_add(self, **kwargs):
        self.add_calls.append(kwargs)
        self._torrents.append(make_torrent_with_state("bt-hash", "BT", 0, "missingFiles"))
        return self.add_response


class MissingTorrentSFTPClient:
    def __init__(self, **_kwargs):
        pass

    def connect(self):
        return None

    def download(self, *_args, **_kwargs):
        raise FileNotFoundError("remote torrent missing")

    def close(self):
        return None


class DownloadingSFTPClient:
    download_calls = []
    payload = b"downloaded"

    def __init__(self, **_kwargs):
        pass

    def connect(self):
        return None

    def download(self, remote_file, local_file):
        type(self).download_calls.append((remote_file, local_file))
        Path(local_file).write_bytes(type(self).payload)

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


def make_config_with_missing_policy(tmp_path, policy):
    config = make_config(tmp_path)
    return Config(
        transfer=Transfer(
            original_torrent_path=config.transfer.original_torrent_path,
            bt_path=config.transfer.bt_path,
            torrent_info_path=config.transfer.torrent_info_path,
            bt_trackers=config.transfer.bt_trackers,
            auto_dl_torrent_from_seedbox=config.transfer.auto_dl_torrent_from_seedbox,
            seedbox_origin_data_missing_policy=policy,
        ),
        seed_box=config.seed_box,
        downloaders=config.downloaders,
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


def make_torrent_with_state(hash_value, category, progress, state):
    torrent = make_torrent(hash_value, category, progress)
    torrent.state = state
    return torrent


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


def test_seedbox_download_replaces_corrupt_existing_local_torrent(tmp_path, monkeypatch):
    config = make_config(tmp_path, auto_dl_torrent_from_seedbox=True)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    local_torrent_path = Path(config.transfer.original_torrent_path) / "origin-hash.torrent"
    local_torrent_path.write_bytes(b"corrupt-local")
    DownloadingSFTPClient.download_calls = []
    DownloadingSFTPClient.payload = b"fresh-remote"

    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=FakeSeedboxClient([make_completed_torrent()], add_response="Ok.")),
    )
    monkeypatch.setattr(seedbox_manager_module, "SFTPClient", DownloadingSFTPClient)

    class FakeTorrentFile:
        def __init__(self, file_path):
            self.file_path = file_path
            data = Path(file_path).read_bytes()
            if data == b"corrupt-local":
                raise seedbox_manager_module.TorrentTrailingDataError(
                    file_path=str(file_path),
                    total_size=len(data),
                    valid_prefix_size=5,
                    original_error=ValueError("invalid bencoded value (data after valid prefix)"),
                )
            self.trackers = []

        def add_trackers(self, _trackers):
            return None

        def save(self, save_path):
            Path(save_path).write_bytes(Path(save_path).read_bytes())
            return True

    monkeypatch.setattr(seedbox_manager_module, "TorrentFile", FakeTorrentFile)

    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    assert DownloadingSFTPClient.download_calls == [
        ("/remote/torrents/origin-hash.torrent", str(local_torrent_path) + ".tmp")
    ]
    assert local_torrent_path.read_bytes() == b"fresh-remote"
    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")
    assert final_state.origin_torrent_file_path == str(local_torrent_path)
    assert final_state.download_retry_count == 0


def test_seedbox_download_rejects_invalid_remote_torrent_before_replace(tmp_path, monkeypatch):
    config = make_config(tmp_path, auto_dl_torrent_from_seedbox=True)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    local_torrent_path = Path(config.transfer.original_torrent_path) / "origin-hash.torrent"
    DownloadingSFTPClient.download_calls = []
    DownloadingSFTPClient.payload = b"invalid-remote"

    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=FakeSeedboxClient([make_completed_torrent()], add_response="Ok.")),
    )
    monkeypatch.setattr(seedbox_manager_module, "SFTPClient", DownloadingSFTPClient)

    class FakeTorrentFile:
        def __init__(self, file_path):
            raise ValueError(f"cannot parse {file_path}")

    monkeypatch.setattr(seedbox_manager_module, "TorrentFile", FakeTorrentFile)

    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    assert not local_torrent_path.exists()
    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")
    assert final_state.download_retry_count == 1
    assert "cannot parse" in final_state.last_error


def test_seedbox_bt_in_missing_files_state_is_treated_as_unusable(tmp_path, monkeypatch):
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
            seedbox_bt_health="missing_files",
        )
    )

    seedbox_torrents = [
        make_torrent("origin-hash", "To", 1),
        make_torrent_with_state("bt-hash", "BT", 0.2, "missingFiles"),
    ]
    client = FakeSeedboxClient(seedbox_torrents, add_response="Ok.")
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_seed_box is False
    assert final_state.is_skipped is False
    assert client.add_calls == []


def test_seedbox_does_not_add_bt_when_origin_is_missing_files(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    Path(tmp_path / "bt.torrent").write_text("bt", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
            seedbox_bt_health="missing_files",
        )
    )

    seedbox_torrents = [make_torrent_with_state("origin-hash", "To", 0.6, "missingFiles")]
    client = FakeSeedboxClient(seedbox_torrents, add_response="Ok.")
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_seed_box is False
    assert client.add_calls == []


def test_seedbox_add_success_is_verified_before_marking_bt_available(tmp_path, monkeypatch):
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

    client = AddCreatesMissingFilesSeedboxClient([make_completed_torrent()], add_response="Ok.")
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    home_trigger = threading.Event()
    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        trigger_home=home_trigger,
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert client.add_calls
    assert final_state.is_bt_in_seed_box is False
    assert final_state.seedbox_bt_health == "missing_files"
    assert home_trigger.is_set() is False


def test_seedbox_add_success_without_visible_bt_waits_without_recheck(tmp_path, monkeypatch):
    config = make_config_with_missing_policy(tmp_path, "force_recheck_and_rebuild_bt")
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

    client = FakeSeedboxClient([make_completed_torrent()], add_response="Ok.")
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    home_trigger = threading.Event()
    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        trigger_home=home_trigger,
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert client.add_calls
    assert final_state.is_bt_in_seed_box is False
    assert final_state.seedbox_bt_health == "missing_torrent"
    assert final_state.is_skipped is False
    assert client.delete_calls == []
    assert client.recheck_calls == []
    assert home_trigger.is_set() is False


def test_seedbox_waits_after_recheck_before_readding_bt(tmp_path, monkeypatch):
    config = make_config_with_missing_policy(tmp_path, "force_recheck_and_rebuild_bt")
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
            seedbox_bt_health="missing_files",
            seedbox_origin_data_status="recheck_requested",
            seedbox_origin_data_recheck_count=1,
        )
    )

    client = FakeSeedboxClient([make_completed_torrent()], add_response="Ok.")
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_seed_box is False
    assert final_state.seedbox_origin_data_status == "recheck_requested"
    assert client.add_calls == []


def test_force_recheck_policy_deletes_bad_seedbox_bt_without_files_and_rechecks_origin(tmp_path, monkeypatch):
    config = make_config_with_missing_policy(tmp_path, "force_recheck_and_rebuild_bt")
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
        )
    )

    seedbox_torrents = [
        make_torrent("origin-hash", "To", 1),
        make_torrent_with_state("bt-hash", "BT", 0.2, "missingFiles"),
    ]
    client = FakeSeedboxClient(seedbox_torrents, add_response="Ok.")
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_bt_in_seed_box is False
    assert final_state.seedbox_origin_data_status == "recheck_requested"
    assert client.delete_calls == [{"torrent_hashes": "bt-hash", "delete_files": False}]
    assert client.recheck_calls == [{"torrent_hashes": "origin-hash"}]


def test_skip_policy_marks_missing_seedbox_source_skipped_without_deleting(tmp_path, monkeypatch):
    config = make_config_with_missing_policy(tmp_path, "skip_transfer")
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
        )
    )

    seedbox_torrents = [
        make_torrent("origin-hash", "To", 1),
        make_torrent_with_state("bt-hash", "BT", 0.2, "missingFiles"),
    ]
    client = FakeSeedboxClient(seedbox_torrents, add_response="Ok.")
    monkeypatch.setattr(
        seedbox_manager_module,
        "get_downloader_client",
        lambda **_kwargs: SimpleNamespace(client=client),
    )

    manager = SeedBoxManager(
        config,
        StateManager(config.transfer.torrent_info_path),
        "seedbox",
        "home",
        threading.Event(),
        async_downloads=False,
    )
    manager.run()

    final_state = StateManager(config.transfer.torrent_info_path).get("origin-hash")

    assert final_state.is_skipped is True
    assert "missingFiles" in final_state.skip_reason
    assert client.delete_calls == []
    assert client.recheck_calls == []
