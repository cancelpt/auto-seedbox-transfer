import logging
import os

from qbittorrentapi import Client

from managers.state_manager import StateManager
from utils.config import Config, SeedBox
from utils.downloader_utils import get_downloader_client, DownloaderHelper

logger = logging.getLogger(__name__)


class HomeManager:
    def __init__(self, config: Config, state_manager: StateManager, seed_box_name: str, home_dl_name: str, target_download_dir: str):
        self.config = config
        self.state_manager = state_manager
        self.seed_box_name = seed_box_name
        self.home_dl_name = home_dl_name
        self.target_download_dir = target_download_dir
        self._init_configs()

    def _init_configs(self):
        """Initialize configurations."""
        self.seed_box_config: SeedBox = next(filter(lambda x: x.name == self.seed_box_name, self.config.seed_box), None)
        if self.seed_box_config is None:
            raise ValueError(f"Seedbox config not found: {self.seed_box_name}")

        self.home_dl_config = next(filter(lambda x: x.name == self.home_dl_name, self.config.downloaders), None)
        if self.home_dl_config is None:
            raise ValueError(f"Home downloader config not found: {self.home_dl_name}")

    def run(self):
        """Run home management tasks."""
        try:
            self._process_home_torrents()
        except Exception as e:
            logger.error(f"Error in HomeManager: {e}")

    def _process_home_torrents(self):
        home_dl_helper: DownloaderHelper = get_downloader_client(
            name=self.home_dl_config.name,
            url=self.home_dl_config.url,
            username=self.home_dl_config.username,
            password=self.home_dl_config.password
        )
        home_dl: Client = home_dl_helper.client

        # Get all hashes in home downloader
        home_dl_hashes = {t.hash for t in home_dl.torrents_info()}
        
        # We also need seedbox completed list to know if we can start downloading
        # But this manager shouldn't directly access seedbox downloader if possible to keep decoupled.
        # However, the logic requires knowing if "BT seeds are in seedbox".
        # We can rely on `state.is_bt_in_seed_box` which is updated by SeedBoxManager.
        
        add_torrent_count = 0
        max_once_add = self.config.transfer.max_once_add
        
        all_transfers = self.state_manager.get_all()

        for info_hash, state in all_transfers.items():
            if add_torrent_count >= max_once_add:
                break
            
            # Scenario 1: Add BT torrent to home if it's on seedbox but not at home
            if (state.is_bt_in_seed_box and 
                state.bt_hash not in home_dl_hashes and 
                not state.is_torrent_in_home_dl):
                
                logger.info(f"Adding BT torrent to home downloader: {state.bt_hash}")
                if 'Ok.' in home_dl.torrents_add(
                    torrent_files=state.bt_torrent_file_path,
                    save_path=self.target_download_dir,
                    category=self.config.transfer.home_bt_category
                ):
                    state.is_bt_in_home_dl = True
                    self.state_manager.update(state)
                    add_torrent_count += 1
                continue

            # Scenario 2: BT torrent is at home, Origin not yet added -> add Origin
            if (state.bt_hash in home_dl_hashes and 
                state.hash not in home_dl_hashes and
                not state.is_torrent_in_home_dl):
                is_completed = self._check_bt_completed(home_dl, state.bt_hash)
                if is_completed:
                    logger.info(f"BT torrent completed at home. Adding Origin torrent: {state.hash}")
                    home_dl.torrents_add(
                        torrent_files=state.origin_torrent_file_path,
                        save_path=self.target_download_dir,
                        category=self.config.transfer.home_origin_temp_category,
                        is_skip_checking=True
                    )
                    # Do NOT set is_torrent_in_home_dl here - wait for Origin to fully complete
                continue
            
            # Scenario 3: Origin is at home (Temporary), BT is also at home -> verify Origin completed, then delete BT
            if state.hash in home_dl_hashes and state.bt_hash in home_dl_hashes:
                 if not self._is_torrent_completed(home_dl, state.hash):
                     # Origin not yet completed, wait
                     continue
                 logger.info(f"Origin completed and BT both found at home. Deleting BT: {state.bt_hash}")
                 home_dl.torrents_delete(torrent_hashes=state.bt_hash, delete_files=False)
                 
                 # Set category to final and mark as synced
                 home_dl.torrents_set_category(
                     category=self.config.transfer.home_origin_category, 
                     torrent_hashes=state.hash
                 )
                 state.is_torrent_in_home_dl = True
                 self.state_manager.update(state)
                 continue

            # Scenario 4: Origin at home, BT not at home
            if (state.hash in home_dl_hashes and 
                state.bt_hash not in home_dl_hashes and 
                not state.is_torrent_in_home_dl):
                
                # Only mark as synced after Origin is fully downloaded
                # Note: Origin is a PT torrent, do not add peers or modify category
                if self._is_torrent_completed(home_dl, state.hash):
                    state.is_torrent_in_home_dl = True
                    self.state_manager.update(state)

    def _is_torrent_completed(self, dl: Client, torrent_hash: str) -> bool:
        """Check if a torrent is fully downloaded (progress == 1)."""
        torrents = dl.torrents_info(torrent_hashes=torrent_hash)
        if not torrents:
            return False
        return torrents[0].progress == 1

    def _check_bt_completed(self, dl: Client, bt_hash: str) -> bool:
        """Check if BT torrent is completed and add seedbox peer if not."""
        torrents = dl.torrents_info(torrent_hashes=bt_hash)
        if not torrents:
            return False
        
        bt_torrent = torrents[0]
        if bt_torrent.progress == 1:
            return True
            
        # Add seedbox peer to help download
        dl.torrents_add_peers(torrent_hashes=bt_hash, peers=[
            f"{self.seed_box_config.ssh_host}:{self.seed_box_config.incoming_port}",
             f"[{self.seed_box_config.ipv6}]:{self.seed_box_config.incoming_port}"
        ])
        return False
