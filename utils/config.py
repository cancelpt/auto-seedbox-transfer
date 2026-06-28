from __future__ import annotations

import logging
from enum import Enum
from typing import List, Optional, Set, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


class SeedboxOriginDataMissingPolicy(str, Enum):
    pause_transfer = "pause_transfer"
    skip_transfer = "skip_transfer"
    force_recheck_and_rebuild_bt = "force_recheck_and_rebuild_bt"


class TransferDataPlaneMode(str, Enum):
    qb_bt = "qb_bt"
    direct_piece_pull = "direct_piece_pull"


class ProxyMode(str, Enum):
    direct = "direct"
    http = "http"
    http_connect = "http_connect"
    socks5 = "socks5"
    socks5h = "socks5h"


class ProxyEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    mode: ProxyMode = ProxyMode.direct
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None

    @model_validator(mode="after")
    def _validate_mode_requirements(self):
        if not self.enabled or self.mode == ProxyMode.direct:
            return self
        if not self.host:
            raise ValueError(f"host is required when proxy mode is '{self.mode.value}'")
        if self.port is None:
            raise ValueError(f"port is required when proxy mode is '{self.mode.value}'")
        return self


class NetworkProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    qbittorrent: Optional[ProxyEndpoint] = None
    sftp: Optional[ProxyEndpoint] = None

    @model_validator(mode="after")
    def _validate_supported_protocol_modes(self):
        if self.qbittorrent and self.qbittorrent.enabled and self.qbittorrent.mode == ProxyMode.http_connect:
            raise ValueError("qbittorrent proxy mode must be one of direct, http, socks5, or socks5h")
        if self.sftp and self.sftp.enabled and self.sftp.mode == ProxyMode.http:
            raise ValueError("sftp proxy mode must be one of direct, socks5, socks5h, or http_connect")
        return self


class Transfer(BaseModel):
    original_torrent_path: str
    bt_path: str
    torrent_info_path: str
    data_plane_mode: TransferDataPlaneMode = TransferDataPlaneMode.qb_bt
    direct_piece_workers: int = Field(default=4, gt=0)
    direct_piece_resume_path: Optional[str] = None
    bt_trackers: List[str]
    seedbox_origin_data_missing_policy: SeedboxOriginDataMissingPolicy
    seedbox_origin_recovery_max_rechecks: int = 1
    seedbox_origin_recovery_window_seconds: int = 600
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

    @model_validator(mode="after")
    def _validate_direct_piece_pull_requirements(self):
        if self.data_plane_mode != TransferDataPlaneMode.direct_piece_pull:
            return self
        if not self.direct_piece_resume_path or not self.direct_piece_resume_path.strip():
            raise ValueError("direct_piece_resume_path is required when data_plane_mode is 'direct_piece_pull'")
        return self


class SeedBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ssh_host: str
    ipv6: Optional[str] = None
    incoming_port: int
    ssh_port: int = 22
    ssh_user: str
    ssh_password: str
    torrents_path: str
    network_profile: Optional[str] = None


