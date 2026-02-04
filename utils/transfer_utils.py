import json
import logging
import os
from typing import Dict, List, Optional

from transfer.torrent_transfer import TorrentTransfer

logger = logging.getLogger(__name__)


def load_transfer_file(file_path: str) -> Optional[Dict[str, 'TorrentTransfer']]:
    """
    从指定的文件路径加载 transfer 数据并返回一个包含 TorrentTransfer 实例的字典。

    :param file_path: JSON 格式的 transfer 文件路径
    :return: 包含 TorrentTransfer 实例的字典，如果文件不存在或读取失败则返回 None
    """
    transfer_status_dict: Dict[str, 'TorrentTransfer'] = {}

    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                transfer_status_list: List[dict] = json.load(f)

            # 将字典转换为 TorrentTransfer 的实例
            for transfer_data in transfer_status_list:
                try:
                    transfer = TorrentTransfer(**transfer_data)  # 使用解包方式创建 TorrentTransfer 实例
                    transfer_status_dict[transfer.hash] = transfer  # 使用 hash 作为字典的键
                except (TypeError, ValueError) as e:
                    logger.error(f"Error creating TorrentTransfer from data: {transfer_data} - {str(e)}")
                except Exception as e:
                    logger.error(f"Unexpected error creating TorrentTransfer from data: {transfer_data} - {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from {file_path} - {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error while reading file: {file_path} - {str(e)}")
    else:
        logger.warning(f"Transfer file does not exist: {file_path}")

    return transfer_status_dict
