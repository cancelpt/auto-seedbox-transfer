from __future__ import annotations

import hashlib
import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import transfer.direct_piece_downloader as direct_piece_downloader_module
from transfer.direct_piece_downloader import (
    DirectPieceDownloader,
    DirectPieceDownloadError,
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


def test_direct_piece_downloader_propagates_reader_factory_failure(tmp_path):
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
    reader_error = RuntimeError("reader initialization failed")

    def failing_reader_factory():
        raise reader_error

    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=failing_reader_factory,
        workers=1,
        progress_callback=events.append,
    )

    with pytest.raises(RuntimeError) as raised:
        downloader.download()

    assert raised.value is reader_error
    assert list(store.iter_incomplete_pieces()) == [0]
    assert [event.kind for event in events] == ["started", "failed"]
    assert events[-1].downloaded_pieces == 0
    assert events[-1].error == "reader initialization failed"


def test_direct_piece_downloader_waits_for_all_failed_workers_and_emits_one_terminal_event(tmp_path):
    torrent = _build_torrent(
        name="single.bin",
        piece_length=4,
        files=[("single.bin", b"abcdefgh")],
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
    reader_errors = [RuntimeError("reader zero failed"), RuntimeError("reader one failed")]
    factory_barrier = threading.Barrier(2)
    factory_lock = threading.Lock()
    factory_started = [threading.Event(), threading.Event()]
    factory_finished = [threading.Event(), threading.Event()]
    release_second_worker = threading.Event()
    next_factory_index = 0

    def failing_reader_factory():
        nonlocal next_factory_index
        with factory_lock:
            factory_index = next_factory_index
            next_factory_index += 1
        factory_started[factory_index].set()
        try:
            factory_barrier.wait(timeout=5)
            if factory_index == 1:
                assert release_second_worker.wait(timeout=5)
            raise reader_errors[factory_index]
        finally:
            factory_finished[factory_index].set()

    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=failing_reader_factory,
        workers=2,
        progress_callback=events.append,
    )
    outcome = {}
    caller_finished = threading.Event()

    def run_download():
        try:
            outcome["result"] = downloader.download()
        except Exception as exc:
            outcome["error"] = exc
        finally:
            caller_finished.set()

    caller = threading.Thread(target=run_download, name="direct-piece-test-caller", daemon=True)
    caller.start()
    try:
        assert all(started.wait(timeout=5) for started in factory_started)
        assert factory_finished[0].wait(timeout=5)
        assert caller_finished.is_set() is False
    finally:
        release_second_worker.set()

    assert caller_finished.wait(timeout=5)
    caller.join()

    assert all(finished.is_set() for finished in factory_finished)
    assert outcome.get("error") in reader_errors
    assert "result" not in outcome
    assert list(store.iter_incomplete_pieces()) == [0, 1]
    assert [event.kind for event in events if event.kind in {"completed", "failed"}] == ["failed"]


