import qbittorrentapi
from urllib import parse


class DownloaderHelper:
    def __init__(self, name, url, username, password):
        self.name = name
        self.url = url
        self.username = username
        self.password = password
        host = parse.urlparse(url).netloc
        port = parse.urlparse(url).port
        self.client = qbittorrentapi.Client(host=host, port=port, username=username, password=password)


def get_downloader_client(name, url, username, password):
    return DownloaderHelper(name, url, username, password)
