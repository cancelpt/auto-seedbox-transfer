import hashlib
import json
from pathlib import Path

import pytest

from transfer.direct_piece_downloader import (
    DirectPieceDownloader,
    DirectPieceDownloadProgressEvent,
    MissingRemoteFileError,
    PieceHashMismatchError,
)
from transfer.direct_piece_manifest import build_direct_piece_manifest
from transfer.direct_piece_resume import DirectPieceResumeStore
from utils.torrent_utils import TorrentFile


def _build_torrent(
    name: str,
    piece_length: int,
    files: list[tuple[str, bytes]],
    single_file: bool = False,
) -> TorrentFile:
    payload = b"".join(file_bytes for _, file_bytes in files)
    pieces = b"".join(
        hashlib.sha1(payload[index : index + piece_length]).digest()
        for index in range(0, len(payload), piece_length)
    )
    info = {
        b"name": name.encode("utf-8"),
        b"piece length": piece_length,
        b"pieces": pieces,
    }
    if single_file:
        info[b"length"] = len(files[0][1])
    else:
        info[b"files"] = [
            {
                b"length": len(file_bytes),
                b"path": [segment.encode("utf-8") for segment in relative_path.split("/")],
            }
            for relative_path, file_bytes in files
        ]
    return TorrentFile({b"info": info})


class FakeRangeReader:
    def __init__(self, remote_files: dict[str, bytes], read_log: list[tuple[str, int, int]]):
        self.remote_files = remote_files
        self.read_log = read_log
        self.closed = False

    def read_range(self, remote_path: str, offset: int, length: int) -> bytes:
        self.read_log.append((remote_path, offset, length))
        if remote_path not in self.remote_files:
            raise FileNotFoundError(remote_path)
        data = self.remote_files[remote_path]
        return data[offset : offset + length]

    def close(self):
        self.closed = True


def _reader_factory(remote_files: dict[str, bytes], read_log: list[tuple[str, int, int]]):
    def factory():
        return FakeRangeReader(remote_files, read_log)

    return factory


def test_build_direct_piece_manifest_uses_qb_save_path_layout(tmp_path):
    torrent = _build_torrent(
        name="album",
        piece_length=4,
        files=[
            ("disc1.txt", b"abc"),
            ("nested/disc2.bin", b"defgh"),
        ],
    )

    manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "local"),
    )

    assert manifest.remote_root == "/remote/downloads/album"
    assert manifest.local_root == str(tmp_path / "local" / "album")
    assert [
        (file.relative_path, file.remote_path, file.local_path, file.length, file.offset)
        for file in manifest.files
    ] == [
        (
            "disc1.txt",
            "/remote/downloads/album/disc1.txt",
            str(tmp_path / "local" / "album" / "disc1.txt"),
            3,
            0,
        ),
        (
            "nested/disc2.bin",
            "/remote/downloads/album/nested/disc2.bin",
            str(tmp_path / "local" / "album" / "nested" / "disc2.bin"),
            5,
            3,
        ),
    ]


def test_resume_store_resets_piece_state_when_manifest_target_changes(tmp_path):
    torrent = _build_torrent(
        name="album",
        piece_length=4,
        files=[
            ("disc1.txt", b"abc"),
            ("nested/disc2.bin", b"defgh"),
        ],
    )
    resume_dir = tmp_path / "resume"
    initial_manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "first-target"),
    )
    initial_store = DirectPieceResumeStore(resume_dir=resume_dir, manifest=initial_manifest)
    initial_store.initialize()
    initial_store.mark_piece_complete(0)

    changed_manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "second-target"),
    )
    changed_store = DirectPieceResumeStore(resume_dir=resume_dir, manifest=changed_manifest)
    changed_store.initialize()

    assert list(changed_store.iter_incomplete_pieces()) == [0, 1]
    manifest_path = resume_dir / f"{changed_manifest.info_hash}.manifest.json"
    saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved_manifest["local_root"] == changed_manifest.local_root


