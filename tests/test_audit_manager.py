from types import SimpleNamespace

from managers.audit_manager import AuditManager
from managers.state_manager import StateManager
from transfer.torrent_transfer import TorrentTransfer
from utils.config import Config, Downloader, SeedBox, Transfer


class FakeClient:
    def __init__(self, torrents):
        self._torrents = list(torrents)
        self.delete_calls = []

    def torrents_info(self, **_kwargs):
        return list(self._torrents)

    def torrents_delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        torrent_hashes = kwargs["torrent_hashes"]
        if isinstance(torrent_hashes, str):
            hashes = {torrent_hashes}
        else:
            hashes = set(torrent_hashes)
        self._torrents = [torrent for torrent in self._torrents if torrent.hash not in hashes]


def torrent(hash_value, name, category, progress=1, state="stalledUP", tags=""):
    return SimpleNamespace(
        hash=hash_value,
        name=name,
        category=category,
        progress=progress,
        state=state,
        save_path="/downloads",
        tags=tags,
    )


def make_config(tmp_path):
    return Config(
        transfer=Transfer(
            original_torrent_path=str(tmp_path / "original"),
            bt_path=str(tmp_path / "bt"),
            torrent_info_path=str(tmp_path / "state.json"),
            bt_trackers=[],
            seedbox_origin_data_missing_policy="pause_transfer",
            seed_box_bt_category="BT",
            home_bt_category="BT",
            home_origin_temp_category="ORIGIN_TEMP",
            home_origin_category="ORIGIN",
        ),
        seed_box=[
            SeedBox(
                name="seedbox_a",
                ssh_host="seed.example",
                incoming_port=60000,
                ssh_user="user",
                ssh_password="pass",
                torrents_path="/remote/torrents",
            )
        ],
        downloaders=[
            Downloader(
                name="seedbox_a",
                url="http://seedbox:8080",
                username="user",
                password="pass",
                source_categories=["SourceA", "SourceB"],
            ),
            Downloader(name="home_a", url="http://home:8080", username="user", password="pass"),
        ],
    )


def test_audit_report_counts_state_categories_and_orphans(tmp_path):
    config = make_config(tmp_path)
    state_manager = StateManager(config.transfer.torrent_info_path)
    state_manager.update(
        TorrentTransfer(
            hash="origin-managed",
            bt_hash="bt-managed",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
            last_error="source missing",
        )
    )
    seed_client = FakeClient(
        [
            torrent("origin-managed", "Managed", "SourceA"),
            torrent("seed-orphan-bt", "Seed orphan", "BT", progress=0, state="pausedDL"),
        ]
    )
    home_client = FakeClient(
        [
            torrent("bt-managed", "Managed", "BT"),
            torrent("home-orphan-bt", "Done Elsewhere", "BT", tags="ast:route:seedbox_a->home_a"),
            torrent("home-orphan-origin", "Done Elsewhere", "ORIGIN_TEMP", tags="ast:route:seedbox_a->home_a"),
        ]
    )

    report = AuditManager(config, state_manager, "seedbox_a", "home_a", seed_client, home_client).build_report()

    assert report["route"] == {"seed_box_name": "seedbox_a", "home_dl_name": "home_a"}
    assert report["source_categories"] == ["SourceA", "SourceB"]
    assert report["state"]["total"] == 1
    assert report["state"]["errors"] == 1
    assert report["seedbox"]["category_counts"]["BT"] == 1
    assert report["home"]["category_counts"]["BT"] == 2
    assert [item["hash"] for item in report["home"]["orphans"]] == ["home-orphan-bt", "home-orphan-origin"]


def test_cleanup_plan_keeps_same_name_without_hash_or_ast_tag_for_manual_review(tmp_path):
    config = make_config(tmp_path)
    state_manager = StateManager(config.transfer.torrent_info_path)
    home_client = FakeClient(
        [
            torrent("home-orphan-bt", "Done Elsewhere", "BT"),
            torrent("manual-review", "Not In TR", "BT"),
        ]
    )
    tr_torrents = [
        {
            "hashString": "origin-hash",
            "name": "Done Elsewhere",
            "percentDone": 1,
            "error": 0,
            "downloadDir": "/Disk3/Downloads/comics",
        }
    ]

    manager = AuditManager(
        config, state_manager, "seedbox_a", "home_a", FakeClient([]), home_client, tr_torrents=tr_torrents
    )
    plan = manager.build_cleanup_plan()

    assert plan["safe_to_remove_from_home_qb"] == []
    assert [item["hash"] for item in plan["needs_manual_review"]] == ["home-orphan-bt", "manual-review"]