def test_direct_piece_downloader_joins_started_worker_when_later_start_fails(tmp_path, monkeypatch):
    payload = b"abcdefgh"
    torrent = _build_torrent(
        name="single.bin",
        piece_length=4,
        files=[("single.bin", payload)],
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
    worker_inflight = threading.Event()
    release_worker = threading.Event()
    worker_closed = threading.Event()
    coordinator_order: queue.Queue[str] = queue.Queue()
    start_error = RuntimeError("worker start failed")

    class BlockingReader(FakeRangeReader):
        def read_range(self, remote_path: str, offset: int, length: int) -> bytes:
            worker_inflight.set()
            assert release_worker.wait(timeout=5)
            return super().read_range(remote_path, offset, length)

        def close(self):
            super().close()
            worker_closed.set()

    reader = BlockingReader({manifest.files[0].remote_path: payload}, [])
    real_thread_class = threading.Thread
    created_threads = []

    class StartFailingThread(real_thread_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.worker_index = len(created_threads)
            created_threads.append(self)

        def start(self):
            if self.worker_index == 1:
                assert worker_inflight.wait(timeout=5)
                raise start_error
            return super().start()

        def join(self, timeout=None):
            coordinator_order.put("join")
            return super().join(timeout)

    monkeypatch.setattr(
        direct_piece_downloader_module,
        "threading",
        SimpleNamespace(
            Event=threading.Event,
            Lock=threading.Lock,
            Thread=StartFailingThread,
        ),
    )
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=lambda: reader,
        workers=2,
        progress_callback=events.append,
    )
    outcome = {}
    caller_finished = threading.Event()

    def run_download():
        try:
            outcome["result"] = downloader.download()
        except Exception as exc:
            outcome["error"] = exc
        finally:
            caller_finished.set()
            coordinator_order.put("return")

    caller = real_thread_class(target=run_download, name="direct-piece-start-failure-caller", daemon=True)
    caller.start()
    try:
        assert coordinator_order.get(timeout=5) == "join"
        assert caller_finished.is_set() is False
    finally:
        release_worker.set()
        caller.join(timeout=5)
        for thread in created_threads:
            if thread.ident is not None:
                real_thread_class.join(thread, timeout=5)

    assert caller.is_alive() is False
    assert outcome.get("error") is start_error
    assert "result" not in outcome
    assert worker_closed.is_set() is True
    assert len(created_threads) == 2
    assert store.is_piece_complete(0) is True
    assert store.is_piece_complete(1) is False
    assert [event.kind for event in events] == ["started", "piece_verified", "failed"]
    assert events[-1].error == "worker start failed"


def test_direct_piece_downloader_fails_when_reader_cleanup_fails(tmp_path):
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

    class CleanupFailingReader(FakeRangeReader):
        def close(self):
            self.closed = True
            raise RuntimeError("reader cleanup failed")

    reader = CleanupFailingReader({manifest.files[0].remote_path: b"abcd"}, [])
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=lambda: reader,
        workers=1,
        progress_callback=events.append,
    )

    with pytest.raises(RuntimeError, match="reader cleanup failed"):
        downloader.download()

    assert reader.closed is True
    assert store.is_piece_complete(0) is True
    assert [event.kind for event in events] == ["started", "piece_verified", "failed"]
    assert events[-1].error == "reader cleanup failed"


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_direct_piece_downloader_stops_after_failure_but_keeps_inflight_piece(tmp_path):
    payload = b"abcdefghijkl"
    torrent = _build_torrent(
        name="single.bin",
        piece_length=4,
        files=[("single.bin", payload)],
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
    read_barrier = threading.Barrier(2)
    primary_worker_closed = threading.Event()
    inflight_worker_closed = threading.Event()
    read_offsets = []
    closed_offsets = []

    class CoordinatedReader:
        def __init__(self):
            self.offset = None

        def read_range(self, _remote_path: str, offset: int, length: int) -> bytes:
            self.offset = offset
            read_offsets.append(offset)
            if offset in {0, 4}:
                read_barrier.wait(timeout=5)
            if offset == 0:
                raise RuntimeError("primary worker failed")
            if offset == 4:
                assert primary_worker_closed.wait(timeout=5)
            return payload[offset : offset + length]

        def close(self):
            closed_offsets.append(self.offset)
            if self.offset == 0:
                primary_worker_closed.set()
            if self.offset == 4:
                inflight_worker_closed.set()
                raise RuntimeError("inflight reader cleanup failed")

    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=CoordinatedReader,
        workers=2,
        progress_callback=events.append,
    )

    with pytest.raises(RuntimeError, match="primary worker failed"):
        downloader.download()

    assert primary_worker_closed.is_set() is True
    assert inflight_worker_closed.is_set() is True
    assert sorted(read_offsets) == [0, 4]
    assert sorted(closed_offsets) == [0, 4]
    assert store.is_piece_complete(0) is False
    assert store.is_piece_complete(1) is True
    assert store.is_piece_complete(2) is False
    assert [event.kind for event in events if event.kind in {"completed", "failed"}] == ["failed"]
    assert events[-1].error == "primary worker failed"


def test_direct_piece_downloader_rejects_incomplete_post_join_resume_state(tmp_path):
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
    store.mark_piece_complete = lambda _piece_index: None
    events: list[DirectPieceDownloadProgressEvent] = []
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=_reader_factory({manifest.files[0].remote_path: b"abcd"}, []),
        workers=1,
        progress_callback=events.append,
    )

    with pytest.raises(DirectPieceDownloadError, match="resume state.*incomplete"):
        downloader.download()

    assert store.is_piece_complete(0) is False
    assert [event.kind for event in events] == ["started", "piece_verified", "failed"]


def test_direct_piece_downloader_rejects_incomplete_piece_aggregate(tmp_path):
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

    class PieceCountChangingReader(FakeRangeReader):
        def close(self):
            super().close()
            torrent.piece_count = 2

    reader = PieceCountChangingReader({manifest.files[0].remote_path: b"abcd"}, [])
    downloader = DirectPieceDownloader(
        torrent=torrent,
        manifest=manifest,
        resume_store=store,
        reader_factory=lambda: reader,
        workers=1,
        progress_callback=events.append,
    )

    with pytest.raises(DirectPieceDownloadError, match="accounted for 1 of 2 pieces"):
        downloader.download()

    assert list(store.iter_incomplete_pieces()) == []
    assert [event.kind for event in events] == ["started", "piece_verified", "failed"]
