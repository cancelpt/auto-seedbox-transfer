import os
import paramiko
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SFTPClient:
    def __init__(self, hostname, port, username, password):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.transport = None
        self.sftp = None

    def connect(self):
        """连接到 SFTP 服务器"""
        try:
            self.transport = paramiko.Transport((self.hostname, self.port))
            self.transport.connect(username=self.username, password=self.password)
            self.sftp = paramiko.SFTPClient.from_transport(self.transport)
            logger.info("SFTP connection established.")
        except Exception as e:
            logger.error(f"Failed to connect to SFTP: {e}")

    def upload(self, local_file, remote_file):
        """上传文件"""
        try:
            self.sftp.put(local_file, remote_file)
            logger.info(f"Uploaded {local_file} to {remote_file}.")
        except Exception as e:
            logger.error(f"Failed to upload file: {e}")

    def download(self, remote_file, local_file):
        """下载文件"""
        try:
            self.sftp.get(remote_file, local_file)
            logger.info(f"Downloaded {remote_file} to {local_file}.")
        except Exception as e:
            logger.error(f"Failed to download file: {e}")

    def close(self):
        """关闭 SFTP 连接 """
        if self.sftp:
            self.sftp.close()
        if self.transport:
            self.transport.close()
        logger.info("SFTP connection closed.")
