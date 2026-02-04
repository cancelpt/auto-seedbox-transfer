import copy
import hashlib
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

import bencodepy

logging.basicConfig(level=logging.INFO)


class TorrentFile:
    @dataclass(frozen=True)
    class File:
        path: str
        size: int
        name: str

    def __init__(self, torrent_file: str | dict):
        self.private = None
        self.comment = None
        self.created_by = None
        self.creation_date = None
        self.trackers = None
        self.file_path = torrent_file
        self.torrent_data = None
        self.file_name = None
        self.source = None
        self.piece_length = None
        self.piece_count = None
        self._info_hash = None
        self._is_info_hash_calculated = False

        try:
            if isinstance(torrent_file, dict):
                self.torrent_data = torrent_file
            else:
                with open(torrent_file, 'rb') as f:
                    data = f.read()
                    self.torrent_data = bencodepy.decode(data)
        except FileNotFoundError:
            raise FileNotFoundError(f'种子文件 {torrent_file} 不存在')
        except Exception as e:
            raise Exception(f'无法读取种子: {e}')

        if not self.torrent_data:
            raise ValueError('无法解析种子文件')

        self._extract_info()

    def _extract_info(self):
        # 读取 info 字典
        info_dict = self.torrent_data.get(b'info', {})

        # 获取文件名
        self.file_name = info_dict.get(b'name', b'unknown').decode()

        # 是否是私有种子
        self.private = info_dict.get(b'private', False) == 1

        # 文件列表
        files = info_dict.get(b'files', [])

        self.files = []

        # 分块
        self.piece_length = info_dict.get(b'piece length', 0)

        # 分块设置的k，2^k
        self.piece_length_k = int(math.log2(self.piece_length))

        # 块数量
        self.piece_count = int(info_dict.get(b'pieces', b'').__len__() / 20)

        for file in files:
            # 获取文件路径
            path = file.get(b'path', [])
            # 获取文件大小
            length = file.get(b'length', 0)

            # 将文件路径拼接成文件名 PurePosixPath(path1) / path2
            temp_path = None
            for p in path:
                path_str = p.decode()
                if temp_path is None:
                    file_path = path_str
                else:
                    file_path = PurePosixPath(temp_path) / path_str
                temp_path = file_path
            self.files.append(self.File(
                path=temp_path if isinstance(temp_path, str) else temp_path.as_posix(),
                size=length,
                name=temp_path if isinstance(temp_path, str) else temp_path.name
            ))

        # comment
        self.comment = self.torrent_data.get(b'comment', b'').decode()

        self.source = self.torrent_data.get(b'info', {}).get(b'source', b'').decode()

        # created by
        self.created_by = self.torrent_data.get(b'created by', b'').decode()

        # creation date
        self.creation_date = self.torrent_data.get(b'creation date', 0)

        # tracker列表
        trackers_bytes_list = self.torrent_data.get(b'announce-list', [])

        # announce
        self.announce = self.torrent_data.get(b'announce', b'').decode()

        if not trackers_bytes_list:
            self.trackers = [self.announce]

        else:
            self.trackers = []
            for trackers_bytes in trackers_bytes_list:
                for tracker in trackers_bytes:
                    self.trackers.append(tracker.decode())

        # tracker数量
        self.trackers_count = len(self.trackers)

    @property
    def info_hash(self):
        if self._is_info_hash_calculated:
            return self._info_hash
        self._info_hash = hashlib.sha1(bencodepy.encode(self.torrent_data.get(b'info'))).hexdigest()
        return self._info_hash

    def change_announce(self, announce_list):
        self.torrent_data[b'announce-list'] = [[x.encode()] for x in announce_list]
        if len(announce_list) > 1:
            self.torrent_data[b'announce'] = announce_list[0].encode()
        self.trackers = announce_list

    def change_creation_date(self, date):
        self.torrent_data[b'creation date'] = date
        self.creation_date = date

    def change_comment(self, comment):
        self.torrent_data[b'comment'] = comment.encode()
        self.comment = comment
        self._is_info_hash_calculated = False

    def change_source(self, source):
        self.torrent_data[b'info'][b'source'] = source.encode()
        self.source = source
        self._is_info_hash_calculated = False

    def change_created_by(self, created_by):
        self.torrent_data[b'created by'] = created_by.encode()
        self.created_by = created_by

    def change_private(self, private):
        self.torrent_data[b'info'][b'private'] = 1 if private else 0
        self._is_info_hash_calculated = False

    def save(self, save_path):
        try:
            dir_path = os.path.dirname(save_path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path)
            with open(save_path, 'wb') as f:
                f.write(bencodepy.encode(self.torrent_data))
                return True
        except Exception as e:
            logging.error(f'保存种子文件失败: {e}')
            return False


def export_as_torrent(torrent_data, bt_announce_list, source="pt2bt", comment="BT is Best Taste!", is_private=False,
                      created_by="pt2bt v0.1", creation_date=int(time.time()),
                      output_name="", path: str = "") -> tuple[bool, str, 'TorrentFile']:
    export_torrent_file = TorrentFile(copy.deepcopy(torrent_data))

    # 修改 comment
    export_torrent_file.change_comment(comment)
    # 修改 source
    export_torrent_file.change_source(source)

    # 改为非私有
    export_torrent_file.change_private(is_private)

    export_torrent_file.change_announce(bt_announce_list)
    export_torrent_file.change_created_by(created_by)
    export_torrent_file.change_creation_date(creation_date)

    if not output_name:
        output_name = f'[BT].[{export_torrent_file.info_hash[:6].upper()}].{export_torrent_file.file_name}.torrent'
        # 去除非法字符
        output_name = re.sub(r'[\\/:*?"<>|]', '_', output_name)

    result = export_torrent_file.save(os.path.join(path, output_name))

    return result, output_name, export_torrent_file


if __name__ == '__main__':
    main()
