import hashlib
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


def test_torrent_file_exposes_piece_hashes_and_single_file_piece_spans():
    piece_length = 4
    payload = b"abcdef"
    torrent = TorrentFile(
        {
            b"info": {
                b"name": b"single.bin",
                b"length": len(payload),
                b"piece length": piece_length,
                b"pieces": b"".join(
                    hashlib.sha1(payload[index : index + piece_length]).digest()
                    for index in range(0, len(payload), piece_length)
                ),
            }
        }
    )

    assert torrent.total_size == len(payload)
    assert [torrent_file.path for torrent_file in torrent.files] == ["single.bin"]
    assert torrent.piece_hashes == [
        hashlib.sha1(b"abcd").digest(),
        hashlib.sha1(b"ef").digest(),
    ]
    assert torrent.piece_hash_hexes == [
        hashlib.sha1(b"abcd").hexdigest(),
        hashlib.sha1(b"ef").hexdigest(),
    ]
    assert torrent.get_piece_size(1) == 2
    spans = list(torrent.iter_piece_file_spans(1))

    assert [
        (span.file_index, span.file_path, span.file_offset, span.piece_offset, span.length)
        for span in spans
    ] == [
        (0, "single.bin", 4, 0, 2),
    ]


def test_torrent_file_iterates_cross_file_piece_spans_deterministically():
    first_file = b"abc"
    second_file = b"defgh"
    payload = first_file + second_file
    piece_length = 4
    torrent = TorrentFile(
        {
            b"info": {
                b"name": b"sample",
                b"piece length": piece_length,
                b"pieces": b"".join(
                    hashlib.sha1(payload[index : index + piece_length]).digest()
                    for index in range(0, len(payload), piece_length)
                ),
                b"files": [
                    {
                        b"length": len(first_file),
                        b"path": [b"disc1.txt"],
                    },
                    {
                        b"length": len(second_file),
                        b"path": [b"nested", b"disc2.bin"],
                    },
                ],
            }
        }
    )

    first_piece_spans = list(torrent.iter_piece_file_spans(0))
    second_piece_spans = list(torrent.iter_piece_file_spans(1))

    assert [
        (span.file_index, span.file_path, span.file_offset, span.piece_offset, span.length)
        for span in first_piece_spans
    ] == [
        (0, "disc1.txt", 0, 0, 3),
        (1, "nested/disc2.bin", 0, 3, 1),
    ]
    assert [
        (span.file_index, span.file_path, span.file_offset, span.piece_offset, span.length)
        for span in second_piece_spans
    ] == [
        (1, "nested/disc2.bin", 1, 0, 4),
    ]
