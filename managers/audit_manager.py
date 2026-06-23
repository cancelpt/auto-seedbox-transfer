from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from collections import Counter
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
