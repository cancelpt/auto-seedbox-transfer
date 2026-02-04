import logging
import time

from qbittorrentapi import Client, TorrentInfoList

from managers.state_manager import StateManager
from utils.config import Config, SeedBox
from utils.downloader_utils import get_downloader_client, DownloaderHelper

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
            logger.info(f"Adding BT torrent to seedbox: {torrent.name}")
            if 'Ok.' in seed_box_dl.torrents_add(
                torrent_files=state.bt_torrent_file_path,
                category=self.config.transfer.seed_box_bt_category,
                is_skip_checking=True,
                download_path=torrent.save_path
            ):
                logger.info(f"Successfully added BT torrent: {state.bt_hash}")
                state.is_bt_in_seed_box = True
                self.state_manager.update(state)
                add_torrent_count += 1
            else:
                logger.error(f"Failed to add BT torrent: {state.bt_hash}")
