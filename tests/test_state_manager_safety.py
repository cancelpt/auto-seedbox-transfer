from managers.state_manager import StateManager
from transfer.torrent_transfer import TorrentTransfer


def test_get_returns_detached_copy(tmp_path):
    state_path = tmp_path / "state.json"
    manager = StateManager(str(state_path))
    manager.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
        )
    )

    fetched = manager.get("origin-hash")
    fetched.is_skipped = True

    assert manager.get("origin-hash").is_skipped is False


def test_get_all_returns_detached_copies(tmp_path):
    state_path = tmp_path / "state.json"
    manager = StateManager(str(state_path))
    manager.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
        )
    )

    all_transfers = manager.get_all()
    all_transfers["origin-hash"].is_skipped = True

    assert manager.get("origin-hash").is_skipped is False
