import logging
import os
from typing import Optional
from urllib import parse

import qbittorrentapi

from utils.config import Downloader, NetworkProfile, ProxyEndpoint, ProxyMode

logger = logging.getLogger(__name__)

ENV_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_WARNED_ENV_PROXY_DOWNLOADERS = set()


def _proxy_url(proxy_endpoint: ProxyEndpoint) -> str:
    auth = ""
    if proxy_endpoint.username:
        auth = proxy_endpoint.username
        if proxy_endpoint.password:
            auth = f"{auth}:{proxy_endpoint.password}"
        auth = f"{auth}@"
    return f"{proxy_endpoint.mode.value}://{auth}{proxy_endpoint.host}:{proxy_endpoint.port}"


def _proxy_summary(proxy_endpoint: ProxyEndpoint) -> str:
    if proxy_endpoint.mode == ProxyMode.direct or not proxy_endpoint.host or proxy_endpoint.port is None:
        return "direct"
    return f"{proxy_endpoint.mode.value}://{proxy_endpoint.host}:{proxy_endpoint.port}"


def _effective_qb_proxy_endpoint(network_profile: Optional[NetworkProfile]) -> Optional[ProxyEndpoint]:
    if network_profile is None:
        return None
    if network_profile.qbittorrent is None or not network_profile.qbittorrent.enabled:
        return ProxyEndpoint(enabled=False, mode=ProxyMode.direct)
    return network_profile.qbittorrent


def _env_proxy_is_configured() -> bool:
    return any(os.environ.get(key) for key in ENV_PROXY_KEYS)


def _warn_implicit_env_proxy(name: str):
    if name in _WARNED_ENV_PROXY_DOWNLOADERS or not _env_proxy_is_configured():
        return
    _WARNED_ENV_PROXY_DOWNLOADERS.add(name)
    logger.warning(
        "downloader '%s' has no explicit network_profile; qB HTTP behavior may be influenced by host environment "
        "proxy variables. Configure network_profile for explicit, auditable behavior.",
        name,
    )


def _disable_client_trust_env(client) -> None:
    session_attr_names = ["_session", "_http_session"]
    session_attr_names.extend(
        attr_name
        for attr_name in vars(client)
        if attr_name.endswith("_session") and attr_name not in session_attr_names
    )

    for attr_name in session_attr_names:
        session = getattr(client, attr_name, None)
        if hasattr(session, "trust_env"):
            session.trust_env = False


class DownloaderHelper:
    def __init__(
        self,
        name=None,
        url=None,
        username=None,
        password=None,
        downloader: Optional[Downloader] = None,
        network_profile: Optional[NetworkProfile] = None,
    ):
        if downloader is not None:
            name = downloader.name
            url = downloader.url
            username = downloader.username
            password = downloader.password

        self.name = name
        self.url = url
        self.username = username
        self.password = password
        host = parse.urlparse(url).netloc
        port = parse.urlparse(url).port
        client_kwargs = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        }
        proxy_endpoint = _effective_qb_proxy_endpoint(network_profile)
        explicit_profile = network_profile is not None
        if proxy_endpoint and proxy_endpoint.mode != ProxyMode.direct and proxy_endpoint.enabled:
            proxy_url = _proxy_url(proxy_endpoint)
            client_kwargs["REQUESTS_ARGS"] = {"proxies": {"http": proxy_url, "https": proxy_url}}
            logger.info("downloader '%s' using qb proxy %s", name, _proxy_summary(proxy_endpoint))
        elif explicit_profile:
            client_kwargs["REQUESTS_ARGS"] = {"proxies": {"http": None, "https": None}}
            logger.info(
                "downloader '%s' using explicit qb network profile '%s' with direct connection",
                name,
                network_profile.name,
            )
        else:
            _warn_implicit_env_proxy(name)

        self.client = qbittorrentapi.Client(**client_kwargs)
        if explicit_profile:
            _disable_client_trust_env(self.client)
        try:
            self.client.auth_log_in()
            if explicit_profile:
                _disable_client_trust_env(self.client)
            logger.info(f"Successfully connected to downloader '{name}' at {url}")
        except Exception as e:
            logger.error(f"Failed to connect to downloader '{name}' at {url}: {e}")
            raise


def get_downloader_client(
    name=None,
    url=None,
    username=None,
    password=None,
    downloader: Optional[Downloader] = None,
    network_profile: Optional[NetworkProfile] = None,
):
    return DownloaderHelper(
        name=name,
        url=url,
        username=username,
        password=password,
        downloader=downloader,
        network_profile=network_profile,
    )
