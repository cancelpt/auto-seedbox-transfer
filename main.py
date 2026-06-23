import argparse
import fcntl
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from managers.audit_manager import AuditManager, fetch_transmission_torrents
from managers.home_manager import HomeManager
from managers.local_manager import LocalManager
from managers.seedbox_manager import SeedBoxManager
from managers.state_manager import StateManager
from utils.config import Config, YAMLConfigHandler, resolve_downloader_network_profile, validate_route_config
from utils.downloader_utils import get_downloader_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def wait_for_next_run(interval, shutdown_event, trigger_event=None, poll_interval=0.5):
    """Wait until the next run, but wake up early when triggered."""
    if trigger_event is None:
        return shutdown_event.wait(interval)

    deadline = time.monotonic() + interval
    while not shutdown_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if trigger_event.wait(timeout=min(poll_interval, remaining)):
            trigger_event.clear()
            return False
    return True


def run_manager_loop(manager, name, interval, shutdown_event, trigger_event=None):
    """Run a manager's run method safely in a loop."""
    logger.info(f"Starting {name} loop with interval {interval}s")
    while not shutdown_event.is_set():
        try:
            manager.run()
        except Exception as e:
            logger.error(f"Error in {name}: {e}")

        if wait_for_next_run(interval, shutdown_event, trigger_event):
            break
    logger.info(f"{name} loop stopped.")


def ensure_directory_exists(path_str):
    """Ensure a directory exists, create it if not. Exit on failure."""
    if not path_str:
        return

    path = Path(path_str)
    try:
        if not path.exists():
            logger.info(f"Directory does not exist, creating: {path.absolute()}")
            path.mkdir(parents=True, exist_ok=True)
        elif not path.is_dir():
            logger.critical(f"Path exists but is not a directory: {path.absolute()}")
            sys.exit(1)
    except Exception as e:
        logger.critical(f"Failed to create directory {path}: {e}")
        sys.exit(1)


def try_acquire_lock(lock_path):
    """Acquire a non-blocking process lock. Return file handle or None when locked."""
    ensure_directory_exists(str(Path(lock_path).parent))
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def release_lock(lock_file):
    if not lock_file:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def run_once_cycle(local_manager, seedbox_manager, home_manager, shutdown_event=None):
    """Run a bounded single-process workflow suitable for cron."""
    cycle = [
        local_manager,
        seedbox_manager,
        local_manager,
        seedbox_manager,
        home_manager,
        seedbox_manager,
    ]
    for manager in cycle:
        if shutdown_event and shutdown_event.is_set():
            break
        manager.run()


def create_downloader_clients(config: Config, seed_box_name: str, home_dl_name: str):
    seedbox_downloader, home_downloader = validate_route_config(config, seed_box_name, home_dl_name)
    seedbox_client = get_downloader_client(
        downloader=seedbox_downloader,
        network_profile=resolve_downloader_network_profile(config, seedbox_downloader),
    ).client
    home_client = get_downloader_client(
        downloader=home_downloader,
        network_profile=resolve_downloader_network_profile(config, home_downloader),
    ).client
    return seedbox_client, home_client


