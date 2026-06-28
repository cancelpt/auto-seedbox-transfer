from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from managers.state_manager import StateManager
from utils.config import Config, validate_route_config


class AuditManager:
    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        seed_box_name: str,
        home_dl_name: str,
        seedbox_client,
        home_client,
        tr_torrents: Optional[List[Dict[str, Any]]] = None,
    ):
        self.config = config
        self.state_manager = state_manager
        self.seed_box_name = seed_box_name
        self.home_dl_name = home_dl_name
        self.seedbox_client = seedbox_client
        self.home_client = home_client
        self.tr_torrents = tr_torrents or []
        self.seedbox_downloader, self.home_downloader = validate_route_config(config, seed_box_name, home_dl_name)

    def build_report(self) -> Dict[str, Any]:
        state_by_origin = self.state_manager.get_all()
        state_origin_hashes = set(state_by_origin.keys())
        state_bt_hashes = {state.bt_hash for state in state_by_origin.values() if state.bt_hash}
        seedbox_torrents = self._list_torrents(self.seedbox_client)
        home_torrents = self._list_torrents(self.home_client)

        return {
            "route": {
                "seed_box_name": self.seed_box_name,
                "home_dl_name": self.home_dl_name,
            },
            "source_categories": sorted(self.seedbox_downloader.get_source_categories()),
            "state": self._state_summary(state_by_origin),
            "seedbox": self._downloader_summary(
                seedbox_torrents,
                managed_hashes=state_origin_hashes | state_bt_hashes,
                managed_categories=self._seedbox_managed_categories(),
            ),
            "home": self._downloader_summary(
                home_torrents,
                managed_hashes=state_origin_hashes | state_bt_hashes,
                managed_categories=self._home_managed_categories(),
            ),
        }

    def build_progress_report(self) -> Dict[str, Any]:
        state_by_origin = self.state_manager.get_all()
        home_torrents = self._list_torrents(self.home_client)
        route_tag = f"ast:route:{self.seed_box_name}->{self.home_dl_name}"
        home_by_hash = {getattr(torrent, "hash", ""): torrent for torrent in home_torrents}

        transfers = []
        for info_hash, state in state_by_origin.items():
            if state.is_torrent_in_home_dl:
                continue

            snapshot = self._load_progress_snapshot(info_hash)
            if snapshot is None and not self._state_needs_progress_reporting(state, home_by_hash, route_tag):
                continue

            snapshot = snapshot or self._build_state_only_progress_snapshot(info_hash, state)
            if snapshot is None:
                continue

            normalized = self._normalize_progress_snapshot(snapshot, info_hash, state)
            if normalized is None:
                continue
            transfers.append(normalized)

        transfers.sort(key=lambda item: (self._progress_state_rank(item["state"]), item["name"], item["info_hash"]))

        summary = {
            "total": len(transfers),
            "running": sum(1 for item in transfers if item["state"] == "running"),
            "stalled": sum(1 for item in transfers if item["state"] == "stalled"),
            "completed_ready_not_imported": sum(1 for item in transfers if item["state"] == "completed"),
            "failed": sum(1 for item in transfers if item["state"] == "failed"),
        }

        return {
            "route": {
                "seed_box_name": self.seed_box_name,
                "home_dl_name": self.home_dl_name,
            },
            "transfers": transfers,
            "summary": summary,
        }

    def build_cleanup_plan(self) -> Dict[str, Any]:
        report = self.build_report()
        tr_complete_by_hash = self._complete_tr_by_hash()
        safe_to_remove = []
        manual_review = []
        state_by_origin = self.state_manager.get_all()
        home_torrents = self._list_torrents(self.home_client)

        for torrent in home_torrents:
            for state in state_by_origin.values():
                if (
                    state.is_torrent_in_home_dl
                    and state.bt_hash
                    and getattr(torrent, "hash", "") == state.bt_hash
                    and getattr(torrent, "category", "") == self.config.transfer.home_bt_category
                ):
                    cleanup_item = self._torrent_row(torrent)
                    cleanup_item["reason"] = "state_synced_home_bt_residual"
                    safe_to_remove.append(cleanup_item)

        for item in report["home"]["orphans"]:
            cleanup_item = dict(item)
            tr_match = tr_complete_by_hash.get(item["hash"].lower())
            if tr_match:
                cleanup_item["reason"] = "same_hash_complete_in_tr"
                cleanup_item["tr_hash"] = tr_match.get("hashString")
                cleanup_item["tr_download_dir"] = tr_match.get("downloadDir")
                safe_to_remove.append(cleanup_item)
                continue

            ast_origin_hash = self._extract_ast_tag_value(item.get("tags", ""), "ast:origin:")
            tr_match = tr_complete_by_hash.get(ast_origin_hash.lower()) if ast_origin_hash else None
            if tr_match:
                cleanup_item["reason"] = "ast_origin_complete_in_tr"
                cleanup_item["tr_hash"] = tr_match.get("hashString")
                cleanup_item["tr_download_dir"] = tr_match.get("downloadDir")
                safe_to_remove.append(cleanup_item)
                continue

            same_name_match = self._complete_tr_by_name().get(item["name"])
            if same_name_match:
                cleanup_item["reason"] = "same_name_only_requires_manual_review"
                cleanup_item["tr_hash"] = same_name_match.get("hashString")
                cleanup_item["tr_download_dir"] = same_name_match.get("downloadDir")
                manual_review.append(cleanup_item)
            else:
                cleanup_item["reason"] = "orphan_without_verified_external_completion"
                manual_review.append(cleanup_item)

        return {
            "route": report["route"],
            "safe_to_remove_from_home_qb": self._dedupe_cleanup_items(safe_to_remove),
            "needs_manual_review": manual_review,
        }

    def apply_cleanup(self) -> Dict[str, Any]:
        plan = self.build_cleanup_plan()
        deleted_hashes = []
        for item in plan["safe_to_remove_from_home_qb"]:
            self.home_client.torrents_delete(torrent_hashes=item["hash"], delete_files=False)
            deleted_hashes.append(item["hash"])
        return {
            "route": plan["route"],
            "deleted_from_home_qb": deleted_hashes,
            "delete_files": False,
            "needs_manual_review": plan["needs_manual_review"],
        }

    def _state_summary(self, state_by_origin) -> Dict[str, int]:
        states = list(state_by_origin.values())
        return {
            "total": len(states),
            "errors": sum(1 for state in states if state.last_error),
            "skipped": sum(1 for state in states if state.is_skipped),
            "synced": sum(1 for state in states if state.is_torrent_in_home_dl),
            "bt_in_seedbox": sum(1 for state in states if state.is_bt_in_seed_box),
            "bt_in_home": sum(1 for state in states if state.is_bt_in_home_dl),
        }

    def _downloader_summary(self, torrents, managed_hashes: Set[str], managed_categories: Set[str]) -> Dict[str, Any]:
        counts = Counter(getattr(torrent, "category", "") for torrent in torrents)
        orphans = [
            self._torrent_row(torrent)
            for torrent in torrents
            if (
                getattr(torrent, "category", "") in managed_categories
                and getattr(torrent, "hash", "") not in managed_hashes
            )
        ]
        orphans.sort(key=lambda item: (item["category"], item["name"], item["hash"]))
        return {
            "category_counts": {category: counts.get(category, 0) for category in sorted(managed_categories)},
            "orphans": orphans,
        }

    def _seedbox_managed_categories(self) -> Set[str]:
        return self.seedbox_downloader.get_source_categories() | {self.config.transfer.seed_box_bt_category}

    def _home_managed_categories(self) -> Set[str]:
        return {
            self.config.transfer.home_bt_category,
            self.config.transfer.home_origin_temp_category,
            self.config.transfer.home_origin_category,
        }

    def _complete_tr_by_name(self) -> Dict[str, Dict[str, Any]]:
        return {item.get("name"): item for item in self.tr_torrents if self._tr_item_is_complete(item)}

    def _complete_tr_by_hash(self) -> Dict[str, Dict[str, Any]]:
        return {
            item.get("hashString", "").lower(): item
            for item in self.tr_torrents
            if self._tr_item_is_complete(item)
        }

    @staticmethod
    def _tr_item_is_complete(item: Dict[str, Any]) -> bool:
        return item.get("name") and item.get("percentDone") == 1 and item.get("error") == 0

    @staticmethod
    def _list_torrents(client):
        return list(client.torrents_info() or [])

    @staticmethod
    def _extract_ast_tag_value(tags: str, prefix: str) -> str:
        for tag in (tag.strip() for tag in (tags or "").split(",")):
            if tag.startswith(prefix):
                return tag[len(prefix) :]
        return ""

    @staticmethod
    def _dedupe_cleanup_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped = []
        seen = set()
        for item in items:
            item_hash = item.get("hash")
            if item_hash in seen:
                continue
            seen.add(item_hash)
            deduped.append(item)
        deduped.sort(key=lambda item: (item.get("category", ""), item.get("name", ""), item.get("hash", "")))
        return deduped

    @staticmethod
    def _torrent_row(torrent) -> Dict[str, Any]:
        return {
            "hash": getattr(torrent, "hash", ""),
            "name": getattr(torrent, "name", ""),
            "category": getattr(torrent, "category", ""),
            "state": getattr(torrent, "state", ""),
            "progress": float(getattr(torrent, "progress", 0) or 0),
            "save_path": getattr(torrent, "save_path", ""),
            "tags": getattr(torrent, "tags", ""),
        }

    def _state_needs_progress_reporting(self, state, home_by_hash: Dict[str, Any], route_tag: str) -> bool:
        if state.is_direct_payload_ready or state.last_error:
            return True
        home_torrent = home_by_hash.get(state.hash)
        if home_torrent is None:
            return False
        tags = getattr(home_torrent, "tags", "") or ""
        return route_tag in {tag.strip() for tag in tags.split(",") if tag.strip()}

    def _load_progress_snapshot(self, info_hash: str) -> Optional[Dict[str, Any]]:
        resume_dir = getattr(self.config.transfer, "direct_piece_resume_path", None)
        if not resume_dir:
            return None
        resume_path = Path(resume_dir)
        status_path = resume_path / f"{info_hash}.status.json"
        if status_path.exists():
            return self._read_json_file(status_path)
        manifest_path = resume_path / f"{info_hash}.manifest.json"
        pieces_path = resume_path / f"{info_hash}.pieces"
        if not manifest_path.exists() or not pieces_path.exists():
            return None
        manifest = self._read_json_file(manifest_path)
        if manifest is None:
            return None
        return self._reconstruct_progress_from_resume_artifacts(info_hash, manifest, pieces_path)

    def _build_state_only_progress_snapshot(self, info_hash: str, state) -> Optional[Dict[str, Any]]:
        if state.is_direct_payload_ready:
            return {
                "info_hash": info_hash,
                "name": Path(state.origin_torrent_file_path).stem or info_hash,
                "state": "completed",
                "piece_count": None,
                "completed_pieces": None,
                "remaining_pieces": None,
                "total_bytes": None,
                "completed_bytes": None,
                "percent": 100.0,
                "workers": None,
                "started_at": None,
                "updated_at": None,
                "last_piece_completed_at": None,
                "seconds_since_last_progress": None,
                "bytes_per_second_recent": None,
                "bytes_per_second_average": None,
                "eta_seconds": None,
                "local_root": state.direct_payload_root or "",
                "remote_root": "",
                "last_error": state.last_error or "",
            }
        if state.last_error:
            return {
                "info_hash": info_hash,
                "name": Path(state.origin_torrent_file_path).stem or info_hash,
                "state": "failed",
                "piece_count": None,
                "completed_pieces": None,
                "remaining_pieces": None,
                "total_bytes": None,
                "completed_bytes": None,
                "percent": None,
                "workers": None,
                "started_at": None,
                "updated_at": None,
                "last_piece_completed_at": None,
                "seconds_since_last_progress": None,
                "bytes_per_second_recent": None,
                "bytes_per_second_average": None,
                "eta_seconds": None,
                "local_root": state.direct_payload_root or "",
                "remote_root": "",
                "last_error": state.last_error,
            }
        return None

    def _reconstruct_progress_from_resume_artifacts(
        self,
        info_hash: str,
        manifest: Dict[str, Any],
        pieces_path: Path,
    ) -> Dict[str, Any]:
        piece_states = pieces_path.read_bytes()
        piece_count = int(manifest.get("piece_count") or len(piece_states))
        completed_pieces = sum(1 for value in piece_states[:piece_count] if value == 1)
        total_bytes = int(manifest.get("total_size") or 0)
        completed_bytes = self._completed_bytes_from_piece_states(
            piece_states=piece_states[:piece_count],
            piece_length=int(manifest.get("piece_length") or 0),
            total_bytes=total_bytes,
        )
        percent = self._compute_percent(completed_bytes, total_bytes)
        updated_at = pieces_path.stat().st_mtime
        started_at = manifest.get("created_at")
        seconds_since_last_progress = max(0.0, time.time() - updated_at)

        return {
            "info_hash": info_hash,
            "name": manifest.get("torrent_name") or info_hash,
            "state": "running",
            "piece_count": piece_count,
            "completed_pieces": completed_pieces,
            "remaining_pieces": max(piece_count - completed_pieces, 0),
            "total_bytes": total_bytes,
            "completed_bytes": completed_bytes,
            "percent": percent,
            "workers": None,
            "started_at": started_at,
            "updated_at": updated_at,
            "last_piece_completed_at": updated_at,
            "seconds_since_last_progress": seconds_since_last_progress,
            "bytes_per_second_recent": None,
            "bytes_per_second_average": None,
            "eta_seconds": None,
            "local_root": manifest.get("local_root", ""),
            "remote_root": manifest.get("remote_root", ""),
            "last_error": "",
        }

    def _normalize_progress_snapshot(self, snapshot: Dict[str, Any], info_hash: str, state) -> Optional[Dict[str, Any]]:
        normalized = {
            "info_hash": snapshot.get("info_hash") or info_hash,
            "name": snapshot.get("name") or Path(state.origin_torrent_file_path).stem or info_hash,
            "state": snapshot.get("state") or ("failed" if state.last_error else "running"),
            "piece_count": self._int_or_none(snapshot.get("piece_count")),
            "completed_pieces": self._int_or_none(snapshot.get("completed_pieces")),
            "remaining_pieces": self._int_or_none(snapshot.get("remaining_pieces")),
            "total_bytes": self._int_or_none(snapshot.get("total_bytes")),
            "completed_bytes": self._int_or_none(snapshot.get("completed_bytes")),
            "percent": self._float_or_none(snapshot.get("percent")),
            "workers": self._int_or_none(snapshot.get("workers")),
            "started_at": self._float_or_none(snapshot.get("started_at")),
            "updated_at": self._float_or_none(snapshot.get("updated_at")),
            "last_piece_completed_at": self._float_or_none(snapshot.get("last_piece_completed_at")),
            "seconds_since_last_progress": self._float_or_none(snapshot.get("seconds_since_last_progress")),
            "bytes_per_second_recent": self._float_or_none(snapshot.get("bytes_per_second_recent")),
            "bytes_per_second_average": self._float_or_none(snapshot.get("bytes_per_second_average")),
            "eta_seconds": self._float_or_none(snapshot.get("eta_seconds")),
            "local_root": snapshot.get("local_root") or state.direct_payload_root or "",
            "remote_root": snapshot.get("remote_root") or "",
            "last_error": snapshot.get("last_error") or state.last_error or "",
        }

        if normalized["state"] == "completed" and state.is_torrent_in_home_dl:
            return None
        return normalized

    @staticmethod
    def _completed_bytes_from_piece_states(piece_states: bytes, piece_length: int, total_bytes: int) -> int:
        if total_bytes <= 0 or piece_length <= 0 or not piece_states:
            return 0
        completed_bytes = 0
        piece_count = len(piece_states)
        last_piece_length = total_bytes % piece_length or piece_length
        for piece_index, status in enumerate(piece_states):
            if status != 1:
                continue
            if piece_index == piece_count - 1:
                completed_bytes += last_piece_length
            else:
                completed_bytes += piece_length
        return min(total_bytes, completed_bytes)

    @staticmethod
    def _compute_percent(completed_bytes: int, total_bytes: int) -> Optional[float]:
        if total_bytes <= 0:
            return None
        return round((completed_bytes / total_bytes) * 100, 2)

    @staticmethod
    def _int_or_none(value):
        if value is None or value == "":
            return None
        return int(value)

    @staticmethod
    def _float_or_none(value):
        if value is None or value == "":
            return None
        return float(value)

    @staticmethod
    def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _progress_state_rank(state: str) -> int:
        order = {
            "failed": 0,
            "completed": 1,
            "running": 2,
            "stalled": 3,
        }
        return order.get(state, 99)


def fetch_transmission_torrents(url: str, username: str = "", password: str = "") -> List[Dict[str, Any]]:
    if not url:
        return []

    payload = json.dumps(
        {
            "method": "torrent-get",
            "arguments": {
                "fields": [
                    "id",
                    "hashString",
                    "name",
                    "downloadDir",
                    "percentDone",
                    "status",
                    "error",
                    "errorString",
                    "totalSize",
                ]
            },
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    session_id = None
    for _ in range(2):
        request_headers = dict(headers)
        if session_id:
            request_headers["X-Transmission-Session-Id"] = session_id
        request = urllib.request.Request(url, data=payload, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode())
                if result.get("result") != "success":
                    raise RuntimeError(result)
                return result["arguments"]["torrents"]
        except urllib.error.HTTPError as error:
            if error.code == 409:
                session_id = error.headers.get("X-Transmission-Session-Id")
                continue
            raise
    raise RuntimeError("Transmission session negotiation failed")
