from __future__ import annotations

import logging
import os

from qbittorrentapi import Client

from managers.state_manager import StateManager
from transfer.torrent_transfer import (
    DEFAULT_RETRY_LIMIT,
    ORIGIN_DATA_STATUS_BLOCKED,
    ORIGIN_DATA_STATUS_MISSING_FILES,
    ORIGIN_DATA_STATUS_RECHECK_REQUESTED,
    ORIGIN_DATA_STATUS_WAITING_FOR_REDOWNLOAD,
    SEEDBOX_BT_HEALTH_MISSING_FILES,
    SEEDBOX_BT_HEALTH_MISSING_TORRENT,
)
from utils.config import Config, SeedBox, SeedboxOriginDataMissingPolicy
from utils.downloader_utils import DownloaderHelper, get_downloader_client
from utils.qbittorrent_snapshot import QbittorrentSnapshot

logger = logging.getLogger(__name__)


class HomeManager:
    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        seed_box_name: str,
        home_dl_name: str,
        target_download_dir: str,
        trigger_seedbox=None,
    ):
        self.config = config
        self.state_manager = state_manager
        self.seed_box_name = seed_box_name
        self.home_dl_name = home_dl_name
        self.target_download_dir = target_download_dir
        self.trigger_seedbox = trigger_seedbox
        self._init_configs()
        self.home_helper: DownloaderHelper = get_downloader_client(
            name=self.home_dl_config.name,
            url=self.home_dl_config.url,
            username=self.home_dl_config.username,
            password=self.home_dl_config.password,
        )
        self.home_snapshot = QbittorrentSnapshot(self.home_helper.client)

    def _init_configs(self):
        """Initialize configurations."""
        self.seed_box_config: SeedBox = next(filter(lambda x: x.name == self.seed_box_name, self.config.seed_box), None)
        if self.seed_box_config is None:
            raise ValueError(f"Seedbox config not found: {self.seed_box_name}")

        self.home_dl_config = next(filter(lambda x: x.name == self.home_dl_name, self.config.downloaders), None)
        if self.home_dl_config is None:
            raise ValueError(f"Home downloader config not found: {self.home_dl_name}")

    def _record_home_failure(self, state, error_message: str, skip_reason: str):
        attempts = state.record_failure(
            "home_add_retry_count",
            error_message,
            retry_limit=DEFAULT_RETRY_LIMIT,
            skip_reason=skip_reason,
        )
        if state.is_skipped:
            logger.warning(f"{skip_reason}: {state.hash}")
        else:
            logger.warning(f"{error_message}. Attempt {attempts}/{DEFAULT_RETRY_LIMIT}")
        self.state_manager.update(state)

    @staticmethod
    def _seedbox_source_unavailable(state) -> bool:
        return (
            state.seedbox_bt_health in {SEEDBOX_BT_HEALTH_MISSING_FILES, SEEDBOX_BT_HEALTH_MISSING_TORRENT}
            or state.seedbox_origin_data_status
            in {
                ORIGIN_DATA_STATUS_MISSING_FILES,
                ORIGIN_DATA_STATUS_BLOCKED,
                ORIGIN_DATA_STATUS_RECHECK_REQUESTED,
                ORIGIN_DATA_STATUS_WAITING_FOR_REDOWNLOAD,
            }
        )

    def _handle_unavailable_seedbox_source_for_home_bt(self, home_dl: Client, state) -> bool:
        policy = self.config.transfer.seedbox_origin_data_missing_policy

        if policy == SeedboxOriginDataMissingPolicy.skip_transfer:
            state.is_skipped = True
            state.skip_reason = "Seedbox source unavailable while home BT is incomplete"
            state.last_error = state.skip_reason
            self.state_manager.update(state)
            logger.warning(f"{state.skip_reason}: {state.hash}")
            return True

        if policy == SeedboxOriginDataMissingPolicy.force_recheck_and_rebuild_bt:
            if state.is_bt_in_home_dl:
                logger.warning(f"Deleting incomplete home BT without files before rebuild: {state.bt_hash}")
                home_dl.torrents_delete(torrent_hashes=state.bt_hash, delete_files=False)
            state.is_bt_in_home_dl = False
            state.last_error = "Seedbox source unavailable; home BT removed and waiting for rebuild"
            self.state_manager.update(state)
            return True

        state.last_error = "Seedbox source unavailable; home BT is blocked by policy"
        self.state_manager.update(state)
        logger.warning(f"Seedbox source unavailable, keeping home BT blocked: {state.hash}")
        return True

    def _start_home_bt_if_needed(self, home_dl: Client, bt_torrent):
        state = getattr(bt_torrent, "state", "")
        if state not in {"pausedDL", "stoppedDL"}:
            return

        torrent_hash = getattr(bt_torrent, "hash", "")
        if not torrent_hash:
            return

        logger.info(f"Starting paused home BT torrent: {torrent_hash}")
        if hasattr(home_dl, "torrents_start"):
            home_dl.torrents_start(torrent_hashes=torrent_hash)
        else:
            home_dl.torrents_resume(torrent_hashes=torrent_hash)

    def _ensure_home_category(self, home_dl: Client, category: str):
        if not category:
            return
        if not hasattr(home_dl, "torrents_create_category"):
            return
        try:
            home_dl.torrents_create_category(name=category, save_path=self.target_download_dir)
        except Exception as e:
            logger.debug(f"Home category may already exist or cannot be created: {category}: {e}")

    def run(self):
        """Run home management tasks."""
        try:
            self._process_home_torrents()
        except Exception as e:
            logger.error(f"Error in HomeManager: {e}")

    def _process_home_torrents(self):
        home_dl: Client = self.home_helper.client
        self.home_snapshot.refresh()

        # Get all hashes in home downloader
        home_dl_hashes = self.home_snapshot.hashes()

        # We also need seedbox completed list to know if we can start downloading
        # But this manager shouldn't directly access seedbox downloader if possible to keep decoupled.
        # However, the logic requires knowing if "BT seeds are in seedbox".
        # We can rely on `state.is_bt_in_seed_box` which is updated by SeedBoxManager.

        add_torrent_count = 0
        max_once_add = self.config.transfer.max_once_add

        all_transfers = self.state_manager.get_all()

        for info_hash, state in all_transfers.items():
            try:
                if add_torrent_count >= max_once_add:
                    break

                if state.is_skipped or not state.has_bt_torrent():
                    continue

                # Scenario 1: Add BT torrent to home if it's on seedbox but not at home
                if (
                    state.is_bt_in_seed_box
                    and state.bt_hash not in home_dl_hashes
                    and not state.is_bt_in_home_dl
                    and not state.is_torrent_in_home_dl
                ):
                    if not os.path.exists(state.bt_torrent_file_path):
                        self._record_home_failure(
                            state,
                            f"Local BT torrent file missing: {state.bt_torrent_file_path}",
                            "Local BT torrent file missing while adding to home downloader",
                        )
                        continue
                    logger.info(f"Adding BT torrent to home downloader: {state.bt_hash}")
                    result = home_dl.torrents_add(
                        torrent_files=state.bt_torrent_file_path,
                        save_path=self.target_download_dir,
                        category=self.config.transfer.home_bt_category,
                        is_paused=False,
                    )
                    if "Ok." in str(result):
                        state.is_bt_in_home_dl = True
                        state.reset_failures("home_add_retry_count")
                        self.state_manager.update(state)
                        add_torrent_count += 1
                    else:
                        self._record_home_failure(
                            state,
                            f"Failed to add BT torrent to home downloader: {state.bt_hash}",
                            "Repeatedly failed to add BT torrent to home downloader",
                        )
                    continue

                # Scenario 2: BT torrent is at home, Origin not yet added -> add Origin
                if (
                    state.bt_hash in home_dl_hashes
                    and state.hash not in home_dl_hashes
                    and not state.is_torrent_in_home_dl
                ):
                    bt_torrent = self.home_snapshot.torrent(state.bt_hash)
                    if bt_torrent is not None:
                        self._start_home_bt_if_needed(home_dl, bt_torrent)

                    is_completed = self._check_bt_completed(home_dl, state.bt_hash, self.home_snapshot)
                    if not is_completed and self._seedbox_source_unavailable(state):
                        self._handle_unavailable_seedbox_source_for_home_bt(home_dl, state)
                        continue

                    if is_completed:
                        if not os.path.exists(state.origin_torrent_file_path):
                            self._record_home_failure(
                                state,
                                f"Origin torrent file missing locally: {state.origin_torrent_file_path}",
                                "Origin torrent file missing while adding to home downloader",
                            )
                            continue
                        logger.info(f"BT torrent completed at home. Adding Origin torrent: {state.hash}")
                        result = home_dl.torrents_add(
                            torrent_files=state.origin_torrent_file_path,
                            save_path=self.target_download_dir,
                            category=self.config.transfer.home_origin_temp_category,
                            is_skip_checking=True,
                            is_paused=self.config.transfer.pause_after_add_origin,
                            tags=self.config.transfer.home_origin_tags
                            if self.config.transfer.home_origin_tags
                            else None,
                        )
                        if "Ok." in str(result):
                            state.reset_failures("home_add_retry_count")
                            self.state_manager.update(state)
                        else:
                            self._record_home_failure(
                                state,
                                f"Failed to add origin torrent to home downloader: {state.hash}",
                                "Repeatedly failed to add origin torrent to home downloader",
                            )
                        # Do NOT set is_torrent_in_home_dl here - wait for Origin to fully complete
                    continue

                # Scenario 3: Origin is at home (Temporary),
                # BT is also at home -> verify Origin completed, then delete BT
                if state.hash in home_dl_hashes and state.bt_hash in home_dl_hashes:
                    if not self._is_torrent_completed(home_dl, state.hash, self.home_snapshot):
                        # Origin not yet completed, wait
                        continue
                    logger.info(f"Origin completed and BT both found at home. Deleting BT: {state.bt_hash}")
                    home_dl.torrents_delete(torrent_hashes=state.bt_hash, delete_files=False)

                    # Set category to final and mark as synced
                    self._ensure_home_category(home_dl, self.config.transfer.home_origin_category)
                    home_dl.torrents_set_category(
                        category=self.config.transfer.home_origin_category,
                        torrent_hashes=state.hash,
                    )
                    state.is_torrent_in_home_dl = True
                    state.reset_failures("home_add_retry_count")
                    self.state_manager.update(state)

                    # Trigger SeedBoxManager so it can delete the seedbox copy immediately
                    if self.trigger_seedbox:
                        self.trigger_seedbox.set()
                    continue

                # Scenario 4: Origin at home, BT not at home
                if (
                    state.hash in home_dl_hashes
                    and state.bt_hash not in home_dl_hashes
                    and not state.is_torrent_in_home_dl
                ):
                    # Only mark as synced after Origin is fully downloaded
                    # Note: Origin is a PT torrent, do not add peers or modify category
                    if self._is_torrent_completed(home_dl, state.hash, self.home_snapshot):
                        state.is_torrent_in_home_dl = True
                        state.reset_failures("home_add_retry_count")
                        self.state_manager.update(state)

                        # Trigger SeedBoxManager so it can delete the seedbox copy immediately
                        if self.trigger_seedbox:
                            self.trigger_seedbox.set()
                    continue

                if (
                    state.is_bt_in_home_dl
                    and state.bt_hash not in home_dl_hashes
                    and not state.is_torrent_in_home_dl
                ):
                    logger.warning(f"Home BT torrent missing, resetting state so it can be re-added: {state.bt_hash}")
                    state.is_bt_in_home_dl = False
                    self.state_manager.update(state)
                    continue
            except Exception as e:
                if not state.is_torrent_in_home_dl:
                    self._record_home_failure(
                        state,
                        f"Error processing torrent {info_hash} in HomeManager: {e}",
                        "Repeated errors while processing home downloader transfer",
                    )
                else:
                    logger.error(f"Error processing torrent {info_hash} in HomeManager: {e}")

    def _is_torrent_completed(self, dl: Client, torrent_hash: str, snapshot: QbittorrentSnapshot | None = None) -> bool:
        """Check if a torrent is fully downloaded (progress == 1)."""
        if snapshot is not None:
            torrent = snapshot.torrent(torrent_hash)
            if torrent is not None:
                return getattr(torrent, "progress", 0) == 1

        torrents = dl.torrents_info(torrent_hashes=torrent_hash)
        if not torrents:
            return False
        return torrents[0].progress == 1

    def _check_bt_completed(self, dl: Client, bt_hash: str, snapshot: QbittorrentSnapshot | None = None) -> bool:
        """Check if BT torrent is completed and add seedbox peer if not."""
        if snapshot is not None:
            bt_torrent = snapshot.torrent(bt_hash)
            if bt_torrent is not None:
                if bt_torrent.progress == 1:
                    return True
        else:
            torrents = dl.torrents_info(torrent_hashes=bt_hash)
            if not torrents:
                return False

            bt_torrent = torrents[0]
            if bt_torrent.progress == 1:
                return True

            # Add seedbox peer to help download
            peers = [f"{self.seed_box_config.ssh_host}:{self.seed_box_config.incoming_port}"]
            if self.seed_box_config.ipv6:
                peers.append(f"[{self.seed_box_config.ipv6}]:{self.seed_box_config.incoming_port}")
            dl.torrents_add_peers(
                torrent_hashes=bt_hash,
                peers=peers,
            )
            return False

        # Add seedbox peer to help download
        peers = [f"{self.seed_box_config.ssh_host}:{self.seed_box_config.incoming_port}"]
        if self.seed_box_config.ipv6:
            peers.append(f"[{self.seed_box_config.ipv6}]:{self.seed_box_config.incoming_port}")
        dl.torrents_add_peers(
            torrent_hashes=bt_hash,
            peers=peers,
        )
        return False
