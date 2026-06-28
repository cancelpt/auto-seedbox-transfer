from __future__ import annotations

import logging
import os
import threading

from managers.state_manager import StateManager
from transfer.direct_piece_downloader import DirectPieceDownloader
from transfer.direct_piece_manifest import build_direct_piece_manifest
from transfer.direct_piece_resume import DirectPieceResumeStore
from transfer.direct_piece_telemetry import DirectPieceTelemetryProjector
from utils.config import Config, resolve_downloader_network_profile, resolve_seedbox_network_profile
from utils.downloader_utils import get_downloader_client
from utils.qbittorrent_snapshot import QbittorrentSnapshot
from utils.sftp_utils import SFTPClient
from utils.torrent_utils import TorrentFile, TorrentTrailingDataError

logger = logging.getLogger(__name__)


class DirectTransferManager:
    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        seed_box_name: str,
        target_download_dir: str | None,
        shutdown_event: threading.Event,
        trigger_home=None,
    ):
        self.config = config
        self.state_manager = state_manager
        self.seed_box_name = seed_box_name
        self.target_download_dir = target_download_dir
        self.shutdown_event = shutdown_event
        self.trigger_home = trigger_home
        self._init_configs()
        self.seed_box_helper = get_downloader_client(
            downloader=self.seed_box_dl_config,
            network_profile=self.seed_box_downloader_network_profile,
        )
        self.seed_box_snapshot = QbittorrentSnapshot(self.seed_box_helper.client)

    def _init_configs(self):
        self.seed_box_config = next(filter(lambda x: x.name == self.seed_box_name, self.config.seed_box), None)
        if self.seed_box_config is None:
            raise ValueError(f"Seedbox config not found: {self.seed_box_name}")

        self.seed_box_dl_config = next(
            filter(lambda x: x.name == self.seed_box_name, self.config.downloaders),
            None,
        )
        if self.seed_box_dl_config is None:
            raise ValueError(f"Seedbox downloader config not found: {self.seed_box_name}")

        self.seed_box_downloader_network_profile = resolve_downloader_network_profile(
            self.config,
            self.seed_box_dl_config,
        )
        self.seed_box_network_profile = resolve_seedbox_network_profile(
            self.config,
            self.seed_box_config,
        )

    def run(self):
        try:
            self._process_direct_transfers()
        except Exception as e:
            logger.error(f"Error in DirectTransferManager: {e}")

    def _process_direct_transfers(self):
        self.seed_box_snapshot.refresh()
        processed_transfers = 0
        max_once_add = self.config.transfer.max_once_add
        for info_hash, state in self.state_manager.get_all().items():
            if self.shutdown_event.is_set():
                break
            if processed_transfers >= max_once_add:
                logger.info(f"Direct transfer max add limit reached ({max_once_add})")
                break
            if state.is_skipped or state.is_torrent_in_home_dl or state.is_direct_payload_ready:
                continue
            if not self._local_torrent_is_usable(state.origin_torrent_file_path):
                continue

            origin_torrent = self.seed_box_snapshot.torrent(info_hash)
            if origin_torrent is None or getattr(origin_torrent, "progress", 0) != 1:
                continue

            download_root = state.direct_payload_root or self.target_download_dir
            if not download_root:
                self._record_error(state, "Direct piece pull requires target_download_dir")
                continue

            remote_save_path = getattr(origin_torrent, "save_path", "")
            if not remote_save_path:
                self._record_error(state, f"Seedbox origin save_path unavailable for direct transfer: {info_hash}")
                continue

            processed_transfers += 1
            try:
                torrent = TorrentFile(state.origin_torrent_file_path)
                manifest = build_direct_piece_manifest(
                    torrent=torrent,
                    remote_save_path=remote_save_path,
                    local_download_path=download_root,
                )
                resume_store = DirectPieceResumeStore(
                    resume_dir=self.config.transfer.direct_piece_resume_path,
                    manifest=manifest,
                )
                telemetry = DirectPieceTelemetryProjector(
                    manifest=manifest,
                    resume_store=resume_store,
                    workers=self.config.transfer.direct_piece_workers,
                    progress_log_interval_seconds=getattr(
                        self.config.transfer,
                        "direct_piece_progress_log_interval_seconds",
                        30,
                    ),
                    stall_timeout_seconds=getattr(
                        self.config.transfer,
                        "direct_piece_stall_timeout_seconds",
                        180,
                    ),
                    logger=logger,
                )
                downloader = DirectPieceDownloader(
                    torrent=torrent,
                    manifest=manifest,
                    resume_store=resume_store,
                    reader_factory=self._reader_factory,
                    workers=self.config.transfer.direct_piece_workers,
                    progress_callback=telemetry.handle_event,
                )
                try:
                    result = downloader.download()
                finally:
                    telemetry.close()
                state.is_direct_payload_ready = True
                state.direct_payload_root = download_root
                state.last_error = ""
                self.state_manager.update(state)
                logger.info(
                    "Direct payload ready for %s (%s downloaded, %s skipped)",
                    info_hash,
                    result.downloaded_pieces,
                    result.skipped_pieces,
                )
                if self.trigger_home:
                    self.trigger_home.set()
            except Exception as e:
                self._record_error(state, f"Direct piece pull failed for {info_hash}: {e}", download_root)

    def _reader_factory(self):
        client = SFTPClient(
            hostname=self.seed_box_config.ssh_host,
            username=self.seed_box_config.ssh_user,
            password=self.seed_box_config.ssh_password,
            port=self.seed_box_config.ssh_port,
            proxy_endpoint=None if self.seed_box_network_profile is None else self.seed_box_network_profile.sftp,
        )
        client.connect()
        return client

    def _local_torrent_is_usable(self, torrent_path: str) -> bool:
        if not torrent_path or not os.path.exists(torrent_path):
            return False
        try:
            TorrentFile(torrent_path)
            return True
        except TorrentTrailingDataError as e:
            logger.warning(
                "Local origin torrent has trailing bencode data and cannot drive direct transfer: "
                f"{torrent_path} ({e.trailing_size} trailing bytes)"
            )
            return False
        except Exception as e:
            logger.warning(f"Local origin torrent is unreadable and cannot drive direct transfer: {torrent_path}: {e}")
            return False

    def _record_error(self, state, message: str, download_root: str | None = None):
        state.is_direct_payload_ready = False
        if download_root:
            state.direct_payload_root = download_root
        state.last_error = message
        self.state_manager.update(state)
        logger.error(message)
