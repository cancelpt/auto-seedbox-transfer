import logging
import re
from pathlib import Path

import pytest

from utils.config import Config


def make_config_dict():
    return {
        "transfer": {
            "original_torrent_path": "/tmp/original",
            "bt_path": "/tmp/bt",
            "torrent_info_path": "/tmp/state.json",
            "bt_trackers": [],
            "seedbox_origin_data_missing_policy": "pause_transfer",
        },
        "seed_box": [
            {
                "name": "seedbox",
                "ssh_host": "seed.example",
                "incoming_port": 60000,
                "ssh_user": "user",
                "ssh_password": "pass",
                "torrents_path": "/remote/torrents",
            }
        ],
        "downloaders": [
            {
                "name": "seedbox",
                "url": "http://seedbox:8080",
                "username": "user",
                "password": "pass",
                "source_categories": ["To"],
            },
            {
                "name": "home",
                "url": "http://home:8080",
                "username": "user",
                "password": "pass",
            },
        ],
    }


def test_config_without_network_profiles_remains_compatible():
    config = Config(**make_config_dict())

    assert config.transfer.data_plane_mode == "qb_bt"
    assert config.transfer.direct_piece_workers == 4
    assert config.transfer.direct_piece_resume_path is None
    assert config.downloaders[0].network_profile is None
    assert config.seed_box[0].network_profile is None


def test_direct_piece_pull_mode_requires_resume_path():
    config_data = make_config_dict()
    config_data["transfer"]["data_plane_mode"] = "direct_piece_pull"

    with pytest.raises(ValueError, match="direct_piece_resume_path"):
        Config(**config_data)


def test_direct_piece_pull_mode_accepts_positive_worker_count_with_resume_path():
    config_data = make_config_dict()
    config_data["transfer"]["data_plane_mode"] = "direct_piece_pull"
    config_data["transfer"]["direct_piece_workers"] = 8
    config_data["transfer"]["direct_piece_resume_path"] = "/tmp/direct-piece-resume"

    config = Config(**config_data)

    assert config.transfer.data_plane_mode == "direct_piece_pull"
    assert config.transfer.direct_piece_workers == 8
    assert config.transfer.direct_piece_resume_path == "/tmp/direct-piece-resume"


def test_direct_piece_pull_mode_rejects_non_positive_worker_count():
    config_data = make_config_dict()
    config_data["transfer"]["data_plane_mode"] = "direct_piece_pull"
    config_data["transfer"]["direct_piece_workers"] = 0
    config_data["transfer"]["direct_piece_resume_path"] = "/tmp/direct-piece-resume"

    with pytest.raises(ValueError, match="direct_piece_workers"):
        Config(**config_data)


def test_network_profiles_are_loaded_and_resolved():
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "nc-via-mihomo",
            "qbittorrent": {
                "mode": "socks5h",
                "host": "172.31.0.1",
                "port": 7891,
            },
            "sftp": {
                "mode": "socks5",
                "host": "172.31.0.1",
                "port": 7891,
            },
        }
    ]
    config_data["downloaders"][0]["network_profile"] = "nc-via-mihomo"
    config_data["seed_box"][0]["network_profile"] = "nc-via-mihomo"

    config = Config(**config_data)

    assert config.network_profiles[0].name == "nc-via-mihomo"
    assert config.network_profiles[0].qbittorrent.mode == "socks5h"
    assert config.network_profiles[0].sftp.mode == "socks5"


def test_direct_network_profile_does_not_require_host_or_port():
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "direct-only",
            "qbittorrent": {"mode": "direct"},
            "sftp": {"mode": "direct"},
        }
    ]
    config_data["downloaders"][0]["network_profile"] = "direct-only"
    config_data["seed_box"][0]["network_profile"] = "direct-only"

    config = Config(**config_data)

    assert config.network_profiles[0].qbittorrent.host is None
    assert config.network_profiles[0].sftp.port is None


def test_proxy_mode_requires_host_and_port():
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "broken",
            "qbittorrent": {"mode": "socks5h"},
        }
    ]
    config_data["downloaders"][0]["network_profile"] = "broken"

    with pytest.raises(ValueError, match="host"):
        Config(**config_data)


def test_qbittorrent_proxy_endpoint_unknown_field_is_rejected():
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "broken",
            "qbittorrent": {
                "mode": "socks5h",
                "hosst": "127.0.0.1",
                "port": 7891,
            },
        }
    ]
    config_data["downloaders"][0]["network_profile"] = "broken"

    with pytest.raises(ValueError, match="hosst"):
        Config(**config_data)