def test_direct_piece_downloader_resumes_only_incomplete_pieces(tmp_path):
    torrent = _build_torrent(
        name="album",
        piece_length=4,
        files=[
            ("disc1.txt", b"abc"),
            ("nested/disc2.bin", b"defgh"),
        ],
    )
    manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "local"),
    )
    store = DirectPieceResumeStore(resume_dir=tmp_path / "resume", manifest=manifest)
    store.initialize()
    read_log: list[tuple[str, int, int]] = []
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=_reader_factory(
            {
                manifest.files[0].remote_path: b"abc",
                manifest.files[1].remote_path: b"defgh",
            },
            read_log,
        ),
        workers=2,
    )
    downloader.prepare_local_files()

    first_piece = b"abcd"
    for span in torrent.iter_piece_file_spans(0):
        local_path = Path(manifest.files[span.file_index].local_path)
        with local_path.open("r+b") as handle:
            handle.seek(span.file_offset)
            handle.write(first_piece[span.piece_offset : span.piece_offset + span.length])
    store.mark_piece_complete(0)

    result = downloader.download()

    assert result.downloaded_pieces == 1
    assert result.skipped_pieces == 1
    assert read_log == [
        (manifest.files[1].remote_path, 1, 4),
    ]
    assert Path(manifest.files[0].local_path).read_bytes() == b"abc"
    assert Path(manifest.files[1].local_path).read_bytes() == b"defgh"


def test_direct_piece_downloader_leaves_piece_incomplete_on_hash_mismatch(tmp_path):
    torrent = _build_torrent(
        name="single.bin",
        piece_length=4,
        files=[("single.bin", b"abcd")],
        single_file=True,
    )
    manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "local"),
    )
    store = DirectPieceResumeStore(resume_dir=tmp_path / "resume", manifest=manifest)
    store.initialize()
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=_reader_factory(
            {
                manifest.files[0].remote_path: b"abce",
            },
            [],
        ),
        workers=1,
    )

    with pytest.raises(PieceHashMismatchError, match="piece 0"):
        downloader.download()

    assert store.is_piece_complete(0) is False
    assert Path(manifest.files[0].local_path).read_bytes() == b"\x00\x00\x00\x00"


def test_direct_piece_downloader_raises_missing_remote_file(tmp_path):
    torrent = _build_torrent(
        name="album",
        piece_length=4,
        files=[
            ("disc1.txt", b"abc"),
            ("nested/disc2.bin", b"defgh"),
        ],
    )
    manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "local"),
    )
    store = DirectPieceResumeStore(resume_dir=tmp_path / "resume", manifest=manifest)
    store.initialize()
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=_reader_factory(
            {
                manifest.files[0].remote_path: b"abc",
            },
            [],
        ),
        workers=1,
    )

    with pytest.raises(MissingRemoteFileError, match="nested/disc2.bin"):
        downloader.download()

    assert store.is_piece_complete(0) is False


def test_direct_piece_downloader_emits_progress_events(tmp_path):
    torrent = _build_torrent(
        name="album",
        piece_length=4,
        files=[
            ("disc1.txt", b"abc"),
            ("nested/disc2.bin", b"defgh"),
        ],
    )
    manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "local"),
    )
    store = DirectPieceResumeStore(resume_dir=tmp_path / "resume", manifest=manifest)
    store.initialize()
    store.mark_piece_complete(0)
    events: list[DirectPieceDownloadProgressEvent] = []
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=_reader_factory(
            {
                manifest.files[0].remote_path: b"abc",
                manifest.files[1].remote_path: b"defgh",
            },
            [],
        ),
        workers=1,
        progress_callback=events.append,
    )

    result = downloader.download()

    assert result.downloaded_pieces == 1
    assert [event.kind for event in events] == ["started", "piece_verified", "completed"]
    assert events[0].skipped_pieces == 1
    assert events[1].piece_index == 1
    assert events[1].downloaded_pieces == 1


def test_direct_piece_downloader_emits_failure_event(tmp_path):
    torrent = _build_torrent(
        name="single.bin",
        piece_length=4,
        files=[("single.bin", b"abcd")],
        single_file=True,
    )
    manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "local"),
    )
    store = DirectPieceResumeStore(resume_dir=tmp_path / "resume", manifest=manifest)
    store.initialize()
    events: list[DirectPieceDownloadProgressEvent] = []
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=_reader_factory(
            {
                manifest.files[0].remote_path: b"abce",
            },
            [],
        ),
        workers=1,
        progress_callback=events.append,
    )

    with pytest.raises(PieceHashMismatchError, match="piece 0"):
        downloader.download()

    assert [event.kind for event in events] == ["started", "failed"]
    assert events[-1].error == "piece 0 hash mismatch"
