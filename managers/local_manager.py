from __future__ import annotations

import logging
import os
import shutil

from managers.state_manager import StateManager
from transfer.torrent_transfer import TorrentTransfer
from utils.config import Config
from utils.torrent_utils import TorrentFile, export_as_torrent

logger = logging.getLogger(__name__)


class LocalManager:
    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        trigger_seedbox=None,
        trigger_home=None,
    ):
        self.config = config
        self.state_manager = state_manager
        self.bt_path = config.transfer.bt_path
        self.failed_counts = {}
        self._torrent_file_cache: dict[str, tuple[int, int, str]] = {}
        self.trigger_seedbox = trigger_seedbox
        self.trigger_home = trigger_home

    def run(self):
        """Run local management tasks."""
        try:
            self._scan_and_convert()
            self._cleanup_deleted_torrents()
        except Exception as e:
            logger.error(f"Error in LocalManager: {e}")

    def _scan_and_convert(self):
        """Scan original torrents and convert to BT."""
        original_torrent_path = self.config.transfer.original_torrent_path
        logger.debug(f"Scanning {original_torrent_path} for torrents")
        seen_files = set()

        for root, dirs, files in os.walk(original_torrent_path):
            for file in files:
                if file.endswith(".torrent"):
                    torrent_file_path = os.path.join(root, file)
                    seen_files.add(torrent_file_path)

                    if self.failed_counts.get(torrent_file_path, 0) >= 3:
                        continue

                    try:
                        file_stat = os.stat(torrent_file_path)
                        cached_entry = self._torrent_file_cache.get(torrent_file_path)
                        if cached_entry and cached_entry[0] == file_stat.st_mtime_ns and cached_entry[1] == file_stat.st_size:
                            cached_state = self.state_manager.get(cached_entry[2])
                            if cached_state and (cached_state.is_skipped or cached_state.has_bt_torrent()):
                                continue

                        torrent_file_info = TorrentFile(str(torrent_file_path))
                        self._torrent_file_cache[torrent_file_path] = (
                            file_stat.st_mtime_ns,
                            file_stat.st_size,
                            torrent_file_info.info_hash,
                        )

                        # Check if already processed
                        state = self.state_manager.get(torrent_file_info.info_hash)
                        if state and (state.is_skipped or state.has_bt_torrent()):
                            if torrent_file_path in self.failed_counts:
                                del self.failed_counts[torrent_file_path]
                            continue

                        # Convert to BT
                        self._convert_to_bt(torrent_file_info, state)
                        if torrent_file_path in self.failed_counts:
                            del self.failed_counts[torrent_file_path]

                    except Exception as e:
                        self.failed_counts[torrent_file_path] = self.failed_counts.get(torrent_file_path, 0) + 1
                        logger.error(f"Failed to process torrent {torrent_file_path}: {e}")
                        if self.failed_counts[torrent_file_path] >= 3:
                            logger.warning(f"Skipping torrent {torrent_file_path} after 3 failed attempts.")

        if self._torrent_file_cache:
            self._torrent_file_cache = {
                torrent_file_path: cache_entry
                for torrent_file_path, cache_entry in self._torrent_file_cache.items()
                if torrent_file_path in seen_files
            }

    def _convert_to_bt(self, torrent_file_info: TorrentFile, existing_transfer: TorrentTransfer | None = None):
        """Convert a single torrent to BT format."""
        result, temp_bt_file_name, bt_torrent_file = export_as_torrent(
            torrent_file_info.torrent_data, self.config.transfer.bt_trackers
        )

        if not result:
            raise RuntimeError(f"Failed to export BT torrent: {torrent_file_info.file_path}")

        bt_file_path = os.path.join(self.bt_path, temp_bt_file_name)
        shutil.move(temp_bt_file_name, bt_file_path)

        logger.info(f"Exported BT torrent: {bt_file_path}, hash: {bt_torrent_file.info_hash}")

        transfer = existing_transfer or TorrentTransfer(
            hash=torrent_file_info.info_hash,
            origin_torrent_file_path=torrent_file_info.file_path,
        )
        transfer.origin_torrent_file_path = torrent_file_info.file_path
        transfer.bt_hash = bt_torrent_file.info_hash
        transfer.bt_torrent_file_path = bt_file_path
        transfer.reset_failures(
            "download_retry_count",
            "seedbox_add_retry_count",
            "home_add_retry_count",
            "missing_origin_retry_count",
        )
        self.state_manager.update(transfer)

        # Trigger other managers to start working on this new BT torrent
        if self.trigger_seedbox:
            self.trigger_seedbox.set()
        if self.trigger_home:
            self.trigger_home.set()

    def _cleanup_deleted_torrents(self):
        """Remove entries from state if original file no longer exists."""
        all_transfers = self.state_manager.get_all()
        for info_hash, transfer in all_transfers.items():
            if os.path.exists(transfer.origin_torrent_file_path):
                continue

            if transfer.is_torrent_in_home_dl:
                logger.info(f"Completed transfer missing original torrent locally, removing state: {info_hash}")
                self.state_manager.delete(info_hash)
                continue

            if not transfer.has_bt_torrent():
                logger.debug(f"Pending transfer has no local origin yet, keeping state: {info_hash}")
                continue

            logger.warning(f"Original torrent missing for active transfer, preserving state: {info_hash}")
