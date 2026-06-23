import importlib
import logging
import socket
from base64 import b64encode
from typing import Optional

import paramiko

from utils.config import ProxyEndpoint, ProxyMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _PrefixedSocket:
    def __init__(self, sock, prefix: bytes):
        self._sock = sock
        self._prefix = bytearray(prefix)

    def recv(self, size):
        if self._prefix:
            if size is None or size <= 0:
                size = len(self._prefix)
            chunk = bytes(self._prefix[:size])
            del self._prefix[:size]
            return chunk
        return self._sock.recv(size)

    def __getattr__(self, name):
        return getattr(self._sock, name)


class SFTPClient:
    def __init__(self, hostname, port, username, password, proxy_endpoint: Optional[ProxyEndpoint] = None):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.proxy_endpoint = proxy_endpoint
        self.transport = None
        self.sftp = None

    @staticmethod
    def _proxy_summary(proxy_endpoint: Optional[ProxyEndpoint]) -> str:
        if proxy_endpoint is None or proxy_endpoint.mode == ProxyMode.direct:
            return "direct"
        return f"{proxy_endpoint.mode.value}://{proxy_endpoint.host}:{proxy_endpoint.port}"

    def _build_http_connect_socket(self, proxy_endpoint: ProxyEndpoint):
        proxy_socket = socket.create_connection((proxy_endpoint.host, proxy_endpoint.port))
        connect_target = f"{self.hostname}:{self.port}"
        request_lines = [
            f"CONNECT {connect_target} HTTP/1.1",
            f"Host: {connect_target}",
            "Proxy-Connection: Keep-Alive",
        ]
        if proxy_endpoint.username:
            password = proxy_endpoint.password or ""
            credentials = f"{proxy_endpoint.username}:{password}".encode("utf-8")
            request_lines.append(f"Proxy-Authorization: Basic {b64encode(credentials).decode('ascii')}")
        request = "\r\n".join(request_lines).encode("ascii") + b"\r\n\r\n"

        try:
            proxy_socket.sendall(request)
            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = proxy_socket.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
            header_bytes, _, buffered_after_headers = bytes(response).partition(b"\r\n\r\n")
            if not header_bytes:
                raise ConnectionError("HTTP CONNECT proxy closed before sending a response.")
            status_line = header_bytes.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
            status_parts = status_line.split(" ", 2)
            if len(status_parts) < 2 or not status_parts[1].isdigit():
                raise ConnectionError(f"Invalid HTTP CONNECT proxy response: {status_line}")
            status_code = int(status_parts[1])
            if status_code < 200 or status_code >= 300:
                raise ConnectionError(f"HTTP CONNECT proxy failed with {status_line}")
            if buffered_after_headers:
                return _PrefixedSocket(proxy_socket, buffered_after_headers)
            return proxy_socket
        except Exception:
            proxy_socket.close()
            raise

    def _build_transport_target(self):
        proxy_endpoint = self.proxy_endpoint
        if proxy_endpoint is None or not proxy_endpoint.enabled or proxy_endpoint.mode == ProxyMode.direct:
            return self.hostname, self.port

        logger.info("SFTP connection using proxy %s", self._proxy_summary(proxy_endpoint))
        if proxy_endpoint.mode == ProxyMode.http_connect:
            return self._build_http_connect_socket(proxy_endpoint)

        try:
            socks = importlib.import_module("socks")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                f"PySocks is required for SFTP proxy mode '{proxy_endpoint.mode.value}'. "
                "Install dependencies from requirements.txt."
            ) from exc

        proxy_socket = socks.socksocket()
        proxy_socket.set_proxy(
            proxy_type=socks.SOCKS5,
            addr=proxy_endpoint.host,
            port=proxy_endpoint.port,
            username=proxy_endpoint.username,
            password=proxy_endpoint.password,
            rdns=proxy_endpoint.mode == ProxyMode.socks5h,
        )
        proxy_socket.connect((self.hostname, self.port))
        return proxy_socket

    def connect(self):
        """连接到 SFTP 服务器"""
        try:
            self.transport = paramiko.Transport(self._build_transport_target())
            self.transport.connect(username=self.username, password=self.password)
            self.sftp = paramiko.SFTPClient.from_transport(self.transport)
            logger.info("SFTP connection established.")
        except Exception as e:
            logger.error(f"Failed to connect to SFTP: {e}")
            raise

    def upload(self, local_file, remote_file):
        """上传文件"""
        try:
            self.sftp.put(local_file, remote_file)
            logger.info(f"Uploaded {local_file} to {remote_file}.")
        except Exception as e:
            logger.error(f"Failed to upload file: {e}")
            raise

    def download(self, remote_file, local_file):
        """下载文件"""
        try:
            self.sftp.get(remote_file, local_file)
            logger.info(f"Downloaded {remote_file} to {local_file}.")
        except Exception as e:
            logger.error(f"Failed to download file: {e}")
            raise

    def close(self):
        """关闭 SFTP 连接"""
        if self.sftp:
            self.sftp.close()
        if self.transport:
            self.transport.close()
        logger.info("SFTP connection closed.")
