import argparse
import logging
import os
from pathlib import Path

from qbittorrentapi import Client, TorrentInfoList

from utils.config import Config, YAMLConfigHandler, SeedBox
from utils.downloader_utils import DownloaderHelper
from utils.sftp_utils import SFTPClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main(config_path, category, torrent_dir):
    config: Config = YAMLConfigHandler.load(config_path)
    # 获取盒子配置
    seed_box_config: SeedBox = next(filter(lambda x: x.name == args.seed_box_name, config.seed_box), None)
    if seed_box_config is None:
        raise ValueError(f"找不到盒子配置：{args.seed_box_name}")
    seed_box_dl_config = next(filter(lambda x: x.name == seed_box_config.name, config.downloaders), None)
    if seed_box_dl_config is None:
        raise ValueError(f"找不到盒子下载器配置：{seed_box_config.name}")

    sftp_client = SFTPClient(
        hostname=seed_box_config.ssh_host,
        username=seed_box_config.ssh_user,
        password=seed_box_config.ssh_password,
        port=seed_box_config.ssh_port
    )

    if not os.path.exists(args.torrent_dir):
        os.makedirs(args.torrent_dir)

    seed_box_dl_helper = DownloaderHelper(name=seed_box_dl_config.name, url=seed_box_dl_config.url,
                                          username=seed_box_dl_config.username, password=seed_box_dl_config.password)
    seed_box_dl: Client = seed_box_dl_helper.client

    # 获取种子列表
    torrent_list: TorrentInfoList = seed_box_dl.torrents_info(category=category)
    logger.info(f"种子列表长度：{len(torrent_list)}")

    sftp_client.connect()
    # 下载种子文件
    for torrent in torrent_list:
        # 获取种子hash
        torrent_hash = torrent.hash
        # 构造种子在本地的路径
        local_torrent_file_path = os.path.join(torrent_dir, f"{torrent_hash}.torrent")
        if os.path.exists(local_torrent_file_path):
            logger.info(f"种子文件已存在：{local_torrent_file_path}")
            continue

        # 构造种子在盒子上的路径

        # 使用unix风格 Path
        seed_box_torrent_file_path = Path(seed_box_config.torrents_path) / f"{torrent_hash}.torrent"
        # 下载种子文件
        try:
            sftp_client.download(seed_box_torrent_file_path.as_posix(), local_torrent_file_path)
        except Exception as e:
            logger.error(f"下载种子文件【{seed_box_torrent_file_path}】失败：{e}")

    sftp_client.close()
    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    # 配置文件路径
    parser.add_argument("--config_path", type=str, default='config.yaml', help="配置文件路径")
    # 盒子名称
    parser.add_argument("--seed_box_name", type=str, default=None, required=True, help="种子盒子名称")
    # 需要导出的种子的分类
    parser.add_argument("--category", type=str, default=None, required=True, help="种子的分类")
    # 目标下载目录
    parser.add_argument("--torrent_dir", type=str, default=None, required=True, help="种子下载目录")

    args = parser.parse_args()

    main(args.config_path, args.category, args.torrent_dir)
