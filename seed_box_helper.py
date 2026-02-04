import argparse
import json
import logging
import os
import re
import shutil
import time

import qbittorrentapi
from qbittorrentapi import Client, TorrentInfoList

from transfer.torrent_transfer import TorrentTransfer
from utils.config import YAMLConfigHandler, Config, SeedBox
from utils.downloader_utils import get_downloader_client, DownloaderHelper
from utils.torrent_utils import TorrentFile, export_as_torrent
from utils.transfer_utils import load_transfer_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)




def save_transfer_status(transfer_status_dict, transfer_file_path):
    transfer_status_list = [transfer.dict() for transfer in transfer_status_dict.values()]
    with open(transfer_file_path, 'w') as f:
        json.dump(transfer_status_list, f)


def check_existed_torrent(dl, torrent_status, transfer_status_dict, transfer_file_path, seed_box_config):
    bt_torrent_info = dl.torrents_info(torrent_hashes=torrent_status.bt_hash)
    if bt_torrent_info is None:
        torrent_status.is_bt_in_home_dl = False
        save_transfer_status(transfer_status_dict, transfer_file_path)
        logger.info(f"BT种子不存在：{torrent_status.bt_hash}")
        return False, None
    bt_torrent = bt_torrent_info[0]

    if bt_torrent.progress != 1:
        dl.torrents_add_peers(torrent_hashes=bt_torrent.hash, peers=[
            f"{seed_box_config.ssh_host}:{seed_box_config.incoming_port}"
        ])
        # dl.torrents_reannounce(torrent_hashes=bt_torrent.hash)
        logger.info(f"BT种子还未完成：{bt_torrent.name}，进度{bt_torrent.progress}")
        return False, bt_torrent

    logger.info(f"BT种子下载完成：{bt_torrent.name}")
    return True, bt_torrent


