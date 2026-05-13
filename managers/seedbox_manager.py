from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

from qbittorrentapi import Client, TorrentInfoList

from managers.state_manager import StateManager
from transfer.torrent_transfer import (
    DEFAULT_RETRY_LIMIT,
    ORIGIN_DATA_STATUS_BLOCKED,
    ORIGIN_DATA_STATUS_MISSING_FILES,
    ORIGIN_DATA_STATUS_OK,
    ORIGIN_DATA_STATUS_RECHECK_REQUESTED,
    ORIGIN_DATA_STATUS_WAITING_FOR_REDOWNLOAD,
    SEEDBOX_BT_HEALTH_MISSING_FILES,
    SEEDBOX_BT_HEALTH_MISSING_TORRENT,
    SEEDBOX_BT_HEALTH_READY,
    TorrentTransfer,
)
from utils.config import Config, SeedboxOriginDataMissingPolicy
from utils.downloader_utils import DownloaderHelper, get_downloader_client
from utils.qbittorrent_snapshot import QbittorrentSnapshot
from utils.sftp_utils import SFTPClient
from utils.torrent_utils import TorrentFile

logger = logging.getLogger(__name__)


class SeedBoxManager:
    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        seed_box_name: str,
        home_dl_name: str,
        shutdown_event: threading.Event,
        trigger_local=None,
        trigger_home=None,
        async_downloads=True,
    ):
        self.config = config
        self.state_manager = state_manager
        self.seed_box_name = seed_box_name
        self.home_dl_name = home_dl_name
        self.shutdown_event = shutdown_event
        self.trigger_local = trigger_local
        self.trigger_home = trigger_home
        self.failed_counts = {}
        self._is_downloading = False
        self._download_lock = threading.Lock()
        self.async_downloads = async_downloads
        self._init_configs()
        self.seed_box_helper: DownloaderHelper = get_downloader_client(
            name=self.seed_box_dl_config.name,
            url=self.seed_box_dl_config.url,
            username=self.seed_box_dl_config.username,
            password=self.seed_box_dl_config.password,
        )
        self.seed_box_snapshot = QbittorrentSnapshot(self.seed_box_helper.client)

    def _init_configs(self):
        """Initialize configurations for seedbox and downloaders."""
        self.seed_box_config = next(filter(lambda x: x.name == self.seed_box_name, self.config.seed_box), None)
        if self.seed_box_config is None:
            raise ValueError(f"Seedbox config not found: {self.seed_box_name}")

        self.seed_box_dl_config = next(
            filter(lambda x: x.name == self.seed_box_name, self.config.downloaders),
            None,
        )
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

    def _local_torrent_path(self, torrent_hash: str) -> str:
        return os.path.join(self.config.transfer.original_torrent_path, f"{torrent_hash}.torrent")

    def _get_or_create_transfer(self, torrent_hash: str) -> TorrentTransfer:
        state = self.state_manager.get(torrent_hash)
        if state:
            return state

        state = TorrentTransfer(
            hash=torrent_hash,
            origin_torrent_file_path=self._local_torrent_path(torrent_hash),
        )
        self.state_manager.update(state)
        return state

    def _record_transfer_failure(self, state: TorrentTransfer, counter_field: str, error_message: str, skip_reason: str):
        attempts = state.record_failure(
            counter_field,
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
    def _is_missing_files(torrent) -> bool:
        return getattr(torrent, "state", "") == "missingFiles"

    def _apply_origin_data_missing_policy(
        self,
        state: TorrentTransfer,
        seed_box_dl: Client,
        origin_torrent=None,
        bt_torrent=None,
        reason: str = "",
    ) -> bool:
        policy = self.config.transfer.seedbox_origin_data_missing_policy
        updated = False

        if state.seedbox_origin_data_status not in {
            ORIGIN_DATA_STATUS_RECHECK_REQUESTED,
            ORIGIN_DATA_STATUS_WAITING_FOR_REDOWNLOAD,
        }:
            state.seedbox_origin_data_status = ORIGIN_DATA_STATUS_MISSING_FILES
        state.last_error = reason or "Seedbox origin data is missing"
        updated = True

        if policy == SeedboxOriginDataMissingPolicy.skip_transfer:
            state.is_skipped = True
            state.skip_reason = reason or "Seedbox origin data is missing"
            logger.warning(f"Skipping transfer because seedbox origin data is missing: {state.hash}")
            return updated

        if policy == SeedboxOriginDataMissingPolicy.force_recheck_and_rebuild_bt:
            if bt_torrent is not None and state.bt_hash and state.seedbox_origin_data_recheck_count == 0:
                logger.warning(f"Deleting unusable BT torrent from seedbox without files: {state.bt_hash}")
                seed_box_dl.torrents_delete(torrent_hashes=state.bt_hash, delete_files=False)
                state.is_bt_in_seed_box = False
                state.seedbox_bt_health = SEEDBOX_BT_HEALTH_MISSING_FILES
                updated = True

            if origin_torrent is not None and state.seedbox_origin_data_recheck_count == 0:
                logger.warning(f"Requesting seedbox origin torrent recheck: {state.hash}")
                seed_box_dl.torrents_recheck(torrent_hashes=state.hash)
                state.seedbox_origin_data_recheck_count += 1
                state.seedbox_origin_data_status = ORIGIN_DATA_STATUS_RECHECK_REQUESTED
                updated = True
            return updated

        state.seedbox_origin_data_status = ORIGIN_DATA_STATUS_BLOCKED
        logger.warning(f"Seedbox origin data missing, transfer blocked by policy: {state.hash}")
        return updated

    def _mark_origin_data_healthy(self, state: TorrentTransfer) -> bool:
        updated = False
        if state.seedbox_origin_data_status != ORIGIN_DATA_STATUS_OK:
            state.seedbox_origin_data_status = ORIGIN_DATA_STATUS_OK
            updated = True
        if state.seedbox_origin_data_recheck_count:
            state.seedbox_origin_data_recheck_count = 0
            updated = True
        return updated

    def _sync_existing_transfer_state(self, seed_box_torrent_hashes: set[str], seed_box_dl: Client):
        for info_hash, state in self.state_manager.get_all().items():
            if state.is_skipped or state.is_torrent_in_home_dl:
                continue

            updated = False
            origin_torrent = self.seed_box_snapshot.torrent(state.hash)
            bt_torrent = self.seed_box_snapshot.torrent(state.bt_hash) if state.bt_hash else None
            source_missing_detected = False

            if state.bt_hash and state.bt_hash in seed_box_torrent_hashes and bt_torrent is not None:
                if self._is_missing_files(bt_torrent):
                    logger.warning(f"BT torrent has missing files on seedbox, resetting state: {state.bt_hash}")
                    state.is_bt_in_seed_box = False
                    state.seedbox_bt_health = SEEDBOX_BT_HEALTH_MISSING_FILES
                    source_missing_detected = True
                    updated = True
                    updated |= self._apply_origin_data_missing_policy(
                        state,
                        seed_box_dl,
                        origin_torrent=origin_torrent,
                        bt_torrent=bt_torrent,
                        reason="Seedbox BT torrent reports missingFiles",
                    )
                elif state.is_bt_in_seed_box:
                    if state.seedbox_bt_health != SEEDBOX_BT_HEALTH_READY:
                        state.seedbox_bt_health = SEEDBOX_BT_HEALTH_READY
                        updated = True
            elif state.bt_hash and state.is_bt_in_seed_box and state.bt_hash not in seed_box_torrent_hashes:
                logger.warning(f"BT torrent missing from seedbox, resetting state: {state.bt_hash}")
                state.is_bt_in_seed_box = False
                state.seedbox_bt_health = SEEDBOX_BT_HEALTH_MISSING_TORRENT
                updated = True

            if origin_torrent is not None and self._is_missing_files(origin_torrent):
                source_missing_detected = True
                updated |= self._apply_origin_data_missing_policy(
                    state,
                    seed_box_dl,
                    origin_torrent=origin_torrent,
                    bt_torrent=bt_torrent,
                    reason="Seedbox origin torrent reports missingFiles",
                )
                self.state_manager.update(state)
                continue

            origin_available = state.hash in seed_box_torrent_hashes or os.path.exists(state.origin_torrent_file_path)
            if origin_available:
                if state.missing_origin_retry_count:
                    state.missing_origin_retry_count = 0
                    updated = True
                if origin_torrent is not None and not source_missing_detected:
                    updated |= self._mark_origin_data_healthy(state)
            else:
                attempts = state.record_failure(
                    "missing_origin_retry_count",
                    f"Origin torrent missing from seedbox while transfer is incomplete: {info_hash}",
                    retry_limit=DEFAULT_RETRY_LIMIT,
                    skip_reason="Origin torrent disappeared from seedbox before transfer finished",
                )
                updated = True
                if state.is_skipped:
                    logger.warning(f"Skipping transfer after missing-origin retries: {info_hash}")
                else:
                    logger.warning(
                        f"Origin torrent missing from seedbox while transfer is incomplete: {info_hash}. "
                        f"Attempt {attempts}/{DEFAULT_RETRY_LIMIT}"
                    )

            if updated:
                self.state_manager.update(state)

    def _process_seedbox_torrents(self):
        seed_box_dl: Client = self.seed_box_helper.client

        # Refresh the cached snapshot once per run. It will use sync/maindata when available.
        self.seed_box_snapshot.refresh()
        all_torrents: TorrentInfoList = self.seed_box_snapshot.torrents()
        seed_box_torrent_hashes = self.seed_box_snapshot.hashes()
        self._sync_existing_transfer_state(seed_box_torrent_hashes, seed_box_dl)

        torrents = [torrent for torrent in all_torrents if getattr(torrent, "progress", 0) == 1]

        # Determine the set of managed categories for this run
        want_cat = self.seed_box_dl_config.want_torrent_category
        managed_want_categories = {want_cat} if isinstance(want_cat, str) else set(want_cat or [])

        # Filter torrents by category first to check if we are truly "done" for these category
        torrents = [t for t in torrents if t.category in managed_want_categories]

        # Filter by completion time if requested
        if self.config.transfer.seed_box_ignore_complete_time > 0:
            current_time = time.time()
            threshold = self.config.transfer.seed_box_ignore_complete_time
            torrents = [t for t in torrents if (current_time - t.completion_on) >= threshold]

        def check_exit_on_finish():
            if self.config.transfer.exit_on_finish:
                all_states = self.state_manager.get_all()
                skipped_origin_hashes = {info_hash for info_hash, state in all_states.items() if state.is_skipped}
                skipped_bt_hashes = {state.bt_hash for state in all_states.values() if state.is_skipped and state.bt_hash}

                # Check all managed origin categories and the BT category
                managed_categories = list(managed_want_categories) + [
                    self.config.transfer.seed_box_bt_category,
                ]
                for cat in managed_categories:
                    cat_torrents = [t for t in all_torrents if t.category == cat]
                    if not cat_torrents:
                        continue

                    # If this is an origin category and we have an ignore threshold
                    if cat in managed_want_categories and self.config.transfer.seed_box_ignore_complete_time > 0:
                        current_time = time.time()
                        threshold = self.config.transfer.seed_box_ignore_complete_time
                        # Filter to see if there are any "matured" torrents
                        eligible = [
                            t
                            for t in cat_torrents
                            if t.hash not in skipped_origin_hashes and (current_time - t.completion_on) >= threshold
                        ]
                        if eligible:
                            return False
                    elif cat in managed_want_categories:
                        active = [t for t in cat_torrents if t.hash not in skipped_origin_hashes]
                        if active:
                            return False
                    elif cat == self.config.transfer.seed_box_bt_category:
                        active = [t for t in cat_torrents if t.hash not in skipped_bt_hashes]
                        if active:
                            logger.info(f"Found torrent in category {cat}: {active[0].name}")
                            return False
                    else:
                        logger.info(f"Found torrent in category {cat}: {cat_torrents[0].name}")
                        # For other categories (like BT temporary), any torrent prevents exit
                        return False

                logger.info(
                    "No eligible torrents left in managed categories. Exit on finish is enabled. Shutting down..."
                )
                self.shutdown_event.set()
                return True
            return False

        if not torrents:
            if check_exit_on_finish():
                return
            # If not exiting, but no origin torrents to process, we still return from this method
            return

        add_torrent_count = 0
        max_once_add = self.config.transfer.max_once_add

        # Collect torrents that need downloading (hash -> trackers list)
        torrents_to_download = {}
        if self.config.transfer.auto_dl_torrent_from_seedbox:
            for torrent in torrents:
                state = self.state_manager.get(torrent.hash)
                if state and state.is_skipped:
                    continue

                # Check if exists in local state
                if state and state.has_bt_torrent():
                    continue

                # Check if exists locally (avoid double download if LocalManager hasn't scanned yet)
                local_path = self._local_torrent_path(torrent.hash)
                if os.path.exists(local_path):
                    continue

                self._get_or_create_transfer(torrent.hash)

                # Get trackers for this torrent
                try:
                    trackers_info = self.seed_box_snapshot.get_trackers(torrent.hash)
                    trackers_urls = [t.url for t in trackers_info if re.match(r"^(udp|http|https)://", t.url)]
                    torrents_to_download[torrent.hash] = trackers_urls
                except Exception as e:
                    logger.warning(f"Failed to get trackers for {torrent.hash}: {e}")
                    # Still try to download without trackers if fetch fails
                    torrents_to_download[torrent.hash] = []

        # Batch download if needed
        if torrents_to_download:
            if self.async_downloads:
                download_thread = threading.Thread(
                    target=self._batch_download_torrents_from_seedbox,
                    args=(torrents_to_download,),
                    daemon=True,
                )
                download_thread.start()
            else:
                self._batch_download_torrents_from_seedbox(torrents_to_download)

        for torrent in torrents:
            state = None
            try:
                if add_torrent_count >= max_once_add:
                    logger.info(f"Seedbox max add limit reached ({max_once_add})")
                    break

                # Check failure count
                if self.failed_counts.get(torrent.hash, 0) >= 3:
                    continue

                # Check if exists in local state
                state = self.state_manager.get(torrent.hash)
                if not state or state.is_skipped or not state.has_bt_torrent():
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
                        if self.config.transfer.seed_box_keep_torrent:
                            logger.info(
                                f"Keeping Origin torrent on seedbox, changing category to \
                                    '{self.config.transfer.seed_box_keep_torrent_category}': {state.hash}"
                            )
                            seed_box_dl.torrents_set_category(
                                category=self.config.transfer.seed_box_keep_torrent_category,
                                torrent_hashes=state.hash,
                            )
                        else:
                            logger.info(f"Deleting completed Origin torrent from seedbox: {state.hash}")
                            seed_box_dl.torrents_delete(torrent_hashes=state.hash, delete_files=True)
                    continue

                # Logic: Add BT torrent to seedbox if not present
                if state.bt_hash in seed_box_torrent_hashes:
                    existing_bt_torrent = self.seed_box_snapshot.torrent(state.bt_hash)
                    if existing_bt_torrent is not None and self._is_missing_files(existing_bt_torrent):
                        state.is_bt_in_seed_box = False
                        state.seedbox_bt_health = SEEDBOX_BT_HEALTH_MISSING_FILES
                        self._apply_origin_data_missing_policy(
                            state,
                            seed_box_dl,
                            origin_torrent=torrent,
                            bt_torrent=existing_bt_torrent,
                            reason="Seedbox BT torrent reports missingFiles",
                        )
                        self.state_manager.update(state)
                    elif not state.is_bt_in_seed_box:
                        logger.info(f"BT torrent found on seedbox, updating state: {state.bt_hash}")
                        state.is_bt_in_seed_box = True
                        state.seedbox_bt_health = SEEDBOX_BT_HEALTH_READY
                        state.reset_failures("seedbox_add_retry_count")
                        if state.hash in seed_box_torrent_hashes or os.path.exists(state.origin_torrent_file_path):
                            state.reset_failures("missing_origin_retry_count")
                            self._mark_origin_data_healthy(state)
                        self.state_manager.update(state)
                    continue

                if self._is_missing_files(torrent):
                    state.is_bt_in_seed_box = False
                    state.seedbox_bt_health = SEEDBOX_BT_HEALTH_MISSING_TORRENT
                    self._apply_origin_data_missing_policy(
                        state,
                        seed_box_dl,
                        origin_torrent=torrent,
                        reason="Seedbox origin torrent reports missingFiles",
                    )
                    self.state_manager.update(state)
                    continue

                if not os.path.exists(state.bt_torrent_file_path):
                    self._record_transfer_failure(
                        state,
                        "seedbox_add_retry_count",
                        f"Local BT torrent file missing: {state.bt_torrent_file_path}",
                        "Local BT torrent file missing while adding to seedbox",
                    )
                    continue

                # Add BT torrent
                logger.info(f"Adding BT torrent to seedbox: {torrent.name}, {torrent.save_path}")
                result = seed_box_dl.torrents_add(
                    torrent_files=state.bt_torrent_file_path,
                    category=self.config.transfer.seed_box_bt_category,
                    is_skip_checking=True,
                    save_path=torrent.save_path,
                )
                if "Ok." in str(result):
                    self.seed_box_snapshot.refresh()
                    added_bt_torrent = self.seed_box_snapshot.torrent(state.bt_hash)
                    if added_bt_torrent is None:
                        logger.warning(f"BT torrent add returned success but is not visible yet: {state.bt_hash}")
                        state.is_bt_in_seed_box = False
                        state.seedbox_bt_health = SEEDBOX_BT_HEALTH_MISSING_TORRENT
                        self.state_manager.update(state)
                        continue

                    if self._is_missing_files(added_bt_torrent):
                        logger.warning(f"BT torrent add returned success but reports missingFiles: {state.bt_hash}")
                        state.is_bt_in_seed_box = False
                        state.seedbox_bt_health = SEEDBOX_BT_HEALTH_MISSING_FILES
                        self._apply_origin_data_missing_policy(
                            state,
                            seed_box_dl,
                            origin_torrent=torrent,
                            bt_torrent=added_bt_torrent,
                            reason="Seedbox BT torrent is unusable after add",
                        )
                        self.state_manager.update(state)
                        continue

                    logger.info(f"Successfully added BT torrent: {state.bt_hash}")
                    state.is_bt_in_seed_box = True
                    state.seedbox_bt_health = SEEDBOX_BT_HEALTH_READY
                    state.reset_failures("seedbox_add_retry_count")
                    if state.hash in seed_box_torrent_hashes or os.path.exists(state.origin_torrent_file_path):
                        state.reset_failures("missing_origin_retry_count")
                        self._mark_origin_data_healthy(state)
                    self.state_manager.update(state)
                    add_torrent_count += 1

                    # Reset failure count on success
                    if torrent.hash in self.failed_counts:
                        del self.failed_counts[torrent.hash]

                    # Trigger home manager to check for this new torrent
                    if self.trigger_home:
                        self.trigger_home.set()
                else:
                    self._record_transfer_failure(
                        state,
                        "seedbox_add_retry_count",
                        f"Failed to add BT torrent to seedbox: {state.bt_hash}",
                        "Repeatedly failed to add BT torrent to seedbox",
                    )
            except Exception as e:
                if state and not state.is_torrent_in_home_dl:
                    self._record_transfer_failure(
                        state,
                        "seedbox_add_retry_count",
                        f"Error processing torrent {torrent.hash} ({torrent.name}) in SeedBoxManager: {e}",
                        "Repeated errors while processing seedbox transfer",
                    )
                else:
                    logger.error(f"Error processing torrent {torrent.hash} ({torrent.name}) in SeedBoxManager: {e}")

        # After processing all torrents, check if we should exit on finish
        check_exit_on_finish()

    def _batch_download_torrents_from_seedbox(self, torrents_map: dict):
        """Batch download torrent files from seedbox via SFTP."""
        if not torrents_map:
            return

        with self._download_lock:
            if self._is_downloading:
                logger.info("A batch download is already in progress, skipping this run.")
                return
            self._is_downloading = True
        try:
            logger.info(f"Starting batch download for {len(torrents_map)} torrents from seedbox...")
            sftp_client = None
            try:
                sftp_client = SFTPClient(
                    hostname=self.seed_box_config.ssh_host,
                    username=self.seed_box_config.ssh_user,
                    password=self.seed_box_config.ssh_password,
                    port=self.seed_box_config.ssh_port,
                )
                max_retries = 3
                for attempt in range(1, max_retries + 1):
                    try:
                        sftp_client.connect()
                        break
                    except Exception as e:
                        if attempt == max_retries:
                            logger.error(f"SFTP connection failed after {max_retries} attempts, giving up.")
                            raise
                        wait = 5 * (2 ** (attempt - 1))
                        logger.warning(
                            f"SFTP connection attempt {attempt}/{max_retries} failed: {e}. Retrying in {wait}s..."
                        )
                        time.sleep(wait)

                for torrent_hash, trackers in torrents_map.items():
                    temp_local_path = None
                    state = self._get_or_create_transfer(torrent_hash)
                    try:
                        remote_path = Path(self.seed_box_config.torrents_path) / f"{torrent_hash}.torrent"
                        final_local_path = self._local_torrent_path(torrent_hash)
                        temp_local_path = os.path.join(
                            self.config.transfer.original_torrent_path,
                            f"{torrent_hash}.torrent.tmp",
                        )

                        if os.path.exists(final_local_path):
                            state.origin_torrent_file_path = final_local_path
                            state.reset_failures("download_retry_count", "missing_origin_retry_count")
                            self.state_manager.update(state)
                            continue

                        logger.info(f"Downloading torrent {torrent_hash} from seedbox...")
                        sftp_client.download(remote_path.as_posix(), temp_local_path)

                        # Inject trackers
                        if os.path.exists(temp_local_path):
                            if trackers:
                                logger.info(f"Injecting {len(trackers)} trackers into {torrent_hash}")
                                try:
                                    t_file = TorrentFile(temp_local_path)
                                    t_file.add_trackers(trackers)
                                    t_file.save(temp_local_path)
                                except Exception as e:
                                    logger.error(f"Failed to inject trackers for {torrent_hash}: {e}")

                            # Rename to final name
                            os.replace(temp_local_path, final_local_path)
                            state.origin_torrent_file_path = final_local_path
                            state.reset_failures("download_retry_count", "missing_origin_retry_count")
                            self.state_manager.update(state)
                            logger.info(f"Successfully downloaded and processed: {final_local_path}")
                        else:
                            self._record_transfer_failure(
                                state,
                                "download_retry_count",
                                f"Downloaded torrent file missing after transfer: {torrent_hash}",
                                "Seedbox origin torrent file disappeared before it could be downloaded",
                            )
                    except FileNotFoundError as e:
                        self._record_transfer_failure(
                            state,
                            "download_retry_count",
                            f"Seedbox torrent file missing for {torrent_hash}: {e}",
                            "Seedbox origin torrent file disappeared before it could be downloaded",
                        )
                    except Exception as e:
                        logger.error(f"Failed to download/process torrent {torrent_hash}: {e}")
                        self._record_transfer_failure(
                            state,
                            "download_retry_count",
                            f"Failed to download/process torrent {torrent_hash}: {e}",
                            "Repeatedly failed to download origin torrent file from seedbox",
                        )
                        # Cleanup temp file if exists
                        if temp_local_path and os.path.exists(temp_local_path):
                            try:
                                os.remove(temp_local_path)
                            except Exception as e:
                                logger.error(f"Failed to remove temp file {temp_local_path}: {e}")

                # After finishing a batch, trigger LocalManager to scan
                if self.trigger_local:
                    self.trigger_local.set()

            except Exception as e:
                logger.error(f"SFTP connection error: {e}")
                for torrent_hash in torrents_map:
                    state = self._get_or_create_transfer(torrent_hash)
                    self._record_transfer_failure(
                        state,
                        "download_retry_count",
                        f"SFTP connection error for {torrent_hash}: {e}",
                        "Repeatedly failed to download origin torrent file from seedbox",
                    )
            finally:
                if sftp_client:
                    sftp_client.close()
        finally:
            with self._download_lock:
                self._is_downloading = False