class Downloader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    username: str
    password: str
    source_categories: Optional[Union[str, List[str]]] = None
    want_torrent_category: Optional[Union[str, List[str]]] = None
    network_profile: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sync_source_category_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        source_categories = data.get("source_categories")
        want_torrent_category = data.get("want_torrent_category")
        if source_categories is None and want_torrent_category is not None:
            data["source_categories"] = want_torrent_category
        elif want_torrent_category is None and source_categories is not None:
            data["want_torrent_category"] = source_categories
        elif source_categories is not None and want_torrent_category is not None:
            source_set = cls._normalize_categories(source_categories)
            want_set = cls._normalize_categories(want_torrent_category)
            if source_set != want_set:
                raise ValueError("source_categories and want_torrent_category must match when both are configured")
        return data

    @staticmethod
    def _normalize_categories(categories: Optional[Union[str, List[str]]]) -> Set[str]:
        if categories is None:
            return set()
        if isinstance(categories, str):
            return {categories} if categories else set()
        return {category for category in categories if category}

    def get_source_categories(self) -> Set[str]:
        return self._normalize_categories(self.source_categories)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transfer: Transfer
    seed_box: List[SeedBox]
    downloaders: List[Downloader]
    network_profiles: List[NetworkProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_network_profiles(self):
        profiles_by_name = {}
        for profile in self.network_profiles:
            if profile.name in profiles_by_name:
                raise ValueError(f"Duplicate network profile name: {profile.name}")
            profiles_by_name[profile.name] = profile

        for downloader in self.downloaders:
            if downloader.network_profile and downloader.network_profile not in profiles_by_name:
                raise ValueError(
                    f"Downloader '{downloader.name}' references missing network_profile "
                    f"'{downloader.network_profile}'"
                )

        downloaders_by_name = {downloader.name: downloader for downloader in self.downloaders}
        for seed_box in self.seed_box:
            if seed_box.network_profile and seed_box.network_profile not in profiles_by_name:
                raise ValueError(
                    f"Seed box '{seed_box.name}' references missing network_profile '{seed_box.network_profile}'"
                )
            matched_downloader = downloaders_by_name.get(seed_box.name)
            if (
                matched_downloader
                and seed_box.network_profile
                and matched_downloader.network_profile
                and seed_box.network_profile != matched_downloader.network_profile
            ):
                logger.warning(
                    "seed_box '%s' and downloader '%s' use different network_profile values "
                    "(%s != %s); this is advanced usage.",
                    seed_box.name,
                    matched_downloader.name,
                    seed_box.network_profile,
                    matched_downloader.network_profile,
                )

        return self


class YAMLConfigHandler:
    @staticmethod
    def load(config_path: str) -> Config:
        with open(config_path, "r", encoding="utf-8") as file:
            config_data = yaml.safe_load(file)
            return Config(**config_data)


def get_downloader_config(config: Config, name: str) -> Downloader:
    downloader = next((item for item in config.downloaders if item.name == name), None)
    if downloader is None:
        raise ValueError(f"Downloader config not found: {name}")
    return downloader


def get_seed_box_config(config: Config, name: str) -> SeedBox:
    seed_box = next((item for item in config.seed_box if item.name == name), None)
    if seed_box is None:
        raise ValueError(f"Seedbox config not found: {name}")
    return seed_box


def get_network_profile(config: Config, name: Optional[str]) -> Optional[NetworkProfile]:
    if not name:
        return None
    network_profile = next((item for item in config.network_profiles if item.name == name), None)
    if network_profile is None:
        raise ValueError(f"Network profile not found: {name}")
    return network_profile


def resolve_downloader_network_profile(
    config: Config,
    downloader: Union[str, Downloader],
) -> Optional[NetworkProfile]:
    downloader_config = downloader if isinstance(downloader, Downloader) else get_downloader_config(config, downloader)
    return get_network_profile(config, downloader_config.network_profile)


def resolve_seedbox_network_profile(
    config: Config,
    seed_box: Union[str, SeedBox],
) -> Optional[NetworkProfile]:
    seed_box_config = seed_box if isinstance(seed_box, SeedBox) else get_seed_box_config(config, seed_box)
    return get_network_profile(config, seed_box_config.network_profile)


def validate_route_config(config: Config, seed_box_name: str, home_dl_name: str) -> Tuple[Downloader, Downloader]:
    seedbox_downloader = get_downloader_config(config, seed_box_name)
    home_downloader = get_downloader_config(config, home_dl_name)

    if not seedbox_downloader.get_source_categories():
        raise ValueError(
            f"Seedbox downloader '{seed_box_name}' must configure source_categories "
            "(legacy name: want_torrent_category)."
        )

    if home_downloader.get_source_categories():
        logger.warning(
            "home downloader '%s' has source_categories configured, but this route reads source categories from "
            "seedbox downloader '%s'",
            home_dl_name,
            seed_box_name,
        )

    return seedbox_downloader, home_downloader
