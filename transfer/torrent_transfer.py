from pydantic import BaseModel

DEFAULT_RETRY_LIMIT = 3

SEEDBOX_BT_HEALTH_UNKNOWN = "unknown"
SEEDBOX_BT_HEALTH_READY = "ready"
SEEDBOX_BT_HEALTH_MISSING_TORRENT = "missing_torrent"
SEEDBOX_BT_HEALTH_MISSING_FILES = "missing_files"

ORIGIN_DATA_STATUS_OK = "ok"
ORIGIN_DATA_STATUS_MISSING_FILES = "missing_files"
ORIGIN_DATA_STATUS_BLOCKED = "blocked"
ORIGIN_DATA_STATUS_RECHECK_REQUESTED = "recheck_requested"
ORIGIN_DATA_STATUS_WAITING_FOR_REDOWNLOAD = "waiting_for_redownload"


class TorrentTransfer(BaseModel):
    hash: str
    bt_hash: str = ""
    origin_torrent_file_path: str
    bt_torrent_file_path: str = ""
    is_bt_in_seed_box: bool = False
    is_bt_in_home_dl: bool = False
    is_torrent_in_home_dl: bool = False
    seedbox_bt_health: str = SEEDBOX_BT_HEALTH_UNKNOWN
    seedbox_origin_data_status: str = ORIGIN_DATA_STATUS_OK
    seedbox_origin_data_recheck_count: int = 0
    download_retry_count: int = 0
    seedbox_add_retry_count: int = 0
    home_add_retry_count: int = 0
    missing_origin_retry_count: int = 0
    is_skipped: bool = False
    skip_reason: str = ""
    last_error: str = ""

    def has_bt_torrent(self) -> bool:
        return bool(self.bt_hash and self.bt_torrent_file_path)

    def record_failure(
        self,
        counter_field: str,
        error_message: str,
        retry_limit: int = DEFAULT_RETRY_LIMIT,
        skip_reason: str = "",
    ) -> int:
        attempts = getattr(self, counter_field) + 1
        setattr(self, counter_field, attempts)
        self.last_error = error_message
        if attempts >= retry_limit:
            self.is_skipped = True
            self.skip_reason = skip_reason or error_message
        return attempts

    def reset_failures(self, *counter_fields: str):
        for counter_field in counter_fields:
            setattr(self, counter_field, 0)
        if all(
            getattr(self, counter_name) == 0
            for counter_name in (
                "download_retry_count",
                "seedbox_add_retry_count",
                "home_add_retry_count",
                "missing_origin_retry_count",
            )
        ):
            self.last_error = ""