def main(
    config_path,
    seed_box_name,
    home_dl_name,
    target_download_dir,
    run_once=False,
    audit=False,
    cleanup_plan=False,
    apply_cleanup=False,
    transmission_rpc_url="",
    transmission_username="",
    transmission_password="",
):
    # Load configuration
    config: Config = YAMLConfigHandler.load(config_path)
    validate_route_config(config, seed_box_name, home_dl_name)

    # Validate and create directories
    ensure_directory_exists(config.transfer.original_torrent_path)
    ensure_directory_exists(config.transfer.bt_path)

    # Ensure torrent info path directory exists
    torrent_info_path = Path(config.transfer.torrent_info_path)
    if torrent_info_path.parent:
        ensure_directory_exists(str(torrent_info_path.parent))

    lock_file = None
    if run_once or apply_cleanup:
        lock_path = f"{config.transfer.torrent_info_path}.lock"
        lock_file = try_acquire_lock(lock_path)
        if lock_file is None:
            logger.info(f"Another run is already active, skipping this invocation: {lock_path}")
            if apply_cleanup:
                print(
                    json.dumps(
                        {
                            "skipped": True,
                            "reason": f"another run is already active: {lock_path}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            return

    try:
        if audit or cleanup_plan or apply_cleanup:
            state_manager = StateManager(config.transfer.torrent_info_path)
            seedbox_client, home_client = create_downloader_clients(config, seed_box_name, home_dl_name)
            tr_torrents = fetch_transmission_torrents(
                transmission_rpc_url,
                transmission_username,
                transmission_password,
            )
            audit_manager = AuditManager(
                config,
                state_manager,
                seed_box_name,
                home_dl_name,
                seedbox_client,
                home_client,
                tr_torrents=tr_torrents,
            )
            if apply_cleanup:
                result = audit_manager.apply_cleanup()
            elif cleanup_plan:
                result = audit_manager.build_cleanup_plan()
            else:
                result = audit_manager.build_report()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        # Initialize State Manager after lock acquisition, so run_once never loads stale state.
        state_manager = StateManager(config.transfer.torrent_info_path)

        shutdown_event = threading.Event()

        # Trigger events for cross-thread communication
        trigger_local = threading.Event()
        trigger_seedbox = threading.Event()
        trigger_home = threading.Event()

        # Initialize Business Logic Managers
        local_manager = LocalManager(
            config,
            state_manager,
            trigger_seedbox=trigger_seedbox,
            trigger_home=trigger_home,
        )
        seedbox_manager = SeedBoxManager(
            config,
            state_manager,
            seed_box_name,
            home_dl_name,
            shutdown_event,
            trigger_local=trigger_local,
            trigger_home=trigger_home,
            async_downloads=not run_once,
        )
        home_manager = HomeManager(
            config,
            state_manager,
            seed_box_name,
            home_dl_name,
            target_download_dir,
            trigger_seedbox=trigger_seedbox,
        )

        logger.info("Starting Seedbox Transfer Helper...")
        logger.info(f"Seedbox: {seed_box_name}")
        logger.info(f"Home Downloader: {home_dl_name}")

        if run_once:
            logger.info("Run-once mode enabled. Processing one bounded cycle and exiting.")
            run_once_cycle(local_manager, seedbox_manager, home_manager, shutdown_event=shutdown_event)
            return

        with ThreadPoolExecutor(max_workers=3) as executor:
            # Submit tasks with independent intervals
            # Local manager
            executor.submit(
                run_manager_loop,
                local_manager,
                "LocalManager",
                config.transfer.local_interval,
                shutdown_event,
                trigger_local,
            )
            # Seedbox manager (Remote interactions)
            executor.submit(
                run_manager_loop,
                seedbox_manager,
                "SeedBoxManager",
                config.transfer.seedbox_interval,
                shutdown_event,
                trigger_seedbox,
            )
            # Home manager
            executor.submit(
                run_manager_loop,
                home_manager,
                "HomeManager",
                config.transfer.home_interval,
                shutdown_event,
                trigger_home,
            )

            try:
                while not shutdown_event.is_set():
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Shutdown signal received (Ctrl+C). Stopping threads...")
                shutdown_event.set()
    finally:
        release_lock(lock_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config_path", type=str, default="config.yaml", help="配置文件路径")
    parser.add_argument("--seed_box_name", type=str, required=True, help="种子盒子名称")
    parser.add_argument("--home_dl_name", type=str, required=True, help="目标的家宽下载器名称")
    parser.add_argument("--target_download_dir", type=str, help="目标下载目录")
    parser.add_argument(
        "--run_once",
        action="store_true",
        help="单次执行并退出，同时通过状态文件锁避免定时任务并发重复运行",
    )
    parser.add_argument("--audit", action="store_true", help="只读输出 AST/qB 对账报告，不执行同步")
    parser.add_argument("--cleanup-plan", action="store_true", help="只读输出可清理残留计划，不删除任务")
    parser.add_argument("--apply-cleanup", action="store_true", help="执行 cleanup plan 中安全项，默认不删除文件")
    parser.add_argument("--transmission_rpc_url", type=str, default="", help="可选 Transmission RPC URL")
    parser.add_argument("--transmission_username", type=str, default="", help="可选 Transmission 用户名")
    parser.add_argument("--transmission_password", type=str, default="", help="可选 Transmission 密码")

    args = parser.parse_args()

    main(
        args.config_path,
        args.seed_box_name,
        args.home_dl_name,
        args.target_download_dir,
        args.run_once,
        audit=args.audit,
        cleanup_plan=args.cleanup_plan,
        apply_cleanup=args.apply_cleanup,
        transmission_rpc_url=args.transmission_rpc_url,
        transmission_username=args.transmission_username,
        transmission_password=args.transmission_password,
    )
