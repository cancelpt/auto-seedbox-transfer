import logging

import pytest

from utils.config import Downloader, NetworkProfile, ProxyEndpoint
from utils.downloader_utils import get_downloader_client


class FakeQbClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._session = type("Session", (), {"trust_env": True})()
        self._http_session = type("Session", (), {"trust_env": True})()
        self.logged_in = False
        type(self).instances.append(self)

    def auth_log_in(self):
        self.logged_in = True
        self._session.trust_env = True
        self._http_session.trust_env = True


class LazySessionFakeQbClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._session = None
        self._http_session = None
        self.login_request_calls = []
        type(self).instances.append(self)

    def auth_log_in(self):
        def session_factory():
            return type("Session", (), {"trust_env": True})()

        self._session = session_factory()
        self._http_session = session_factory()
        proxies = self.kwargs.get("REQUESTS_ARGS", {}).get("proxies")
        self.login_request_calls.append(("head", self._session.trust_env, proxies))
        self.login_request_calls.append(("post", self._http_session.trust_env, proxies))


@pytest.fixture(autouse=True)
def reset_fake_client_instances():
    FakeQbClient.instances = []
    yield
    FakeQbClient.instances = []


def test_downloader_without_network_profile_keeps_legacy_behavior(monkeypatch):
    monkeypatch.setattr("utils.downloader_utils.qbittorrentapi.Client", FakeQbClient)

    helper = get_downloader_client(
        name="legacy",
        url="http://seedbox:8080",
        username="user",
        password="pass",
    )

    client = helper.client
    assert client.kwargs["host"] == "seedbox:8080"
    assert client.kwargs["port"] == 8080
    assert "REQUESTS_ARGS" not in client.kwargs
    assert client._session.trust_env is True
    assert client._http_session.trust_env is True


def test_downloader_explicit_proxy_sets_requests_args_and_disables_trust_env(monkeypatch, caplog):
    monkeypatch.setattr("utils.downloader_utils.qbittorrentapi.Client", FakeQbClient)
    downloader = Downloader(
        name="nc",
        url="http://seedbox.example:23454",
        username="user",
        password="pass",
        network_profile="nc-via-mihomo",
    )
    network_profile = NetworkProfile(
        name="nc-via-mihomo",
        qbittorrent=ProxyEndpoint(
            mode="socks5h",
            host="172.31.0.1",
            port=7891,
            username="proxy-user",
            password="super-secret",
        ),
    )

    with caplog.at_level(logging.INFO):
        helper = get_downloader_client(downloader=downloader, network_profile=network_profile)

    client = helper.client
    assert client.kwargs["REQUESTS_ARGS"] == {
        "proxies": {
            "http": "socks5h://proxy-user:super-secret@172.31.0.1:7891",
            "https": "socks5h://proxy-user:super-secret@172.31.0.1:7891",
        }
    }
    assert client._session.trust_env is False
    assert client._http_session.trust_env is False
    assert "172.31.0.1:7891" in caplog.text
    assert "proxy-user" not in caplog.text
    assert "super-secret" not in caplog.text


def test_downloader_explicit_direct_profile_disables_environment_proxy(monkeypatch):
    monkeypatch.setattr("utils.downloader_utils.qbittorrentapi.Client", FakeQbClient)
    downloader = Downloader(
        name="nc",
        url="http://seedbox.example:23454",
        username="user",
        password="pass",
        network_profile="direct-profile",
    )
    network_profile = NetworkProfile(
        name="direct-profile",
        qbittorrent=ProxyEndpoint(mode="direct"),
    )

    helper = get_downloader_client(downloader=downloader, network_profile=network_profile)

    client = helper.client
    assert client.kwargs["REQUESTS_ARGS"] == {"proxies": {"http": None, "https": None}}
    assert client._session.trust_env is False
    assert client._http_session.trust_env is False


def test_downloader_explicit_direct_profile_applies_no_proxy_to_initial_requests(monkeypatch):
    monkeypatch.setattr("utils.downloader_utils.qbittorrentapi.Client", LazySessionFakeQbClient)

    downloader = Downloader(
        name="nc",
        url="http://seedbox.example:23454",
        username="user",
        password="pass",
        network_profile="direct-profile",
    )
    network_profile = NetworkProfile(
        name="direct-profile",
        qbittorrent=ProxyEndpoint(mode="direct"),
    )

    helper = get_downloader_client(downloader=downloader, network_profile=network_profile)

    client = helper.client
    assert client._session.trust_env is False
    assert client._http_session.trust_env is False
    assert client.login_request_calls == [
        ("head", True, {"http": None, "https": None}),
        ("post", True, {"http": None, "https": None}),
    ]
