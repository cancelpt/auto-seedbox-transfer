import hashlib

from transfer.direct_piece_downloader import DirectPieceDownloadProgressEvent
from transfer.direct_piece_manifest import build_direct_piece_manifest
from transfer.direct_piece_resume import DirectPieceResumeStore
from transfer.direct_piece_telemetry import (
    DirectPieceStatusState,
    DirectPieceTelemetryProjector,
    load_direct_piece_status_snapshot,
)
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


class FakeClock:
    def __init__(self, now: float):
        self._now = now

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeLogger:
    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def info(self, message, *args):
        self.records.append(("info", message % args if args else message))

    def warning(self, message, *args):
        self.records.append(("warning", message % args if args else message))


def test_projector_writes_periodic_snapshots_and_detects_stall_resume(tmp_path):
    torrent = _build_torrent(
        name="album",
        piece_length=4,
        files=[("disc1.txt", b"abcd"), ("disc2.txt", b"efgh")],
    )
    manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "local"),
    )
    store = DirectPieceResumeStore(resume_dir=tmp_path / "resume", manifest=manifest)
    store.initialize()
    store.mark_piece_complete(0)
    clock = FakeClock(1_000.0)
    logger = FakeLogger()
    projector = DirectPieceTelemetryProjector(
        manifest=manifest,
        resume_store=store,
        workers=2,
        progress_log_interval_seconds=30,
        stall_timeout_seconds=180,
        logger=logger,
        time_fn=clock.time,
        start_monitor_thread=False,
    )

    projector.handle_event(
        DirectPieceDownloadProgressEvent(
            kind="started",
            timestamp=clock.time(),
            downloaded_pieces=0,
            skipped_pieces=1,
        )
    )

    started_snapshot = load_direct_piece_status_snapshot(projector.status_path)
    assert started_snapshot.state == DirectPieceStatusState.running
    assert started_snapshot.completed_pieces == 1
    assert started_snapshot.completed_bytes == 4
    assert started_snapshot.remaining_pieces == 1
    assert started_snapshot.percent == 50.0

    clock.advance(30)
    projector.tick()

    assert any(
        level == "info" and "50.0%" in message and "state=running" in message
        for level, message in logger.records
    )

    clock.advance(180)
    projector.tick()

    stalled_snapshot = load_direct_piece_status_snapshot(projector.status_path)
    assert stalled_snapshot.state == DirectPieceStatusState.stalled
    assert stalled_snapshot.seconds_since_last_progress == 210.0
    assert any(level == "warning" and "stalled" in message for level, message in logger.records)

    projector.handle_event(
        DirectPieceDownloadProgressEvent(
            kind="piece_verified",
            timestamp=clock.time(),
            piece_index=1,
            downloaded_pieces=1,
            skipped_pieces=1,
        )
    )
    resumed_snapshot = load_direct_piece_status_snapshot(projector.status_path)
    assert resumed_snapshot.state == DirectPieceStatusState.running
    assert resumed_snapshot.completed_pieces == 2
    assert resumed_snapshot.completed_bytes == 8
    assert resumed_snapshot.percent == 100.0
    assert any(level == "info" and "resumed" in message for level, message in logger.records)

    projector.handle_event(
        DirectPieceDownloadProgressEvent(
            kind="completed",
            timestamp=clock.time(),
            downloaded_pieces=1,
            skipped_pieces=1,
        )
    )
    completed_snapshot = load_direct_piece_status_snapshot(projector.status_path)
    assert completed_snapshot.state == DirectPieceStatusState.completed
    assert completed_snapshot.last_error == ""
    projector.close()


def test_projector_writes_failure_snapshot_with_last_error(tmp_path):
    torrent = _build_torrent(
        name="single.bin",
        piece_length=4,
        files=[("single.bin", b"abcdef")],
        single_file=True,
    )
    manifest = build_direct_piece_manifest(
        torrent=torrent,
        remote_save_path="/remote/downloads",
        local_download_path=str(tmp_path / "local"),
    )
    store = DirectPieceResumeStore(resume_dir=tmp_path / "resume", manifest=manifest)
    store.initialize()
    clock = FakeClock(500.0)
    projector = DirectPieceTelemetryProjector(
        manifest=manifest,
        resume_store=store,
        workers=1,
        progress_log_interval_seconds=30,
        stall_timeout_seconds=180,
        logger=FakeLogger(),
        time_fn=clock.time,
        start_monitor_thread=False,
    )

    projector.handle_event(
        DirectPieceDownloadProgressEvent(
            kind="started",
            timestamp=clock.time(),
            downloaded_pieces=0,
            skipped_pieces=0,
        )
    )
    clock.advance(12)
    projector.handle_event(
        DirectPieceDownloadProgressEvent(
            kind="failed",
            timestamp=clock.time(),
            downloaded_pieces=0,
            skipped_pieces=0,
            error="piece 0 hash mismatch",
        )
    )

    snapshot = load_direct_piece_status_snapshot(projector.status_path)
    assert snapshot.state == DirectPieceStatusState.failed
    assert snapshot.last_error == "piece 0 hash mismatch"
    assert snapshot.completed_pieces == 0
    assert snapshot.eta_seconds is None
    projector.close()
