from __future__ import annotations

import hashlib
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from transfer.direct_piece_manifest import DirectPieceManifest
from transfer.direct_piece_resume import DirectPieceResumeStore
from utils.torrent_utils import TorrentFile


class DirectPieceDownloadError(RuntimeError):
    pass


class MissingRemoteFileError(DirectPieceDownloadError):
    pass


class PieceHashMismatchError(DirectPieceDownloadError):
    pass


@dataclass
class DirectPieceDownloadResult:
    downloaded_pieces: int = 0
    skipped_pieces: int = 0


@dataclass(frozen=True)
class DirectPieceDownloadProgressEvent:
    kind: str
    timestamp: float
    downloaded_pieces: int
    skipped_pieces: int
    piece_index: int | None = None
    error: str | None = None


class DirectPieceDownloader:
    def __init__(
        self,
        torrent: TorrentFile,
        manifest: DirectPieceManifest,
        resume_store: DirectPieceResumeStore,
        reader_factory: Callable[[], object],
        workers: int = 4,
        progress_callback: Callable[[DirectPieceDownloadProgressEvent], None] | None = None,
        time_fn: Callable[[], float] = time.time,
    ):
        self.torrent = torrent
        self.manifest = manifest
        self.resume_store = resume_store
        self.reader_factory = reader_factory
        self.workers = max(1, workers)
        self.progress_callback = progress_callback
        self.time_fn = time_fn

    def prepare_local_files(self) -> None:
        for manifest_file in self.manifest.files:
            local_path = Path(manifest_file.local_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            if not local_path.exists():
                with local_path.open("wb") as handle:
                    handle.truncate(manifest_file.length)
                continue
            if local_path.stat().st_size != manifest_file.length:
                with local_path.open("r+b") as handle:
                    handle.truncate(manifest_file.length)

    def download(self) -> DirectPieceDownloadResult:
        self.resume_store.initialize()
        self.prepare_local_files()

        incomplete_pieces = list(self.resume_store.iter_incomplete_pieces())
        result = DirectPieceDownloadResult(
            downloaded_pieces=0,
            skipped_pieces=self.torrent.piece_count - len(incomplete_pieces),
        )
        self._emit_progress("started", result=result)

        piece_queue: queue.Queue[int] = queue.Queue()
        for piece_index in incomplete_pieces:
            piece_queue.put(piece_index)

        result_lock = threading.Lock()
        error_lock = threading.Lock()
        error_holder: list[Exception] = []
        stop_event = threading.Event()

        def record_failure(exc: Exception) -> None:
            with error_lock:
                if not error_holder:
                    error_holder.append(exc)
                stop_event.set()

        def worker() -> None:
            reader = None
            try:
                reader = self.reader_factory()
                while not stop_event.is_set():
                    try:
                        piece_index = piece_queue.get_nowait()
                    except queue.Empty:
                        return
                    if stop_event.is_set():
                        return

                    piece_data = self._read_piece(reader, piece_index)
                    self._verify_piece(piece_index, piece_data)
                    self._write_piece(piece_index, piece_data)
                    self.resume_store.mark_piece_complete(piece_index)
                    with result_lock:
                        result.downloaded_pieces += 1
                        downloaded_pieces = result.downloaded_pieces
                    self._emit_progress(
                        "piece_verified",
                        result=result,
                        piece_index=piece_index,
                        downloaded_pieces=downloaded_pieces,
                    )
            except Exception as exc:  # pragma: no cover - exercised via joined worker state
                record_failure(exc)
            finally:
                if reader is not None:
                    try:
                        close = getattr(reader, "close", None)
                        if callable(close):
                            close()
                    except Exception as exc:  # pragma: no cover - exercised via joined worker state
                        record_failure(exc)

        threads = [
            threading.Thread(target=worker, name=f"direct-piece-worker-{index}", daemon=True)
            for index in range(min(self.workers, len(incomplete_pieces)))
        ]
        started_threads = []
        for thread in threads:
            if stop_event.is_set():
                break
            try:
                thread.start()
            except Exception as exc:
                record_failure(exc)
                break
            started_threads.append(thread)
        for thread in started_threads:
            thread.join()

        def fail(error: Exception) -> None:
            diagnostic = str(error) or type(error).__name__
            self._emit_progress("failed", result=result, error=diagnostic)
            raise error

        if error_holder:
            fail(error_holder[0])

        accounted_pieces = result.downloaded_pieces + result.skipped_pieces
        if accounted_pieces != self.torrent.piece_count:
            fail(
                DirectPieceDownloadError(
                    f"direct piece download accounted for {accounted_pieces} of {self.torrent.piece_count} pieces"
                )
            )

        try:
            remaining_pieces = list(self.resume_store.iter_incomplete_pieces())
        except Exception as exc:
            fail(exc)
        if remaining_pieces:
            fail(
                DirectPieceDownloadError(
                    f"direct piece download resume state remains incomplete for {len(remaining_pieces)} pieces"
                )
            )

        self._emit_progress("completed", result=result)
        return result

    def _emit_progress(
        self,
        kind: str,
        *,
        result: DirectPieceDownloadResult,
        piece_index: int | None = None,
        downloaded_pieces: int | None = None,
        error: str | None = None,
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(
            DirectPieceDownloadProgressEvent(
                kind=kind,
                timestamp=self.time_fn(),
                downloaded_pieces=result.downloaded_pieces if downloaded_pieces is None else downloaded_pieces,
                skipped_pieces=result.skipped_pieces,
                piece_index=piece_index,
                error=error,
            )
        )

    def _read_piece(self, reader, piece_index: int) -> bytes:
        piece_data = bytearray()
        for span in self.torrent.iter_piece_file_spans(piece_index):
            manifest_file = self.manifest.files[span.file_index]
            try:
                chunk = reader.read_range(
                    manifest_file.remote_path,
                    offset=span.file_offset,
                    length=span.length,
                )
            except FileNotFoundError as exc:
                raise MissingRemoteFileError(
                    f"missing remote file for piece {piece_index}: {manifest_file.relative_path}"
                ) from exc

            if len(chunk) != span.length:
                raise MissingRemoteFileError(
                    f"remote file ended early for piece {piece_index}: {manifest_file.relative_path}"
                )
            piece_data.extend(chunk)
        return bytes(piece_data)

    def _verify_piece(self, piece_index: int, piece_data: bytes) -> None:
        actual_hash = hashlib.sha1(piece_data).digest()
        if actual_hash != self.torrent.piece_hashes[piece_index]:
            raise PieceHashMismatchError(f"piece {piece_index} hash mismatch")

    def _write_piece(self, piece_index: int, piece_data: bytes) -> None:
        for span in self.torrent.iter_piece_file_spans(piece_index):
            manifest_file = self.manifest.files[span.file_index]
            with Path(manifest_file.local_path).open("r+b") as handle:
                handle.seek(span.file_offset)
                handle.write(piece_data[span.piece_offset : span.piece_offset + span.length])
