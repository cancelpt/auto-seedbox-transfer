from typing import List

import yaml
from pydantic import BaseModel


class Transfer(BaseModel):
    original_torrent_path: str
    bt_path: str
    torrent_info_path: str
    bt_trackers: List[str]
    max_once_add: int = 5


class SeedBox(BaseModel):
    name: str
    ssh_host: str
    ipv6: str | None = None
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
    want_torrent_category: str | None = None


class Config(BaseModel):
    transfer: Transfer
    seed_box: List[SeedBox]
    downloaders: List[Downloader]


class YAMLConfigHandler:
    @staticmethod
    def load(config_path: str) -> Config:
        with open(config_path, 'r', encoding='utf-8') as file:
            config_data = yaml.safe_load(file)
            return Config(**config_data)
