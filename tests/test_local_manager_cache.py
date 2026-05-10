import threading
from pathlib import Path

import managers.local_manager as local_manager_module
from managers.local_manager import LocalManager
from managers.state_manager import StateManager
from transfer.torrent_transfer import TorrentTransfer
from utils.config import Config, Downloader, SeedBox, Transfer


class FakeTorrentFile:
    calls = 0

    def __init__(self, file_path):
        type(self).calls += 1
        self.file_path = file_path
        self.info_hash = "hash-a"
        self.torrent_data = {}


def make_config(tmp_path):
    return Config(
        transfer=Transfer(
            original_torrent_path=str(tmp_path / "downloads"),
            bt_path=str(tmp_path / "bt"),
            torrent_info_path=str(tmp_path / "state.json"),
            bt_trackers=[],
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
            ),
            Downloader(
                name="home",
                url="http://home:8080",
                username="user",
                password="pass",
            ),
        ],
    )


def test_local_manager_skips_reparsing_unchanged_torrent_when_state_already_exists(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    Path(config.transfer.original_torrent_path).mkdir(parents=True, exist_ok=True)
    Path(config.transfer.bt_path).mkdir(parents=True, exist_ok=True)
    torrent_path = Path(config.transfer.original_torrent_path) / "a.torrent"
    torrent_path.write_text("torrent-data", encoding="utf-8")

    initial_state = StateManager(config.transfer.torrent_info_path)
    initial_state.update(
        TorrentTransfer(
            hash="hash-a",
            origin_torrent_file_path=str(torrent_path),
            bt_hash="bt-a",
            bt_torrent_file_path=str(Path(config.transfer.bt_path) / "a.bt.torrent"),
        )
    )

    monkeypatch.setattr(local_manager_module, "TorrentFile", FakeTorrentFile)

    manager = LocalManager(config, StateManager(config.transfer.torrent_info_path))
    manager.run()
    manager.run()

    assert FakeTorrentFile.calls == 1
