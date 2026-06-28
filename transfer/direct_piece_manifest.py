from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from utils.torrent_utils import TorrentFile


@dataclass(frozen=True)
class DirectPieceManifestFile:
    relative_path: str
    remote_path: str
    local_path: str
    length: int
    offset: int


@dataclass(frozen=True)
class DirectPieceManifest:
    schema_version: int
    info_hash: str
    torrent_name: str
    piece_length: int
    piece_count: int
    total_size: int
    remote_root: str
    local_root: str
    is_multi_file: bool
    files: list[DirectPieceManifestFile]

    @property
    def local_payload_path(self) -> str:
        if self.is_multi_file:
            return self.local_root
        return self.files[0].local_path

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "info_hash": self.info_hash,
            "torrent_name": self.torrent_name,
            "piece_length": self.piece_length,
            "piece_count": self.piece_count,
            "total_size": self.total_size,
            "remote_root": self.remote_root,
            "local_root": self.local_root,
            "is_multi_file": self.is_multi_file,
            "files": [asdict(file) for file in self.files],
        }


def build_direct_piece_manifest(
    torrent: TorrentFile,
    remote_save_path: str,
    local_download_path: str,
) -> DirectPieceManifest:
    remote_root = PurePosixPath(remote_save_path)
    local_root = Path(local_download_path)
    if torrent.is_multi_file:
        remote_root = remote_root / torrent.file_name
        local_root = local_root / torrent.file_name

    files = [
        DirectPieceManifestFile(
            relative_path=torrent_file.path,
            remote_path=(remote_root / torrent_file.path).as_posix(),
            local_path=str(local_root / torrent_file.path),
            length=torrent_file.size,
            offset=torrent.file_offsets[file_index],
        )
        for file_index, torrent_file in enumerate(torrent.files)
    ]

    return DirectPieceManifest(
        schema_version=1,
        info_hash=torrent.info_hash,
        torrent_name=torrent.file_name,
        piece_length=torrent.piece_length,
        piece_count=torrent.piece_count,
        total_size=torrent.total_size,
        remote_root=remote_root.as_posix(),
        local_root=str(local_root),
        is_multi_file=torrent.is_multi_file,
        files=files,
    )
