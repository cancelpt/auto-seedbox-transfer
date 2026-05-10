from __future__ import annotations

from typing import List, Optional, Union

import yaml
from pydantic import BaseModel


class Transfer(BaseModel):
    original_torrent_path: str
    bt_path: str
    torrent_info_path: str
    bt_trackers: List[str]
    max_once_add: int = 5
    seed_box_bt_category: str = "keep"
    seed_box_ignore_complete_time: int = 0
    seed_box_keep_torrent: bool = False
    seed_box_keep_torrent_category: str = ""
    home_bt_category: str = "BT"
    home_origin_temp_category: str = "ORIGIN_TEMP"
    home_origin_category: str = "ORIGIN"
    pause_after_add_origin: bool = False
    home_origin_tags: str = ""
    local_interval: int = 30
    seedbox_interval: int = 60
    home_interval: int = 30
    auto_dl_torrent_from_seedbox: bool = False
    exit_on_finish: bool = False


class SeedBox(BaseModel):
    name: str
    ssh_host: str
    ipv6: Optional[str] = None
    incoming_port: int
    ssh_port: int = 22
    ssh_user: str
    ssh_password: str
    torrents_path: str


class Downloader(BaseModel):
    name: str
    url: str
    username: str
    password: str
    want_torrent_category: Optional[Union[str, List[str]]] = None


class Config(BaseModel):
    transfer: Transfer
    seed_box: List[SeedBox]
    downloaders: List[Downloader]


class YAMLConfigHandler:
    @staticmethod
    def load(config_path: str) -> Config:
        with open(config_path, "r", encoding="utf-8") as file:
            config_data = yaml.safe_load(file)
            return Config(**config_data)