def main(settings, seed_box_name, home_dl_name, target_download_dir):
    operation_make = False
    # 读取配置

    original_torrent_path = settings.transfer.original_torrent_path
    bt_path = settings.transfer.bt_path
    if not os.path.exists(bt_path):
        os.makedirs(bt_path)

    # 读取transfer文件
    transfer_file_path = os.path.join(settings.transfer.torrent_info_path)
    transfer_status_dict = load_transfer_file(str(transfer_file_path))

    # 从配置中获取家宽下载器和盒子下载器
    home_dl_config = None
    seed_box_dl_config = None
    for dl_config in settings.downloaders:
        if dl_config.name == home_dl_name:
            home_dl_config = dl_config
            continue
        if dl_config.name == seed_box_name:
            seed_box_dl_config = dl_config
            continue
        if home_dl_config and seed_box_dl_config:
            break
    else:
        if not home_dl_config:
            logger.error("未找到家宽下载器")
            return
        if not seed_box_dl_config:
            logger.error("未找到种子盒子下载器")
            return

    # 取出seedbox下载器配置

    seed_box_config: SeedBox = next(filter(lambda x: x.name == seed_box_name, config.seed_box), None)
    if seed_box_config is None:
        raise ValueError(f"找不到盒子配置：{seed_box_name}")

    logger.info(f"盒子下载器为：【{seed_box_dl_config.name}】 {seed_box_dl_config.url}")
    logger.info(f"家宽下载器为：【{home_dl_config.name}】 {home_dl_config.url}")

    # hash to file path
    torrent_hash_to_info = {}

    logger.info(f"开始遍历 {original_torrent_path} 目录下的原始种子文件")
    for root, dirs, files in os.walk(original_torrent_path):
        for file in files:
            if file.endswith('.torrent'):
                torrent_file = os.path.join(root, file)
                torrent_file_info = TorrentFile(str(torrent_file))
                # 生成info_hash
                torrent_hash_to_info[torrent_file_info.info_hash] = torrent_file_info
                if torrent_file_info.info_hash in transfer_status_dict:
                    continue
                # 转为BT
                try:
                    result, temp_bt_file_name, bt_torrent_file = export_as_torrent(
                            torrent_file_info.torrent_data, settings.transfer.bt_trackers)

                    bt_file_name = os.path.join(bt_path, temp_bt_file_name)

                    shutil.move(temp_bt_file_name, bt_file_name)

                    if result:
                        logger.info(f"导出BT种子成功：{bt_file_name}，hash：{bt_torrent_file.info_hash}")
                        torrent_status = TorrentTransfer(hash=torrent_file_info.info_hash,
                                                        bt_hash=bt_torrent_file.info_hash,
                                                        origin_torrent_file_path=torrent_file_info.file_path,
                                                        bt_torrent_file_path=bt_file_name)
                        transfer_status_dict[torrent_file_info.info_hash] = torrent_status
                        save_transfer_status(transfer_status_dict, transfer_file_path)
                        operation_make = True
                    else:
                        logger.error(f"导出BT种子失败：{bt_file_name}")
                        continue
                except Exception as e:
                    logger.error(f"导出BT种子失败：{torrent_file_info.file_path} - {str(e)}")
                    continue

    # 删除transfer_status_dict中，实际目录不存在的种子
    for temp_hash in list(transfer_status_dict.keys()):
        if temp_hash not in torrent_hash_to_info:
            del transfer_status_dict[temp_hash]
            logger.info(f"删除transfer_status_dict中不存在的种子：{temp_hash}")
    save_transfer_status(transfer_status_dict, transfer_file_path)

    # 处理盒子
    seed_box_torrent_hashes = []
    # try:
    seed_box_helper: DownloaderHelper = get_downloader_client(name=seed_box_dl_config.name,
                                                            url=seed_box_dl_config.url,
                                                            username=seed_box_dl_config.username,
                                                            password=seed_box_dl_config.password)
    seed_box_dl: Client = seed_box_helper.client
    logger.info(f"准备从盒子下载器：{seed_box_dl_config.name}获取种子列表")
    torrents: TorrentInfoList = seed_box_dl.torrents_info(
        # status='forcedUP | stalledUP | pausedUP | uploading')
        status='completed')

    logger.info(f"将种子hash预处理")
    seed_box_torrent_hashes = [torrent.hash for torrent in torrents]

    logger.info(f"盒子【{seed_box_dl_config.name}】下载器完成状态的种子数量为：{len(seed_box_torrent_hashes)}个")
    
    add_torrent_count = 0

    for torrent in torrents:
        if add_torrent_count > settings.transfer.max_once_add:
            logger.info(f"盒子一次最多添加{settings.transfer.max_once_add}个种子")
            break
        
        if torrent.category != home_dl_config.want_torrent_category:
            logger.debug(f"盒子种子{torrent.name}不是【{home_dl_config.want_torrent_category}】类，跳过")
            continue
        # 盒子上的种子是否本地存在
        if torrent.hash not in transfer_status_dict:
            logger.info(f"盒子种子{torrent.name}不在本地目录中")
            continue
            
        # 进度是否1
        if torrent.progress != 1 or torrent.get('completed_size') != torrent.get('selected_size'):
            logger.info(f"盒子种子{torrent.name}还未完成，进度{torrent.progress}")
            continue

        # 取出种子转移状态
        torrent_status = transfer_status_dict[torrent.hash]
        logger.info(f"处理盒子种子：{torrent.name}，hash：{torrent.hash}，BT：{torrent_status.bt_hash}")
        if torrent_status.is_torrent_in_home_dl:
            # 种子已经在家宽下载器中，删除
            if torrent_status.bt_hash in seed_box_torrent_hashes:
                seed_box_dl.torrents_delete(torrent_hashes=torrent_status.bt_hash, delete_files=True)
            
            if torrent_status.hash in seed_box_torrent_hashes:
                seed_box_dl.torrents_delete(torrent_hashes=torrent_status.hash, delete_files=True)
            logger.info(
                f"种子{torrent.name}转移完成：{torrent_status.hash}，BT：{torrent_status.bt_hash}，从种子盒子中删除")
            continue

        if torrent_status.bt_hash in seed_box_torrent_hashes:
            continue

        if torrent_status.bt_hash in seed_box_torrent_hashes and torrent_status.is_bt_in_seed_box is False:
            logger.info(f"种子{torrent.name} 的 BT 种子在盒子上，hash {torrent_status.bt_hash}")
            torrent_status.is_bt_in_seed_box = True
            save_transfer_status(transfer_status_dict, transfer_file_path)
            operation_make = True
            continue

        logger.info(f"种子{torrent.name} 的 BT 种子不在盒子上，开始添加")

        if 'Ok.' in seed_box_dl.torrents_add(torrent_files=torrent_status.bt_torrent_file_path,
                                            category=settings.transfer.seed_box_bt_category, is_skip_checking=True,
                                            download_path=torrent.save_path):
            logger.info(f"添加种子成功：{torrent_hash_to_info[torrent_status.hash].file_name}")
            seed_box_torrent_hashes.append(torrent_status.hash)
            torrent_status.is_bt_in_seed_box = True
            add_torrent_count += 1
            save_transfer_status(transfer_status_dict, transfer_file_path)
            operation_make = True
        else:
            logger.error(f"添加种子失败：{torrent_hash_to_info[torrent_status.hash].file_name}")
    # except Exception as e:
    #     logger.error(f"处理盒子种子失败：{e}")

    home_dl_helper = get_downloader_client(name=home_dl_config.name, url=home_dl_config.url,
                                           username=home_dl_config.username, password=home_dl_config.password)
    home_dl: qbittorrentapi.Client = home_dl_helper.client
    # 过滤出home_dl中没有的种子
    home_dl_hash = [torrent.hash for torrent in home_dl.torrents_info()]

    logger.info(f"家宽下载器种子数量为：{len(home_dl_hash)}")
    add_torrent_count = 0
    # 不在家宽下载器中的种子
    for transfer_origin_hash, torrent_status in transfer_status_dict.items():
        
        if add_torrent_count > settings.transfer.max_once_add:
            logger.info(f"家宽一次最多添加{settings.transfer.max_once_add}个种子")
            break
        logger.debug(f"处理家宽种子：{torrent_status.origin_torrent_file_path}")
        # BT种子在盒子 且 BT种子和源种子都不在家宽下载器
        logger.debug(f"BT种子是否在盒子下载器：{torrent_status.is_bt_in_seed_box}")
        logger.debug(f"BT种子是否在家宽下载器：{torrent_status.is_bt_in_home_dl}")
        logger.debug(f"是否在家宽下载器：{torrent_status.is_torrent_in_home_dl}")
        if torrent_status.bt_hash in seed_box_torrent_hashes and \
                torrent_status.bt_hash not in home_dl_hash and not torrent_status.is_torrent_in_home_dl:
            torrent_file_path = torrent_status.bt_torrent_file_path
            logger.info(f"向家宽下载器放 BT 种子：{torrent_file_path}")
            if 'Ok.' in home_dl.torrents_add(torrent_files=torrent_file_path, download_dir=target_download_dir,
                                             category=settings.transfer.home_bt_category):
                logger.info(f"家宽添加种子成功：{torrent_file_path}")
                torrent_status.is_bt_in_home_dl = True
                save_transfer_status(transfer_status_dict, transfer_file_path)
                add_torrent_count += 1
                operation_make = True
                continue
        # 源种子不在家宽下载器，但BT种子在家宽下载器
        # 可能正在拉回本地或者拉完了
        if torrent_status.hash not in home_dl_hash and torrent_status.bt_hash in home_dl_hash:
            # 从家宽下载器取出BT种子的状态信息
            is_bt_in_home_dl, bt_torrent = check_existed_torrent(home_dl, torrent_status, transfer_status_dict,
                                                                 transfer_file_path, seed_box_config)
            if not is_bt_in_home_dl:
                continue

            logger.info(f"向本地下载器放 {bt_torrent.name} 的原始种子")
            if 'Ok.' in home_dl.torrents_add(torrent_files=torrent_status.origin_torrent_file_path,
                                             download_dir=target_download_dir,
                                             category=settings.transfer.home_origin_temp_category,
                                             is_skip_checking=True):
                logger.info(f"家宽添加种子成功：{bt_torrent.name}")
                torrent_status.is_torrent_in_home_dl = True
                save_transfer_status(transfer_status_dict, transfer_file_path)
                operation_make = True
        #
        elif torrent_status.hash in home_dl_hash and torrent_status.bt_hash in home_dl_hash:
            # 从家宽下载器取出BT种子的状态信息
            is_bt_in_home_dl, bt_torrent = check_existed_torrent(home_dl, torrent_status, transfer_status_dict,
                                                                 transfer_file_path, seed_box_config)
            if not is_bt_in_home_dl:
                continue
            # 删除BT种子，不删除文件
            home_dl.torrents_delete(torrent_hashes=torrent_status.bt_hash, delete_files=False)
            operation_make = True
            logger.info(
                f"家宽重新检查发现{os.path.basename(torrent_status.origin_torrent_file_path)}的BT种子和原始种子都在，"
                f"因此删除BT种子：{torrent_status.bt_hash}")
            
            home_dl.torrents_set_category(category=settings.transfer.home_origin_category, torrent_hashes=torrent_status.hash)

        elif (torrent_status.hash in home_dl_hash and torrent_status.bt_hash not in home_dl_hash
              and not torrent_status.is_torrent_in_home_dl):

            torrent_infos = home_dl.torrents_info(torrent_hashes=torrent_status.hash)
            if not torrent_infos:
                torrent_status.is_torrent_in_home_dl = False
                save_transfer_status(transfer_status_dict, transfer_file_path)
                logger.info(f"家宽种子不存在：{torrent_status.hash}")
                continue

            torrent_info = torrent_infos[0]
            home_dl.torrents_add_peers(torrent_hashes=torrent_info.bt_hash, peers=[
                f"{seed_box_config.ssh_host}:{seed_box_config.incoming_port}",
                f"[{seed_box_config.ipv6}]:{seed_box_config.incoming_port}"
            ])

            if torrent_info.progress != 1:
                logger.info(f"家宽种子还未完成：{torrent_info.name}，进度{torrent_info.progress}")
                # home_dl.torrents_reannounce(torrent_hashes=torrent_info.bt_hash)
                continue
            home_dl.torrents_delete(torrent_hashes=torrent_status.bt_hash, delete_files=False)
            operation_make = True

            torrent_status.is_torrent_in_home_dl = True
            save_transfer_status(transfer_status_dict, transfer_file_path)
            logger.info(f"家宽重新检查发现原始种子在：{os.path.basename(torrent_status.origin_torrent_file_path)}"
                        f"因此删除BT种子：{torrent_status.bt_hash}")
            
            home_dl.torrents_set_category(category=settings.transfer.home_origin_category, torrent_hashes=torrent_status.hash)
    return operation_make
    pass