def test_cleanup_plan_allows_ast_origin_tag_match_with_complete_tr(tmp_path):
    config = make_config(tmp_path)
    state_manager = StateManager(config.transfer.torrent_info_path)
    home_client = FakeClient(
        [
            torrent(
                "home-orphan-bt",
                "Done Elsewhere",
                "BT",
                tags="ast,ast:origin:origin-hash,ast:bt:home-orphan-bt,ast:route:seedbox_a->home_a",
            ),
        ]
    )
    tr_torrents = [
        {
            "hashString": "origin-hash",
            "name": "Done Elsewhere",
            "percentDone": 1,
            "error": 0,
            "downloadDir": "/Disk3/Downloads/comics",
        }
    ]

    manager = AuditManager(
        config, state_manager, "seedbox_a", "home_a", FakeClient([]), home_client, tr_torrents=tr_torrents
    )
    plan = manager.build_cleanup_plan()

    assert [item["hash"] for item in plan["safe_to_remove_from_home_qb"]] == ["home-orphan-bt"]
    assert plan["safe_to_remove_from_home_qb"][0]["reason"] == "ast_origin_complete_in_tr"


def test_cleanup_plan_allows_same_hash_complete_tr(tmp_path):
    config = make_config(tmp_path)
    state_manager = StateManager(config.transfer.torrent_info_path)
    home_client = FakeClient([torrent("origin-hash", "Done Elsewhere", "ORIGIN_TEMP", state="missingFiles")])
    tr_torrents = [
        {
            "hashString": "origin-hash",
            "name": "Done Elsewhere",
            "percentDone": 1,
            "error": 0,
            "downloadDir": "/Disk3/Downloads/comics",
        }
    ]

    manager = AuditManager(
        config, state_manager, "seedbox_a", "home_a", FakeClient([]), home_client, tr_torrents=tr_torrents
    )
    plan = manager.build_cleanup_plan()

    assert [item["hash"] for item in plan["safe_to_remove_from_home_qb"]] == ["origin-hash"]
    assert plan["safe_to_remove_from_home_qb"][0]["reason"] == "same_hash_complete_in_tr"


def test_cleanup_plan_removes_synced_state_home_bt_residual(tmp_path):
    config = make_config(tmp_path)
    state_manager = StateManager(config.transfer.torrent_info_path)
    state_manager.update(
        TorrentTransfer(
            hash="origin-hash",
            bt_hash="bt-hash",
            origin_torrent_file_path=str(tmp_path / "origin.torrent"),
            bt_torrent_file_path=str(tmp_path / "bt.torrent"),
            is_torrent_in_home_dl=True,
        )
    )
    home_client = FakeClient([torrent("bt-hash", "Managed BT", "BT")])

    manager = AuditManager(config, state_manager, "seedbox_a", "home_a", FakeClient([]), home_client)
    plan = manager.build_cleanup_plan()

    assert [item["hash"] for item in plan["safe_to_remove_from_home_qb"]] == ["bt-hash"]
    assert plan["safe_to_remove_from_home_qb"][0]["reason"] == "state_synced_home_bt_residual"


def test_apply_cleanup_deletes_only_safe_home_qb_tasks_without_files(tmp_path):
    config = make_config(tmp_path)
    state_manager = StateManager(config.transfer.torrent_info_path)
    home_client = FakeClient(
        [
            torrent(
                "home-orphan-bt",
                "Done Elsewhere",
                "BT",
                tags="ast,ast:origin:origin-hash,ast:bt:home-orphan-bt,ast:route:seedbox_a->home_a",
            ),
            torrent("manual-review", "Not In TR", "BT"),
        ]
    )
    tr_torrents = [
        {
            "hashString": "origin-hash",
            "name": "Done Elsewhere",
            "percentDone": 1,
            "error": 0,
            "downloadDir": "/Disk3/Downloads/comics",
        }
    ]

    manager = AuditManager(
        config, state_manager, "seedbox_a", "home_a", FakeClient([]), home_client, tr_torrents=tr_torrents
    )
    result = manager.apply_cleanup()

    assert result["deleted_from_home_qb"] == ["home-orphan-bt"]
    assert home_client.delete_calls == [{"torrent_hashes": "home-orphan-bt", "delete_files": False}]
    assert [torrent.hash for torrent in home_client.torrents_info()] == ["manual-review"]