def test_network_profile_unknown_top_level_field_is_rejected():
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "broken",
            "qbittorent": {
                "mode": "direct",
            },
        }
    ]
    config_data["downloaders"][0]["network_profile"] = "broken"

    with pytest.raises(ValueError, match="qbittorent"):
        Config(**config_data)


def test_sftp_http_proxy_mode_is_rejected():
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "broken",
            "sftp": {
                "mode": "http",
                "host": "127.0.0.1",
                "port": 8080,
            },
        }
    ]
    config_data["seed_box"][0]["network_profile"] = "broken"

    with pytest.raises(ValueError, match="sftp"):
        Config(**config_data)


def test_qbittorrent_http_connect_proxy_mode_is_rejected():
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "broken",
            "qbittorrent": {
                "mode": "http_connect",
                "host": "127.0.0.1",
                "port": 7890,
            },
        }
    ]
    config_data["downloaders"][0]["network_profile"] = "broken"

    with pytest.raises(ValueError, match="qbittorrent"):
        Config(**config_data)


def test_sftp_http_connect_proxy_mode_is_allowed():
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "proxy-http-connect",
            "sftp": {
                "mode": "http_connect",
                "host": "127.0.0.1",
                "port": 7890,
            },
        }
    ]
    config_data["seed_box"][0]["network_profile"] = "proxy-http-connect"

    config = Config(**config_data)

    assert config.network_profiles[0].sftp.mode == "http_connect"
    assert config.network_profiles[0].sftp.host == "127.0.0.1"
    assert config.network_profiles[0].sftp.port == 7890


def test_unknown_network_profile_reference_is_rejected():
    config_data = make_config_dict()
    config_data["downloaders"][0]["network_profile"] = "missing"

    with pytest.raises(ValueError, match="missing"):
        Config(**config_data)


def test_downloader_network_profile_typo_is_rejected():
    config_data = make_config_dict()
    config_data["downloaders"][0]["network_profille"] = "nc-via-mihomo"

    with pytest.raises(ValueError, match="network_profille"):
        Config(**config_data)


def test_seed_box_network_profile_typo_is_rejected():
    config_data = make_config_dict()
    config_data["seed_box"][0]["network_profille"] = "nc-via-mihomo"

    with pytest.raises(ValueError, match="network_profille"):
        Config(**config_data)


def test_root_network_profiles_typo_is_rejected():
    config_data = make_config_dict()
    config_data["network_profilles"] = []

    with pytest.raises(ValueError, match="network_profilles"):
        Config(**config_data)


def test_same_name_downloader_and_seedbox_can_use_different_profiles_with_warning(caplog):
    config_data = make_config_dict()
    config_data["network_profiles"] = [
        {
            "name": "qb-profile",
            "qbittorrent": {"mode": "socks5h", "host": "127.0.0.1", "port": 7891},
        },
        {
            "name": "sftp-profile",
            "sftp": {"mode": "socks5", "host": "127.0.0.2", "port": 1080},
        },
    ]
    config_data["downloaders"][0]["network_profile"] = "qb-profile"
    config_data["seed_box"][0]["network_profile"] = "sftp-profile"

    with caplog.at_level(logging.WARNING):
        config = Config(**config_data)

    assert config.downloaders[0].network_profile == "qb-profile"
    assert config.seed_box[0].network_profile == "sftp-profile"
    assert "different network_profile" in caplog.text


def test_deploy_slice_avoids_pep_604_union_syntax_for_python38():
    repo_root = Path(__file__).resolve().parents[1]
    deploy_slice = [
        repo_root / "managers" / "seedbox_manager.py",
        repo_root / "utils" / "config.py",
        repo_root / "utils" / "downloader_utils.py",
        repo_root / "utils" / "sftp_utils.py",
    ]

    offending_lines = []
    annotation_pattern = re.compile(r"^\s*[\w,()\[\] =]+:\s*[^#'\"]*\|")
    return_pattern = re.compile(r"^\s*def .*\)\s*->\s*[^#'\"]*\|")
    for path in deploy_slice:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if "|" not in stripped:
                continue
            if annotation_pattern.search(line) or return_pattern.search(line):
                offending_lines.append(f"{path.name}:{line_number}:{stripped}")

    assert offending_lines == []
