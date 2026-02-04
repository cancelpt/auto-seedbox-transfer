import threading
import argparse
import logging
import time
import sys
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from managers.home_manager import HomeManager
from managers.local_manager import LocalManager
from managers.seedbox_manager import SeedBoxManager
from managers.state_manager import StateManager
from utils.config import YAMLConfigHandler, Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def run_manager_loop(manager, name, interval, shutdown_event):
    """Run a manager's run method safely in a loop."""
    logger.info(f"Starting {name} loop with interval {interval}s")
    while not shutdown_event.is_set():
        try:
            manager.run()
        except Exception as e:
            logger.error(f"Error in {name}: {e}")
        
        if shutdown_event.wait(interval):
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


def main(config_path, seed_box_name, home_dl_name, target_download_dir):
    # Load configuration
    config: Config = YAMLConfigHandler.load(config_path)
    
    # Validate and create directories
    ensure_directory_exists(config.transfer.original_torrent_path)
    ensure_directory_exists(config.transfer.bt_path)
    
    # Ensure torrent info path directory exists
    torrent_info_path = Path(config.transfer.torrent_info_path)
    if torrent_info_path.parent:
        ensure_directory_exists(str(torrent_info_path.parent))
    
    # Initialize State Manager
    state_manager = StateManager(config.transfer.torrent_info_path)
    
    shutdown_event = threading.Event()
    
    # Initialize Business Logic Managers
    local_manager = LocalManager(config, state_manager)
    seedbox_manager = SeedBoxManager(config, state_manager, seed_box_name, home_dl_name, shutdown_event)
    home_manager = HomeManager(config, state_manager, seed_box_name, home_dl_name, target_download_dir)
    
    logger.info(f"Starting Seedbox Transfer Helper...")
    logger.info(f"Seedbox: {seed_box_name}")
    logger.info(f"Home Downloader: {home_dl_name}")
    
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit tasks with independent intervals
        # Local manager
        executor.submit(run_manager_loop, local_manager, "LocalManager", config.transfer.local_interval, shutdown_event)
        # Seedbox manager (Remote interactions)
        executor.submit(run_manager_loop, seedbox_manager, "SeedBoxManager", config.transfer.seedbox_interval, shutdown_event)
        # Home manager
        executor.submit(run_manager_loop, home_manager, "HomeManager", config.transfer.home_interval, shutdown_event)
        
        try:
            while not shutdown_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutdown signal received (Ctrl+C). Stopping threads...")
            shutdown_event.set()



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--config_path", type=str, default='config.yaml', help="配置文件路径")
    parser.add_argument("--seed_box_name", type=str, required=True, help="种子盒子名称")
    parser.add_argument("--home_dl_name", type=str, required=True, help="目标的家宽下载器名称")
    parser.add_argument("--target_download_dir", type=str, help="目标下载目录")

    args = parser.parse_args()

    main(args.config_path, args.seed_box_name, args.home_dl_name, args.target_download_dir)
