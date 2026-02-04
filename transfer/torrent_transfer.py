from pydantic import BaseModel


class TorrentTransfer(BaseModel):
    hash: str
    bt_hash: str
    origin_torrent_file_path: str
    bt_torrent_file_path: str
    is_bt_in_seed_box: bool = False
    is_bt_in_home_dl: bool = False
    is_torrent_in_home_dl: bool = False
