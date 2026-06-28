from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from transfer.direct_piece_downloader import DirectPieceDownloadProgressEvent
from transfer.direct_piece_manifest import DirectPieceManifest
from transfer.direct_piece_resume import DirectPieceResumeStore


class DirectPieceStatusState(str, Enum):
    running = "running"
    stalled = "stalled"
    completed = "completed"
    failed = "failed"


@dataclass(frozen=True)
class DirectPieceStatusSnapshot:
    info_hash: str
    name: str
    state: DirectPieceStatusState
    piece_count: int
    completed_pieces: int
    remaining_pieces: int
    total_bytes: int
    completed_bytes: int
    percent: float
    workers: int
    started_at: float
    updated_at: float
    last_piece_completed_at: float | None
    seconds_since_last_progress: float
    bytes_per_second_recent: float
    bytes_per_second_average: float
    eta_seconds: float | None
    local_root: str
    remote_root: str
    last_error: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "DirectPieceStatusSnapshot":
        return cls(
            info_hash=payload["info_hash"],
            name=payload["name"],
            state=DirectPieceStatusState(payload["state"]),
            piece_count=payload["piece_count"],
            completed_pieces=payload["completed_pieces"],
            remaining_pieces=payload["remaining_pieces"],
            total_bytes=payload["total_bytes"],
            completed_bytes=payload["completed_bytes"],
            percent=payload["percent"],
            workers=payload["workers"],
            started_at=payload["started_at"],
            updated_at=payload["updated_at"],
            last_piece_completed_at=payload["last_piece_completed_at"],
            seconds_since_last_progress=payload["seconds_since_last_progress"],
            bytes_per_second_recent=payload["bytes_per_second_recent"],
            bytes_per_second_average=payload["bytes_per_second_average"],
            eta_seconds=payload["eta_seconds"],
            local_root=payload["local_root"],
            remote_root=payload["remote_root"],
            last_error=payload["last_error"],
        )


def load_direct_piece_status_snapshot(path: str | Path) -> DirectPieceStatusSnapshot:
    with Path(path).open("r", encoding="utf-8") as handle:
        return DirectPieceStatusSnapshot.from_dict(json.load(handle))


