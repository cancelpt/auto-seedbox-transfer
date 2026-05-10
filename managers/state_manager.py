import fcntl
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
        self.transfer_file_lock_path = f"{transfer_file_path}.state.lock"
        self.transfer_status_dict: Dict[str, TorrentTransfer] = {}
        self.lock = threading.Lock()
        self.load()

    def _acquire_file_lock(self, lock_type: int):
        os.makedirs(os.path.dirname(self.transfer_file_lock_path) or ".", exist_ok=True)
        lock_file = open(self.transfer_file_lock_path, "a+", encoding="utf-8")
        fcntl.flock(lock_file.fileno(), lock_type)
        return lock_file

    @staticmethod
    def _release_file_lock(lock_file):
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()

    def load(self):
        """Load transfer status from file."""
        with self.lock:
            lock_file = self._acquire_file_lock(fcntl.LOCK_SH)
            try:
                self.transfer_status_dict = {}
                if os.path.exists(self.transfer_file_path):
                    try:
                        with open(self.transfer_file_path, "r", encoding="utf-8") as f:
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
            finally:
                self._release_file_lock(lock_file)

    def save(self):
        """Save transfer status to file."""
        with self.lock:
            lock_file = self._acquire_file_lock(fcntl.LOCK_EX)
            try:
                transfer_status_list = [transfer.model_dump() for transfer in self.transfer_status_dict.values()]
                temp_path = f"{self.transfer_file_path}.tmp.{os.getpid()}.{threading.get_ident()}"
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(transfer_status_list, f)
                os.replace(temp_path, self.transfer_file_path)
            except Exception as e:
                logger.error(f"Failed to save transfer file: {self.transfer_file_path} - {e}")
            finally:
                self._release_file_lock(lock_file)

    def get(self, info_hash: str) -> Optional[TorrentTransfer]:
        """Get transfer status by hash."""
        with self.lock:
            transfer = self.transfer_status_dict.get(info_hash)
            if transfer is None:
                return None
            return transfer.model_copy(deep=True)

    def update(self, transfer: TorrentTransfer):
        """Update transfer status."""
        with self.lock:
            self.transfer_status_dict[transfer.hash] = transfer.model_copy(deep=True)
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
            return {
                info_hash: transfer.model_copy(deep=True)
                for info_hash, transfer in self.transfer_status_dict.items()
            }
