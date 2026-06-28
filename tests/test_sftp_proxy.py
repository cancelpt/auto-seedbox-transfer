import importlib
import logging
import sys
import types

import pytest

import utils.sftp_utils as sftp_utils
from utils.config import ProxyEndpoint
from utils.sftp_utils import SFTPClient


class FakeTransport:
    instances = []

    def __init__(self, sock):
        self.sock = sock
        self.connect_calls = []
        self.closed = False
        type(self).instances.append(self)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)

    def close(self):
        self.closed = True


class FakeParamikoSFTPClient:
    instances = []
    remote_files = {}

    @staticmethod
    def from_transport(transport):
        client = FakeParamikoSFTPClient()
        client.transport = transport
        client.open_calls = []
        FakeParamikoSFTPClient.instances.append(client)
        return client

    def open(self, remote_path, mode="rb"):
        self.open_calls.append((remote_path, mode))
        return FakeRemoteFile(self.remote_files[remote_path])

    def close(self):
        return None


class FakeSocksSocket:
    instances = []

    def __init__(self):
        self.proxy = None
        self.connected_to = None
        type(self).instances.append(self)

    def set_proxy(self, **kwargs):
        self.proxy = kwargs

    def connect(self, target):
        self.connected_to = target


class FakeHttpConnectSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        if self.responses:
            return self.responses.pop(0)
        return b""

    def close(self):
        self.closed = True


class FakeRemoteFile:
    def __init__(self, payload):
        self.payload = payload
        self.offset = 0

    def seek(self, offset):
        self.offset = offset

    def read(self, length):
        chunk = self.payload[self.offset : self.offset + length]
        self.offset += len(chunk)
        return chunk

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


@pytest.fixture(autouse=True)
def patch_paramiko(monkeypatch):
    monkeypatch.setattr("utils.sftp_utils.paramiko.Transport", FakeTransport)
    monkeypatch.setattr("utils.sftp_utils.paramiko.SFTPClient", FakeParamikoSFTPClient)
    FakeTransport.instances = []
    FakeParamikoSFTPClient.instances = []
    FakeParamikoSFTPClient.remote_files = {}
    FakeSocksSocket.instances = []
    yield


def test_sftp_direct_connection_uses_transport_tuple():
    client = SFTPClient(hostname="seed.example", port=22, username="user", password="pass")

    client.connect()

    transport = FakeTransport.instances[0]
    assert transport.sock == ("seed.example", 22)
    assert transport.connect_calls == [{"username": "user", "password": "pass"}]


@pytest.mark.parametrize(("mode", "rdns"), [("socks5", False), ("socks5h", True)])
def test_sftp_socks_proxy_uses_pysocks_socket(monkeypatch, mode, rdns, caplog):
    fake_socks = types.SimpleNamespace(
        SOCKS5=5,
        socksocket=FakeSocksSocket,
    )
    monkeypatch.setitem(sys.modules, "socks", fake_socks)
    proxy_endpoint = ProxyEndpoint(
        mode=mode,
        host="127.0.0.1",
        port=7891,
        username="proxy-user",
        password="super-secret",
    )

    with caplog.at_level(logging.INFO):
        client = SFTPClient(
            hostname="seed.example",
            port=22,
            username="user",
            password="pass",
            proxy_endpoint=proxy_endpoint,
        )
        client.connect()

    sock = FakeSocksSocket.instances[0]
    transport = FakeTransport.instances[0]
    assert sock.proxy == {
        "proxy_type": 5,
        "addr": "127.0.0.1",
        "port": 7891,
        "username": "proxy-user",
        "password": "super-secret",
        "rdns": rdns,
    }
    assert sock.connected_to == ("seed.example", 22)
    assert transport.sock is sock
    assert "127.0.0.1:7891" in caplog.text
    assert "proxy-user" not in caplog.text
    assert "super-secret" not in caplog.text


def test_sftp_proxy_dependency_error_is_lazy_and_clear(monkeypatch):
    proxy_endpoint = ProxyEndpoint(mode="socks5", host="127.0.0.1", port=7891)
    client = SFTPClient(
        hostname="seed.example",
        port=22,
        username="user",
        password="pass",
        proxy_endpoint=proxy_endpoint,
    )
    original_import_module = importlib.import_module

    def fake_import_module(name, package=None):
        if name == "socks":
            raise ModuleNotFoundError("No module named 'socks'")
        return original_import_module(name, package)

    monkeypatch.setattr("utils.sftp_utils.importlib.import_module", fake_import_module)

    with pytest.raises(RuntimeError, match="PySocks"):
        client.connect()


