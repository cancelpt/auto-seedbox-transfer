import threading
import time
from types import SimpleNamespace

import main as main_module
from main import run_once_cycle, try_acquire_lock, wait_for_next_run


class Recorder:
    def __init__(self, name, calls, shutdown_event=None, should_shutdown=False):
        self.name = name
        self.calls = calls
        self.shutdown_event = shutdown_event
        self.should_shutdown = should_shutdown

    def run(self):
        self.calls.append(self.name)
        if self.should_shutdown and self.shutdown_event:
            self.shutdown_event.set()


def test_try_acquire_lock_prevents_duplicate_runs(tmp_path):
    lock_path = tmp_path / "job.lock"

    first = try_acquire_lock(str(lock_path))
    second = try_acquire_lock(str(lock_path))

    assert first is not None
    assert second is None
    first.close()


def test_run_once_cycle_uses_bounded_single_process_flow():
    calls = []
    shutdown_event = threading.Event()

    run_once_cycle(
        Recorder("local", calls, shutdown_event=shutdown_event),
        Recorder("seedbox", calls, shutdown_event=shutdown_event),
        Recorder("home", calls, shutdown_event=shutdown_event),
        shutdown_event=shutdown_event,
    )

    assert calls == ["local", "seedbox", "local", "seedbox", "home", "seedbox"]


def test_run_once_cycle_stops_when_shutdown_is_set():
    calls = []
    shutdown_event = threading.Event()

    run_once_cycle(
        Recorder("local", calls, shutdown_event=shutdown_event),
        Recorder("seedbox", calls, shutdown_event=shutdown_event, should_shutdown=True),
        Recorder("home", calls, shutdown_event=shutdown_event),
        shutdown_event=shutdown_event,
    )

    assert calls == ["local", "seedbox"]


def test_main_run_once_acquires_lock_before_state_manager_init(monkeypatch, tmp_path):
    order = []

    config = SimpleNamespace(
        transfer=SimpleNamespace(
            original_torrent_path=str(tmp_path / "downloads"),
            bt_path=str(tmp_path / "bt"),
            torrent_info_path=str(tmp_path / "state.json"),
            local_interval=1,
            seedbox_interval=1,
            home_interval=1,
        )
    )

    class DummyStateManager:
        def __init__(self, _path):
            order.append("state")

    class DummyManager:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self):
            return None

    monkeypatch.setattr(main_module.YAMLConfigHandler, "load", staticmethod(lambda _path: config))
    monkeypatch.setattr(main_module, "ensure_directory_exists", lambda _path: None)
    monkeypatch.setattr(main_module, "try_acquire_lock", lambda _path: order.append("lock") or object())
    monkeypatch.setattr(main_module, "release_lock", lambda _handle: None)
    monkeypatch.setattr(main_module, "StateManager", DummyStateManager)
    monkeypatch.setattr(main_module, "LocalManager", DummyManager)
    monkeypatch.setattr(main_module, "SeedBoxManager", DummyManager)
    monkeypatch.setattr(main_module, "HomeManager", DummyManager)
    monkeypatch.setattr(main_module, "run_once_cycle", lambda *_args, **_kwargs: None)

    main_module.main(
        "config.yaml",
        "seedbox",
        "home",
        "/downloads",
        run_once=True,
    )

    assert order[:2] == ["lock", "state"]


def test_main_run_once_short_circuits_when_lock_not_acquired(monkeypatch, tmp_path):
    order = []

    config = SimpleNamespace(
        transfer=SimpleNamespace(
            original_torrent_path=str(tmp_path / "downloads"),
            bt_path=str(tmp_path / "bt"),
            torrent_info_path=str(tmp_path / "state.json"),
            local_interval=1,
            seedbox_interval=1,
            home_interval=1,
        )
    )

    monkeypatch.setattr(main_module.YAMLConfigHandler, "load", staticmethod(lambda _path: config))
    monkeypatch.setattr(main_module, "ensure_directory_exists", lambda _path: None)
    monkeypatch.setattr(main_module, "try_acquire_lock", lambda _path: None)
    monkeypatch.setattr(main_module, "release_lock", lambda _handle: None)
    monkeypatch.setattr(main_module, "StateManager", lambda _path: order.append("state"))

    main_module.main(
        "config.yaml",
        "seedbox",
        "home",
        "/downloads",
        run_once=True,
    )

    assert order == []


def test_wait_for_next_run_returns_early_when_triggered():
    shutdown_event = threading.Event()
    trigger_event = threading.Event()
    trigger_event.set()

    start = time.monotonic()
    should_stop = wait_for_next_run(
        interval=30,
        shutdown_event=shutdown_event,
        trigger_event=trigger_event,
        poll_interval=0.01,
    )
    elapsed = time.monotonic() - start

    assert should_stop is False
    assert elapsed < 0.5
    assert trigger_event.is_set() is False
