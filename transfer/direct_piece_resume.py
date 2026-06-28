from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from transfer.direct_piece_manifest import DirectPieceManifest


class DirectPieceResumeStore:
    def __init__(self, resume_dir: str | Path, manifest: DirectPieceManifest):
        self.resume_dir = Path(resume_dir)
        self.manifest = manifest
        self._lock = threading.Lock()
        self._piece_states = bytearray()

    @property
    def manifest_path(self) -> Path:
        return self.resume_dir / f"{self.manifest.info_hash}.manifest.json"

    @property
    def pieces_path(self) -> Path:
        return self.resume_dir / f"{self.manifest.info_hash}.pieces"

    def initialize(self) -> None:
        self.resume_dir.mkdir(parents=True, exist_ok=True)
        saved_manifest = self._read_manifest_file()
        if not self._is_compatible(saved_manifest):
            self._piece_states = bytearray(self.manifest.piece_count)
            self._write_manifest(created_at=None)
            self._write_piece_state_file()
            return

        if not self.pieces_path.exists() or self.pieces_path.stat().st_size != self.manifest.piece_count:
            self._piece_states = bytearray(self.manifest.piece_count)
            self._write_piece_state_file()
            return

        self._piece_states = bytearray(self.pieces_path.read_bytes())

    def iter_incomplete_pieces(self):
        with self._lock:
            snapshot = bytes(self._piece_states)
        for piece_index, status in enumerate(snapshot):
            if status != 1:
                yield piece_index

    def is_piece_complete(self, piece_index: int) -> bool:
        with self._lock:
            return bool(self._piece_states[piece_index])

    def mark_piece_complete(self, piece_index: int) -> None:
        self._write_piece_state(piece_index, 1)

    def mark_piece_incomplete(self, piece_index: int) -> None:
        self._write_piece_state(piece_index, 0)

    def _write_piece_state(self, piece_index: int, value: int) -> None:
        with self._lock:
            self._piece_states[piece_index] = value
            with self.pieces_path.open("r+b") as handle:
                handle.seek(piece_index)
                handle.write(bytes([value]))
                handle.flush()
                os.fsync(handle.fileno())

    def _read_manifest_file(self) -> dict | None:
        if not self.manifest_path.exists():
            return None
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _is_compatible(self, saved_manifest: dict | None) -> bool:
        if saved_manifest is None:
            return False
        expected = self.manifest.to_dict()
        actual = {key: saved_manifest.get(key) for key in expected}
        return actual == expected

    def _write_manifest(self, created_at: float | None) -> None:
        payload = self.manifest.to_dict()
        now = time.time()
        payload["created_at"] = created_at or now
        payload["updated_at"] = now
        with self.manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _write_piece_state_file(self) -> None:
        with self.pieces_path.open("wb") as handle:
            handle.write(self._piece_states)
            handle.flush()
            os.fsync(handle.fileno())