def test_sftp_http_connect_proxy_tunnels_socket_and_redacts_logs(monkeypatch, caplog):
    http_socket = FakeHttpConnectSocket(
        [
            b"HTTP/1.1 200 Connection established\r\n",
            b"Proxy-Agent: test-proxy\r\n\r\n",
        ]
    )
    create_connection_calls = []

    def fake_create_connection(target, timeout=None, source_address=None):
        create_connection_calls.append((target, timeout, source_address))
        return http_socket

    monkeypatch.setattr(
        sftp_utils,
        "socket",
        types.SimpleNamespace(create_connection=fake_create_connection),
        raising=False,
    )
    proxy_endpoint = ProxyEndpoint(
        mode="http_connect",
        host="127.0.0.1",
        port=7890,
        username="proxy-user",
        password="super-secret",
    )

    with caplog.at_level(logging.INFO):
        client = SFTPClient(
            hostname="seed.example",
            port=22,
            username="user",
            password="pass",
            proxy_endpoint=proxy_endpoint,
        )
        client.connect()

    assert create_connection_calls == [(("127.0.0.1", 7890), None, None)]
    request = b"".join(http_socket.sent)
    assert request.startswith(b"CONNECT seed.example:22 HTTP/1.1\r\n")
    assert b"Host: seed.example:22\r\n" in request
    assert b"Proxy-Authorization: Basic " in request
    assert request.endswith(b"\r\n\r\n")
    assert FakeTransport.instances[0].sock is http_socket
    assert "http_connect://127.0.0.1:7890" in caplog.text
    assert "proxy-user" not in caplog.text
    assert "super-secret" not in caplog.text


def test_sftp_http_connect_proxy_preserves_buffered_ssh_banner(monkeypatch):
    banner = b"SSH-2.0-OpenSSH_9.2p1\r\n"
    http_socket = FakeHttpConnectSocket(
        [
            b"HTTP/1.1 200 Connection established\r\n"
            b"Proxy-Agent: test-proxy\r\n\r\n" + banner,
        ]
    )

    monkeypatch.setattr(
        sftp_utils,
        "socket",
        types.SimpleNamespace(
            create_connection=lambda target, timeout=None, source_address=None: http_socket
        ),
        raising=False,
    )
    client = SFTPClient(
        hostname="seed.example",
        port=22,
        username="user",
        password="pass",
        proxy_endpoint=ProxyEndpoint(mode="http_connect", host="127.0.0.1", port=7890),
    )

    client.connect()

    transport_socket = FakeTransport.instances[0].sock
    assert transport_socket is not http_socket
    assert transport_socket.recv(len(banner)) == banner


def test_sftp_http_connect_proxy_rejects_non_200_response(monkeypatch):
    http_socket = FakeHttpConnectSocket(
        [
            b"HTTP/1.1 407 Proxy Authentication Required\r\n",
            b"Proxy-Agent: test-proxy\r\n\r\n",
        ]
    )

    monkeypatch.setattr(
        sftp_utils,
        "socket",
        types.SimpleNamespace(
            create_connection=lambda target, timeout=None, source_address=None: http_socket
        ),
        raising=False,
    )
    client = SFTPClient(
        hostname="seed.example",
        port=22,
        username="user",
        password="pass",
        proxy_endpoint=ProxyEndpoint(mode="http_connect", host="127.0.0.1", port=7890),
    )

    with pytest.raises(ConnectionError, match="407"):
        client.connect()

    assert http_socket.closed is True


def test_sftp_read_range_supports_random_access_reads():
    FakeParamikoSFTPClient.remote_files = {
        "/remote/data.bin": b"0123456789",
    }
    client = SFTPClient(hostname="seed.example", port=22, username="user", password="pass")
    client.connect()

    payload = client.read_range("/remote/data.bin", offset=3, length=4)

    assert payload == b"3456"
    assert FakeParamikoSFTPClient.instances[0].open_calls == [
        ("/remote/data.bin", "rb"),
    ]
