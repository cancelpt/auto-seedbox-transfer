import logging

import qbittorrentapi
from urllib import parse

logger = logging.getLogger(__name__)


class DownloaderHelper:
    def __init__(self, name, url, username, password):
        self.name = name
        self.url = url
        self.username = username
        self.password = password
        host = parse.urlparse(url).netloc
        port = parse.urlparse(url).port
        self.client = qbittorrentapi.Client(host=host, port=port, username=username, password=password)
        try:
            self.client.auth_log_in()
            logger.info(f"Successfully connected to downloader '{name}' at {url}")
        except Exception as e:
            logger.error(f"Failed to connect to downloader '{name}' at {url}: {e}")
            raise


def get_downloader_client(name, url, username, password):
    return DownloaderHelper(name, url, username, password)
