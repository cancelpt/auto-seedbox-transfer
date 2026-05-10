import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


if "qbittorrentapi" not in sys.modules:
    qbittorrentapi = types.ModuleType("qbittorrentapi")

    class Client:  # pragma: no cover - simple import stub
        pass

    qbittorrentapi.Client = Client
    qbittorrentapi.TorrentInfoList = list
    sys.modules["qbittorrentapi"] = qbittorrentapi


if "paramiko" not in sys.modules:
    paramiko = types.ModuleType("paramiko")

    class Transport:  # pragma: no cover - simple import stub
        def __init__(self, *args, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            return None

        def close(self):
            return None

    class _SFTPClient:  # pragma: no cover - simple import stub
        @staticmethod
        def from_transport(_transport):
            return _SFTPClient()

        def get(self, *_args, **_kwargs):
            raise NotImplementedError

        def put(self, *_args, **_kwargs):
            raise NotImplementedError

        def close(self):
            return None

    paramiko.Transport = Transport
    paramiko.SFTPClient = _SFTPClient
    sys.modules["paramiko"] = paramiko


if "bencodepy" not in sys.modules:
    bencodepy = types.ModuleType("bencodepy")
    bencodepy.decode = lambda data: data
    bencodepy.encode = lambda data: b"encoded"
    sys.modules["bencodepy"] = bencodepy