class DirectPieceTelemetryProjector:
    def __init__(
        self,
        *,
        manifest: DirectPieceManifest,
        resume_store: DirectPieceResumeStore,
        workers: int,
        progress_log_interval_seconds: int,
        stall_timeout_seconds: int,
        logger,
        time_fn: Callable[[], float] = time.time,
        monitor_interval_seconds: float = 1.0,
        snapshot_write_interval_seconds: float = 5.0,
        recent_window_seconds: float = 60.0,
        start_monitor_thread: bool = True,
    ):
        self.manifest = manifest
        self.resume_store = resume_store
        self.workers = workers
        self.progress_log_interval_seconds = progress_log_interval_seconds
        self.stall_timeout_seconds = stall_timeout_seconds
        self._logger = logger
        self._time_fn = time_fn
        self._monitor_interval_seconds = monitor_interval_seconds
        self._snapshot_write_interval_seconds = snapshot_write_interval_seconds
        self._recent_window_seconds = recent_window_seconds
        self._start_monitor_thread = start_monitor_thread

        self.status_path = self.resume_store.resume_dir / f"{self.manifest.info_hash}.status.json"
        self._piece_sizes = [
            min(
                self.manifest.piece_length,
                max(self.manifest.total_size - piece_index * self.manifest.piece_length, 0),
            )
            for piece_index in range(self.manifest.piece_count)
        ]

        self._lock = threading.Lock()
        self._recent_samples: deque[tuple[float, int]] = deque()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None

        self._state = DirectPieceStatusState.running
        self._started_at = 0.0
        self._updated_at = 0.0
        self._last_piece_completed_at: float | None = None
        self._last_progress_reference_at = 0.0
        self._last_error = ""
        self._completed_pieces = 0
        self._completed_bytes = 0
        self._last_log_at = 0.0
        self._last_write_at = 0.0
        self._started = False

    def handle_event(self, event: DirectPieceDownloadProgressEvent) -> None:
        try:
            self._handle_event(event)
        except Exception as exc:  # pragma: no cover - defensive operational path
            log_warning = getattr(self._logger, "warning", None)
            if callable(log_warning):
                log_warning("Direct piece telemetry update failed for %s: %s", self.manifest.info_hash, exc)

    def close(self) -> None:
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=max(self._monitor_interval_seconds * 2, 1.0))
            self._monitor_thread = None

    def tick(self, now: float | None = None) -> None:
        try:
            self._tick(now=now)
        except Exception as exc:  # pragma: no cover - defensive operational path
            log_warning = getattr(self._logger, "warning", None)
            if callable(log_warning):
                log_warning("Direct piece telemetry monitor failed for %s: %s", self.manifest.info_hash, exc)

    def _handle_event(self, event: DirectPieceDownloadProgressEvent) -> None:
        if event.kind == "started":
            with self._lock:
                self._started = True
                self._state = DirectPieceStatusState.running
                self._started_at = event.timestamp
                self._updated_at = event.timestamp
                self._last_piece_completed_at = None
                self._last_progress_reference_at = event.timestamp
                self._last_error = ""
                incomplete_pieces = list(self.resume_store.iter_incomplete_pieces())
                remaining_bytes = sum(self._piece_sizes[piece_index] for piece_index in incomplete_pieces)
                self._completed_pieces = self.manifest.piece_count - len(incomplete_pieces)
                self._completed_bytes = self.manifest.total_size - remaining_bytes
                self._recent_samples.clear()
                self._recent_samples.append((event.timestamp, self._completed_bytes))
                self._last_log_at = event.timestamp
                self._last_write_at = 0.0
                self._write_snapshot_locked(event.timestamp, force=True)
                self._ensure_monitor_thread_locked()
            return

        if not self._started:
            return

        if event.kind == "piece_verified":
            with self._lock:
                if self._state == DirectPieceStatusState.stalled:
                    self._state = DirectPieceStatusState.running
                    self._log_resumed_locked()
                self._completed_pieces = min(self._completed_pieces + 1, self.manifest.piece_count)
                if event.piece_index is not None:
                    self._completed_bytes = min(
                        self._completed_bytes + self._piece_sizes[event.piece_index],
                        self.manifest.total_size,
                    )
                self._updated_at = event.timestamp
                self._last_piece_completed_at = event.timestamp
                self._last_progress_reference_at = event.timestamp
                self._recent_samples.append((event.timestamp, self._completed_bytes))
                self._trim_recent_samples_locked(event.timestamp)
                self._write_snapshot_locked(event.timestamp, force=True)
            return

        if event.kind == "completed":
            with self._lock:
                self._state = DirectPieceStatusState.completed
                self._completed_pieces = self.manifest.piece_count
                self._completed_bytes = self.manifest.total_size
                self._updated_at = event.timestamp
                if self._last_piece_completed_at is None and self.manifest.piece_count > 0:
                    self._last_piece_completed_at = event.timestamp
                self._last_progress_reference_at = event.timestamp
                self._recent_samples.append((event.timestamp, self._completed_bytes))
                self._trim_recent_samples_locked(event.timestamp)
                self._write_snapshot_locked(event.timestamp, force=True)
                self._monitor_stop.set()
            return

        if event.kind == "failed":
            with self._lock:
                self._state = DirectPieceStatusState.failed
                self._updated_at = event.timestamp
                self._last_error = event.error or ""
                self._write_snapshot_locked(event.timestamp, force=True)
                self._monitor_stop.set()

    def _ensure_monitor_thread_locked(self) -> None:
        if not self._start_monitor_thread or self._monitor_thread is not None:
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name=f"direct-piece-telemetry-{self.manifest.info_hash[:8]}",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(self._monitor_interval_seconds):
            self.tick()

    def _tick(self, now: float | None = None) -> None:
        with self._lock:
            if not self._started or self._state in (DirectPieceStatusState.completed, DirectPieceStatusState.failed):
                return
            current_time = self._time_fn() if now is None else now
            self._updated_at = current_time
            if (
                self._state == DirectPieceStatusState.running
                and current_time - self._last_progress_reference_at >= self.stall_timeout_seconds
            ):
                self._state = DirectPieceStatusState.stalled
                self._write_snapshot_locked(current_time, force=True)
                self._log_stalled_locked(current_time)
            elif current_time - self._last_write_at >= self._snapshot_write_interval_seconds:
                self._write_snapshot_locked(current_time, force=True)

            if current_time - self._last_log_at >= self.progress_log_interval_seconds:
                self._log_progress_locked(current_time)
                self._last_log_at = current_time

    def _trim_recent_samples_locked(self, now: float) -> None:
        threshold = now - self._recent_window_seconds
        while len(self._recent_samples) > 1 and self._recent_samples[0][0] < threshold:
            self._recent_samples.popleft()

    def _build_snapshot_locked(self, now: float) -> DirectPieceStatusSnapshot:
        self._trim_recent_samples_locked(now)
        remaining_pieces = max(self.manifest.piece_count - self._completed_pieces, 0)
        percent = 100.0 if self.manifest.total_size == 0 else round(self._completed_bytes * 100 / self.manifest.total_size, 2)
        last_progress_at = self._last_piece_completed_at or self._started_at or now
        seconds_since_last_progress = round(max(now - last_progress_at, 0.0), 2)

        recent_speed = 0.0
        if self._recent_samples:
            first_timestamp, first_completed_bytes = self._recent_samples[0]
            recent_duration = max(now - first_timestamp, 0.0)
            if recent_duration > 0:
                recent_speed = max(self._completed_bytes - first_completed_bytes, 0) / recent_duration

        average_speed = 0.0
        lifetime = max(now - self._started_at, 0.0)
        if lifetime > 0:
            average_speed = self._completed_bytes / lifetime

        remaining_bytes = max(self.manifest.total_size - self._completed_bytes, 0)
        eta_seconds = round(remaining_bytes / recent_speed, 2) if recent_speed > 0 else None

        return DirectPieceStatusSnapshot(
            info_hash=self.manifest.info_hash,
            name=self.manifest.torrent_name,
            state=self._state,
            piece_count=self.manifest.piece_count,
            completed_pieces=self._completed_pieces,
            remaining_pieces=remaining_pieces,
            total_bytes=self.manifest.total_size,
            completed_bytes=self._completed_bytes,
            percent=percent,
            workers=self.workers,
            started_at=self._started_at,
            updated_at=now,
            last_piece_completed_at=self._last_piece_completed_at,
            seconds_since_last_progress=seconds_since_last_progress,
            bytes_per_second_recent=round(recent_speed, 2),
            bytes_per_second_average=round(average_speed, 2),
            eta_seconds=eta_seconds,
            local_root=self.manifest.local_root,
            remote_root=self.manifest.remote_root,
            last_error=self._last_error,
        )

    def _write_snapshot_locked(self, now: float, *, force: bool) -> None:
        if not force and now - self._last_write_at < self._snapshot_write_interval_seconds:
            return

        snapshot = self._build_snapshot_locked(now)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.status_path.with_suffix(f".status.json.tmp.{threading.get_ident()}")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(snapshot.to_dict(), handle, ensure_ascii=False, indent=2)
        temp_path.replace(self.status_path)
        self._last_write_at = now

    def _log_progress_locked(self, now: float) -> None:
        log_info = getattr(self._logger, "info", None)
        if not callable(log_info):
            return
        snapshot = self._build_snapshot_locked(now)
        log_info(
            "Direct piece progress %s %s pieces=%s/%s bytes=%s/%s percent=%.1f%% speed=%s/s eta=%s state=%s",
            snapshot.info_hash,
            snapshot.name,
            snapshot.completed_pieces,
            snapshot.piece_count,
            snapshot.completed_bytes,
            snapshot.total_bytes,
            snapshot.percent,
            f"{snapshot.bytes_per_second_recent:.2f}",
            "unknown" if snapshot.eta_seconds is None else f"{snapshot.eta_seconds:.2f}s",
            snapshot.state.value,
        )

    def _log_stalled_locked(self, now: float) -> None:
        log_warning = getattr(self._logger, "warning", None)
        if not callable(log_warning):
            return
        snapshot = self._build_snapshot_locked(now)
        log_warning(
            "Direct piece transfer stalled for %s %s: no verified piece progress for %.2fs",
            snapshot.info_hash,
            snapshot.name,
            snapshot.seconds_since_last_progress,
        )

    def _log_resumed_locked(self) -> None:
        log_info = getattr(self._logger, "info", None)
        if callable(log_info):
            log_info("Direct piece transfer resumed for %s %s", self.manifest.info_hash, self.manifest.torrent_name)
