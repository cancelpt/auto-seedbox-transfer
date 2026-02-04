import logging
import time

import os
from pathlib import Path
from qbittorrentapi import Client, TorrentInfoList

from managers.state_manager import StateManager
from utils.config import Config, SeedBox
from utils.downloader_utils import get_downloader_client, DownloaderHelper
from utils.sftp_utils import SFTPClient

logger = logging.getLogger(__name__)


class SeedBoxManager:
    def __init__(self, config: Config, state_manager: StateManager, seed_box_name: str, home_dl_name: str):
        self.config = config
        self.state_manager = state_manager
        self.seed_box_name = seed_box_name
        self.home_dl_name = home_dl_name
        self._init_configs()

    def _init_configs(self):
        """Initialize configurations for seedbox and downloaders."""
        self.seed_box_config = next(filter(lambda x: x.name == self.seed_box_name, self.config.seed_box), None)
        if self.seed_box_config is None:
            raise ValueError(f"Seedbox config not found: {self.seed_box_name}")

        self.seed_box_dl_config = next(filter(lambda x: x.name == self.seed_box_name, self.config.downloaders), None)
        if self.seed_box_dl_config is None:
            raise ValueError(f"Seedbox downloader config not found: {self.seed_box_name}")

        self.home_dl_config = next(filter(lambda x: x.name == self.home_dl_name, self.config.downloaders), None)
        if self.home_dl_config is None:
            raise ValueError(f"Home downloader config not found: {self.home_dl_name}")

    def run(self):
        """Run seedbox management tasks."""
        try:
            self._process_seedbox_torrents()
        except Exception as e:
            logger.error(f"Error in SeedBoxManager: {e}")

    def _process_seedbox_torrents(self):
        seed_box_helper: DownloaderHelper = get_downloader_client(
            name=self.seed_box_dl_config.name,
            url=self.seed_box_dl_config.url,
            username=self.seed_box_dl_config.username,
            password=self.seed_box_dl_config.password
        )
        seed_box_dl: Client = seed_box_helper.client
        
        # Get completed torrents
        # logger.info(f"Fetching torrent list from seedbox: {self.seed_box_dl_config.name}")
        torrents: TorrentInfoList = seed_box_dl.torrents_info(status='completed')
        seed_box_torrent_hashes = [torrent.hash for torrent in torrents]
        
        add_torrent_count = 0
        max_once_add = self.config.transfer.max_once_add

        # Collect torrents that need downloading
        torrents_to_download = []
        if self.config.transfer.auto_dl_torrent_from_seedbox:
             for torrent in torrents:
                 # Skip if category doesn't match
                 if torrent.category != self.home_dl_config.want_torrent_category:
                     continue
                 
                 # Check if exists in local state
                 if self.state_manager.get(torrent.hash):
                     continue
                 
                 # Check if exists locally (avoid double download if LocalManager hasn't scanned yet)
                 local_path = os.path.join(self.config.transfer.original_torrent_path, f"{torrent.hash}.torrent")
                 if os.path.exists(local_path):
                     continue

                 torrents_to_download.append(torrent.hash)
        
        # Batch download if needed
        if torrents_to_download:
             self._batch_download_torrents_from_seedbox(torrents_to_download)

        for torrent in torrents:
            if add_torrent_count >= max_once_add:
                logger.info(f"Seedbox max add limit reached ({max_once_add})")
                break

            if torrent.category != self.home_dl_config.want_torrent_category:
                continue

            # Check if exists in local state
            state = self.state_manager.get(torrent.hash)
            if not state:
                # logger.debug(f"Torrent not in local state: {torrent.name}")
                continue

            # Check progress (double check completion)
            if torrent.progress != 1:
                continue

            # Logic: If already in home downloader, delete from seedbox
            if state.is_torrent_in_home_dl:
                if state.bt_hash in seed_box_torrent_hashes:
                    logger.info(f"Deleting completed BT torrent from seedbox: {state.bt_hash}")
                    seed_box_dl.torrents_delete(torrent_hashes=state.bt_hash, delete_files=True)
                
                if state.hash in seed_box_torrent_hashes:
                    logger.info(f"Deleting completed Origin torrent from seedbox: {state.hash}")
                    seed_box_dl.torrents_delete(torrent_hashes=state.hash, delete_files=True)
                continue

            # Logic: Add BT torrent to seedbox if not present
            if state.bt_hash in seed_box_torrent_hashes:
                if not state.is_bt_in_seed_box:
                    logger.info(f"BT torrent found on seedbox, updating state: {state.bt_hash}")
                    state.is_bt_in_seed_box = True
                    self.state_manager.update(state)
                continue

            # Add BT torrent
            logger.info(f"Adding BT torrent to seedbox: {torrent.name}, {torrent.save_path}")
            if 'Ok.' in seed_box_dl.torrents_add(
                torrent_files=state.bt_torrent_file_path,
                category=self.config.transfer.seed_box_bt_category,
                is_skip_checking=True,
                save_path=torrent.save_path
            ):
                logger.info(f"Successfully added BT torrent: {state.bt_hash}")
                state.is_bt_in_seed_box = True
                self.state_manager.update(state)
                add_torrent_count += 1
            else:
                logger.error(f"Failed to add BT torrent: {state.bt_hash}")

    def _batch_download_torrents_from_seedbox(self, torrent_hashes: list):
        """Batch download torrent files from seedbox via SFTP."""
        if not torrent_hashes:
            return

        logger.info(f"Starting batch download for {len(torrent_hashes)} torrents from seedbox...")
        sftp_client = None
        try:
            sftp_client = SFTPClient(
                hostname=self.seed_box_config.ssh_host,
                username=self.seed_box_config.ssh_user,
                password=self.seed_box_config.ssh_password,
                port=self.seed_box_config.ssh_port
            )
            sftp_client.connect()
            
            for torrent_hash in torrent_hashes:
                try:
                    remote_path = Path(self.seed_box_config.torrents_path) / f"{torrent_hash}.torrent"
                    local_path = os.path.join(self.config.transfer.original_torrent_path, f"{torrent_hash}.torrent")
                    
                    if os.path.exists(local_path):
                        continue

                    logger.info(f"Downloading torrent {torrent_hash} from seedbox...")
                    sftp_client.download(remote_path.as_posix(), local_path)
                except Exception as e:
                    logger.error(f"Failed to download torrent {torrent_hash}: {e}")
                    
        except Exception as e:
             logger.error(f"SFTP connection error: {e}")
        finally:
            if sftp_client:
                sftp_client.close()