if __name__ == '__main__':
    # 解析命令行参数
    parser = argparse.ArgumentParser()
    # 配置文件路径
    parser.add_argument("--config_path", type=str, default='config.yaml', help="配置文件路径")
    # 种子盒子名称
    parser.add_argument("--seed_box_name", type=str, default=None, required=True, help="种子盒子名称")
    # 目标的家宽下载器名称
    parser.add_argument("--home_dl_name", type=str, default=None, required=True, help="目标的家宽下载器名称")
    # 目标下载目录
    parser.add_argument("--target_download_dir", type=str, default=None, help="目标下载目录")

    args = parser.parse_args()

    logger.info(f"种子盒子名称为: {args.seed_box_name}")
    logger.info(f"目标的家宽下载器名称为: {args.home_dl_name}")
    config: Config = YAMLConfigHandler.load(args.config_path)
    if not os.path.exists(config.transfer.original_torrent_path):
        raise FileNotFoundError(f"种子文件路径不存在：{config.transfer.original_torrent_path}")
    while True:
        is_operation_make = False
        try:
            is_operation_make = main(config, args.seed_box_name, args.home_dl_name, args.target_download_dir)
        except Exception as e:
            logger.error(e)
        finally:
            if is_operation_make:
                logger.info("操作成功！延时1秒")
                time.sleep(1)
            else:
                logger.info("无操作，延时60秒")
                time.sleep(60)
