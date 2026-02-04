import logging
import os
import shutil
import time
from typing import Optional

from managers.state_manager import StateManager
from transfer.torrent_transfer import TorrentTransfer
from utils.config import Config
from utils.torrent_utils import TorrentFile, export_as_torrent

logger = logging.getLogger(__name__)


class LocalManager:
    def __init__(self, config: Config, state_manager: StateManager):
        self.config = config
        self.state_manager = state_manager
        self.bt_path = config.transfer.bt_path
        self.bt_path = config.transfer.bt_path

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

        # hash to file info
        torrent_hash_to_info = {}

        for root, dirs, files in os.walk(original_torrent_path):
            for file in files:
                if file.endswith('.torrent'):
                    torrent_file_path = os.path.join(root, file)
                    try:
                        torrent_file_info = TorrentFile(str(torrent_file_path))
                        torrent_hash_to_info[torrent_file_info.info_hash] = torrent_file_info

                        # Check if already processed
                        if self.state_manager.get(torrent_file_info.info_hash):
                            continue

                        # Convert to BT
                        self._convert_to_bt(torrent_file_info)

                    except Exception as e:
                        logger.error(f"Failed to process torrent {torrent_file_path}: {e}")

    def _convert_to_bt(self, torrent_file_info: TorrentFile):
        """Convert a single torrent to BT format."""
        try:
            result, temp_bt_file_name, bt_torrent_file = export_as_torrent(
                torrent_file_info.torrent_data,
                self.config.transfer.bt_trackers
            )

            if not result:
                logger.error(f"Failed to export BT torrent: {torrent_file_info.file_path}")
                return

            bt_file_path = os.path.join(self.bt_path, temp_bt_file_name)
            shutil.move(temp_bt_file_name, bt_file_path)

            logger.info(f"Exported BT torrent: {bt_file_path}, hash: {bt_torrent_file.info_hash}")

            transfer = TorrentTransfer(
                hash=torrent_file_info.info_hash,
                bt_hash=bt_torrent_file.info_hash,
                origin_torrent_file_path=torrent_file_info.file_path,
                bt_torrent_file_path=bt_file_path
            )
            self.state_manager.update(transfer)

        except Exception as e:
            logger.error(f"Exception converting to BT {torrent_file_info.file_path}: {e}")

    def _cleanup_deleted_torrents(self):
        """Remove entries from state if original file no longer exists."""
        all_transfers = self.state_manager.get_all()
        for info_hash, transfer in all_transfers.items():
            if not os.path.exists(transfer.origin_torrent_file_path):
                logger.info(f"Original torrent missing, removing from state: {info_hash}")
                self.state_manager.delete(info_hash)
