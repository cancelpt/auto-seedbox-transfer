from __future__ import annotations

import hashlib
import queue
import threading
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


class DirectPieceDownloader:
    def __init__(
        self,
        torrent: TorrentFile,
        manifest: DirectPieceManifest,
        resume_store: DirectPieceResumeStore,
        reader_factory: Callable[[], object],
        workers: int = 4,
    ):
        self.torrent = torrent
        self.manifest = manifest
        self.resume_store = resume_store
        self.reader_factory = reader_factory
        self.workers = max(1, workers)

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
        if not incomplete_pieces:
            return result

        piece_queue: queue.Queue[int] = queue.Queue()
        for piece_index in incomplete_pieces:
            piece_queue.put(piece_index)

        result_lock = threading.Lock()
        error_lock = threading.Lock()
        error_holder: list[Exception] = []
        stop_event = threading.Event()

        def worker() -> None:
            reader = self.reader_factory()
            try:
                while not stop_event.is_set():
                    try:
                        piece_index = piece_queue.get_nowait()
                    except queue.Empty:
                        return

                    try:
                        piece_data = self._read_piece(reader, piece_index)
                        self._verify_piece(piece_index, piece_data)
                        self._write_piece(piece_index, piece_data)
                        self.resume_store.mark_piece_complete(piece_index)
                        with result_lock:
                            result.downloaded_pieces += 1
                    except Exception as exc:  # pragma: no cover - exercised via joined worker state
                        with error_lock:
                            if not error_holder:
                                error_holder.append(exc)
                        stop_event.set()
                        return
            finally:
                close = getattr(reader, "close", None)
                if callable(close):
                    close()

        threads = [
            threading.Thread(target=worker, name=f"direct-piece-worker-{index}", daemon=True)
            for index in range(min(self.workers, len(incomplete_pieces)))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if error_holder:
            raise error_holder[0]

        return result

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
