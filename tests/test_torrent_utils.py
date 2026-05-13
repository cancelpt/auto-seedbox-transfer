from types import SimpleNamespace

import pytest

import utils.torrent_utils as torrent_utils
from utils.torrent_utils import TorrentFile, TorrentTrailingDataError


def test_torrent_file_reports_recoverable_trailing_bencoded_data(tmp_path, monkeypatch):
    valid_data = b"d4:infod4:name7:example12:piece lengthi1e6:pieces0:ee"
    trailing_data = b"stale-tail"
    torrent_path = tmp_path / "bad.torrent"
    torrent_path.write_bytes(valid_data + trailing_data)

    def decode(data):
        if data == valid_data:
            return {
                b"info": {
                    b"name": b"example",
                    b"piece length": 1,
                    b"pieces": b"",
                }
            }
        raise ValueError("invalid bencoded value (data after valid prefix)")

    monkeypatch.setattr(torrent_utils, "bencodepy", SimpleNamespace(decode=decode, encode=lambda data: b"encoded"))

    with pytest.raises(TorrentTrailingDataError) as exc_info:
        TorrentFile(str(torrent_path))

    assert exc_info.value.file_path == str(torrent_path)
    assert exc_info.value.total_size == len(valid_data + trailing_data)
    assert exc_info.value.valid_prefix_size == len(valid_data)
    assert exc_info.value.trailing_size == len(trailing_data)
    assert "尾随数据" in str(exc_info.value)
