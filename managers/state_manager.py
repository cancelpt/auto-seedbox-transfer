import json
import logging
import os
import threading
from typing import Dict, List, Optional

from transfer.torrent_transfer import TorrentTransfer

logger = logging.getLogger(__name__)


class StateManager:
    def __init__(self, transfer_file_path: str):
        self.transfer_file_path = transfer_file_path
        self.transfer_status_dict: Dict[str, TorrentTransfer] = {}
        self.lock = threading.Lock()
        self.load()

    def load(self):
        """Load transfer status from file."""
        with self.lock:
            if os.path.exists(self.transfer_file_path):
                try:
                    with open(self.transfer_file_path, 'r') as f:
                        transfer_status_list: List[dict] = json.load(f)

                    for transfer_data in transfer_status_list:
                        try:
                            transfer = TorrentTransfer(**transfer_data)
                            self.transfer_status_dict[transfer.hash] = transfer
                        except Exception as e:
                            logger.error(f"Error creating TorrentTransfer from data: {transfer_data} - {e}")
                except Exception as e:
                    logger.error(f"Failed to load transfer file: {self.transfer_file_path} - {e}")
            else:
                logger.warning(f"Transfer file does not exist: {self.transfer_file_path}")

    def save(self):
        """Save transfer status to file."""
        with self.lock:
            try:
                transfer_status_list = [transfer.dict() for transfer in self.transfer_status_dict.values()]
                with open(self.transfer_file_path, 'w') as f:
                    json.dump(transfer_status_list, f)
            except Exception as e:
                logger.error(f"Failed to save transfer file: {self.transfer_file_path} - {e}")

    def get(self, info_hash: str) -> Optional[TorrentTransfer]:
        """Get transfer status by hash."""
        with self.lock:
            return self.transfer_status_dict.get(info_hash)

    def update(self, transfer: TorrentTransfer):
        """Update transfer status."""
        with self.lock:
            self.transfer_status_dict[transfer.hash] = transfer
        self.save()

    def delete(self, info_hash: str):
        """Delete transfer status by hash."""
        with self.lock:
            if info_hash in self.transfer_status_dict:
                del self.transfer_status_dict[info_hash]
        self.save()

    def get_all(self) -> Dict[str, TorrentTransfer]:
        """Get copy of all transfer statuses."""
        with self.lock:
            return self.transfer_status_dict.copy()
