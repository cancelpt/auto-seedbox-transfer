import logging

import pytest

from utils.config import Config, Downloader, SeedBox, Transfer, validate_route_config


def make_config(seedbox_downloader, home_downloader):
    return Config(
        transfer=Transfer(
            original_torrent_path="/tmp/original",
            bt_path="/tmp/bt",
            torrent_info_path="/tmp/state.json",
            bt_trackers=[],
            seedbox_origin_data_missing_policy="pause_transfer",
        ),
        seed_box=[
            SeedBox(
                name="seedbox",
                ssh_host="seed.example",
                incoming_port=60000,
                ssh_user="user",
                ssh_password="pass",
                torrents_path="/remote/torrents",
            )
        ],
        downloaders=[seedbox_downloader, home_downloader],
    )


def test_downloader_accepts_source_categories_as_preferred_name():
    downloader = Downloader(
        name="seedbox",
        url="http://seedbox:8080",
        username="user",
        password="pass",
        source_categories=["SourceA", "SourceB"],
    )

    assert downloader.source_categories == ["SourceA", "SourceB"]
    assert downloader.want_torrent_category == ["SourceA", "SourceB"]
    assert downloader.get_source_categories() == {"SourceA", "SourceB"}


def test_downloader_keeps_legacy_want_torrent_category_compatible():
    downloader = Downloader(
        name="seedbox",
        url="http://seedbox:8080",
        username="user",
        password="pass",
        want_torrent_category="To",
    )

    assert downloader.source_categories == "To"
    assert downloader.want_torrent_category == "To"
    assert downloader.get_source_categories() == {"To"}


def test_validate_route_config_rejects_missing_seedbox_source_categories():
    config = make_config(
        Downloader(name="seedbox", url="http://seedbox:8080", username="user", password="pass"),
        Downloader(name="home", url="http://home:8080", username="user", password="pass"),
    )

    with pytest.raises(ValueError, match="source_categories"):
        validate_route_config(config, "seedbox", "home")


def test_validate_route_config_warns_when_home_has_source_categories(caplog):
    config = make_config(
        Downloader(
            name="seedbox",
            url="http://seedbox:8080",
            username="user",
            password="pass",
            source_categories=["To"],
        ),
        Downloader(
            name="home",
            url="http://home:8080",
            username="user",
            password="pass",
            source_categories=["Unused"],
        ),
    )

    with caplog.at_level(logging.WARNING):
        seedbox_downloader, home_downloader = validate_route_config(config, "seedbox", "home")

    assert seedbox_downloader.name == "seedbox"
    assert home_downloader.name == "home"
    assert "home downloader 'home' has source_categories configured" in caplog.text
