import json
import os
from types import SimpleNamespace

import managers.audit_manager as audit_manager_module
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


def make_config(tmp_path, *, direct_mode=False):
    transfer_kwargs = {
        "original_torrent_path": str(tmp_path / "original"),
        "bt_path": str(tmp_path / "bt"),
        "torrent_info_path": str(tmp_path / "state.json"),
        "bt_trackers": [],
        "seedbox_origin_data_missing_policy": "pause_transfer",
        "seed_box_bt_category": "BT",
        "home_bt_category": "BT",
        "home_origin_temp_category": "ORIGIN_TEMP",
        "home_origin_category": "ORIGIN",
    }
    if direct_mode:
        transfer_kwargs["data_plane_mode"] = "direct_piece_pull"
        transfer_kwargs["direct_piece_resume_path"] = str(tmp_path / "resume")

    return Config(
        transfer=Transfer(**transfer_kwargs),
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


def write_progress_status(resume_dir, info_hash, **overrides):
    payload = {
        "info_hash": info_hash,
        "name": f"name-{info_hash}",
        "state": "running",
        "piece_count": 4,
        "completed_pieces": 1,
        "remaining_pieces": 3,
        "total_bytes": 16,
        "completed_bytes": 4,
        "percent": 25.0,
        "workers": 2,
        "started_at": 10.0,
        "updated_at": 20.0,
        "last_piece_completed_at": 18.0,
        "seconds_since_last_progress": 2.0,
        "bytes_per_second_recent": 1.5,
        "bytes_per_second_average": 1.0,
        "eta_seconds": 8.0,
        "local_root": f"/local/{info_hash}",
        "remote_root": f"/remote/{info_hash}",
        "last_error": "",
    }
    payload.update(overrides)
    status_path = resume_dir / f"{info_hash}.status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def write_progress_manifest_and_pieces(
    resume_dir,
    info_hash,
    *,
    torrent_name="album",
    piece_length=4,
    total_size=10,
    pieces=b"\x01\x00\x01",
    created_at=100.0,
    updated_at=100.0,
):
    manifest = {
        "schema_version": 1,
        "info_hash": info_hash,
        "torrent_name": torrent_name,
        "piece_length": piece_length,
        "piece_count": len(pieces),
        "total_size": total_size,
        "remote_root": f"/remote/{torrent_name}",
        "local_root": f"/local/{torrent_name}",
        "is_multi_file": False,
        "files": [
            {
                "relative_path": f"{torrent_name}.bin",
                "remote_path": f"/remote/{torrent_name}/{torrent_name}.bin",
                "local_path": f"/local/{torrent_name}/{torrent_name}.bin",
                "length": total_size,
                "offset": 0,
            }
        ],
        "created_at": created_at,
        "updated_at": updated_at,
    }
    resume_dir.mkdir(parents=True, exist_ok=True)
    (resume_dir / f"{info_hash}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pieces_path = resume_dir / f"{info_hash}.pieces"
    pieces_path.write_bytes(pieces)
    return manifest, pieces_path


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


def test_build_progress_report_reads_status_sidecars_and_summarizes_states(tmp_path):
    config = make_config(tmp_path, direct_mode=True)
    state_manager = StateManager(config.transfer.torrent_info_path)
    resume_dir = tmp_path / "resume"

    state_manager.update(
        TorrentTransfer(
            hash="running-hash",
            origin_torrent_file_path=str(tmp_path / "running.torrent"),
        )
    )
    state_manager.update(
        TorrentTransfer(
            hash="stalled-hash",
            origin_torrent_file_path=str(tmp_path / "stalled.torrent"),
        )
    )
    state_manager.update(
        TorrentTransfer(
            hash="ready-hash",
            origin_torrent_file_path=str(tmp_path / "ready.torrent"),
            is_direct_payload_ready=True,
            direct_payload_root="/downloads/ready",
        )
    )
    state_manager.update(
        TorrentTransfer(
            hash="failed-hash",
            origin_torrent_file_path=str(tmp_path / "failed.torrent"),
            last_error="network timeout",
        )
    )
    state_manager.update(
        TorrentTransfer(
            hash="imported-hash",
            origin_torrent_file_path=str(tmp_path / "imported.torrent"),
            is_direct_payload_ready=True,
            is_torrent_in_home_dl=True,
            direct_payload_root="/downloads/imported",
        )
    )

    write_progress_status(resume_dir, "running-hash", state="running", percent=25.0)
    write_progress_status(resume_dir, "stalled-hash", state="stalled", percent=50.0)
    write_progress_status(resume_dir, "ready-hash", state="completed", percent=100.0)
    write_progress_status(
        resume_dir,
        "failed-hash",
        state="failed",
        percent=10.0,
        last_error="network timeout",
    )
    write_progress_status(resume_dir, "imported-hash", state="completed", percent=100.0)

    report = AuditManager(config, state_manager, "seedbox_a", "home_a", FakeClient([]), FakeClient([])).build_progress_report()

    assert report["route"] == {"seed_box_name": "seedbox_a", "home_dl_name": "home_a"}
    assert report["summary"] == {
        "total": 4,
        "running": 1,
        "stalled": 1,
        "completed_ready_not_imported": 1,
        "failed": 1,
    }
    assert [item["info_hash"] for item in report["transfers"]] == [
        "failed-hash",
        "ready-hash",
        "running-hash",
        "stalled-hash",
    ]
    assert report["transfers"][0]["state"] == "failed"
    assert report["transfers"][0]["last_error"] == "network timeout"
    assert all(item["info_hash"] != "imported-hash" for item in report["transfers"])


def test_build_progress_report_reconstructs_from_manifest_and_pieces_when_status_missing(monkeypatch, tmp_path):
    config = make_config(tmp_path, direct_mode=True)
    state_manager = StateManager(config.transfer.torrent_info_path)
    resume_dir = tmp_path / "resume"

    state_manager.update(
        TorrentTransfer(
            hash="resume-hash",
            origin_torrent_file_path=str(tmp_path / "resume.torrent"),
        )
    )
    _manifest, pieces_path = write_progress_manifest_and_pieces(
        resume_dir,
        "resume-hash",
        torrent_name="resume-album",
        pieces=b"\x01\x00\x01",
        created_at=100.0,
        updated_at=101.0,
    )
    os.utime(pieces_path, (150.0, 150.0))
    monkeypatch.setattr(audit_manager_module.time, "time", lambda: 200.0)

    report = AuditManager(config, state_manager, "seedbox_a", "home_a", FakeClient([]), FakeClient([])).build_progress_report()

    assert report["summary"] == {
        "total": 1,
        "running": 1,
        "stalled": 0,
        "completed_ready_not_imported": 0,
        "failed": 0,
    }
    assert report["transfers"] == [
        {
            "info_hash": "resume-hash",
            "name": "resume-album",
            "state": "running",
            "piece_count": 3,
            "completed_pieces": 2,
            "remaining_pieces": 1,
            "total_bytes": 10,
            "completed_bytes": 6,
            "percent": 60.0,
            "workers": None,
            "started_at": 100.0,
            "updated_at": 150.0,
            "last_piece_completed_at": 150.0,
            "seconds_since_last_progress": 50.0,
            "bytes_per_second_recent": None,
            "bytes_per_second_average": None,
            "eta_seconds": None,
            "local_root": "/local/resume-album",
            "remote_root": "/remote/resume-album",
            "last_error": "",
        }
    ]


def test_build_progress_report_reconstructs_from_manifest_and_pieces_when_status_sidecar_is_unreadable(
    monkeypatch, tmp_path
):
    config = make_config(tmp_path, direct_mode=True)
    state_manager = StateManager(config.transfer.torrent_info_path)
    resume_dir = tmp_path / "resume"

    state_manager.update(
        TorrentTransfer(
            hash="corrupt-status-hash",
            origin_torrent_file_path=str(tmp_path / "corrupt-status.torrent"),
        )
    )
    _manifest, pieces_path = write_progress_manifest_and_pieces(
        resume_dir,
        "corrupt-status-hash",
        torrent_name="corrupt-status-album",
        pieces=b"\x01\x00\x01",
        created_at=100.0,
        updated_at=101.0,
    )
    (resume_dir / "corrupt-status-hash.status.json").write_text("{not-json", encoding="utf-8")
    os.utime(pieces_path, (150.0, 150.0))
    monkeypatch.setattr(audit_manager_module.time, "time", lambda: 200.0)

    report = AuditManager(config, state_manager, "seedbox_a", "home_a", FakeClient([]), FakeClient([])).build_progress_report()

    assert report["summary"] == {
        "total": 1,
        "running": 1,
        "stalled": 0,
        "completed_ready_not_imported": 0,
        "failed": 0,
    }
    assert report["transfers"] == [
        {
            "info_hash": "corrupt-status-hash",
            "name": "corrupt-status-album",
            "state": "running",
            "piece_count": 3,
            "completed_pieces": 2,
            "remaining_pieces": 1,
            "total_bytes": 10,
            "completed_bytes": 6,
            "percent": 60.0,
            "workers": None,
            "started_at": 100.0,
            "updated_at": 150.0,
            "last_piece_completed_at": 150.0,
            "seconds_since_last_progress": 50.0,
            "bytes_per_second_recent": None,
            "bytes_per_second_average": None,
            "eta_seconds": None,
            "local_root": "/local/corrupt-status-album",
            "remote_root": "/remote/corrupt-status-album",
            "last_error": "",
        }
    ]
