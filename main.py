import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from managers.home_manager import HomeManager
from managers.local_manager import LocalManager
from managers.seedbox_manager import SeedBoxManager
from managers.state_manager import StateManager
from utils.config import YAMLConfigHandler, Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def run_manager_safely(manager, name):
    """Run a manager's run method and log exceptions."""
    try:
        # logger.debug(f"Starting {name} task...")
        manager.run()
        # logger.debug(f"Finished {name} task.")
    except Exception as e:
        logger.error(f"Error in {name}: {e}")


def main(config_path, seed_box_name, home_dl_name, target_download_dir):
    # Load configuration
    config: Config = YAMLConfigHandler.load(config_path)
    
    # Initialize State Manager
    state_manager = StateManager(config.transfer.torrent_info_path)
    
    # Initialize Business Logic Managers
    local_manager = LocalManager(config, state_manager)
    seedbox_manager = SeedBoxManager(config, state_manager, seed_box_name, home_dl_name)
    home_manager = HomeManager(config, state_manager, seed_box_name, home_dl_name, target_download_dir)
    
    logger.info(f"Starting Seedbox Transfer Helper...")
    logger.info(f"Seedbox: {seed_box_name}")
    logger.info(f"Home Downloader: {home_dl_name}")
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        while True:
            futures = []
            
            # Submit tasks
            futures.append(executor.submit(run_manager_safely, local_manager, "LocalManager"))
            futures.append(executor.submit(run_manager_safely, seedbox_manager, "SeedBoxManager"))
            futures.append(executor.submit(run_manager_safely, home_manager, "HomeManager"))
            
            # Wait for all tasks to complete (for this simplified loop version)
            # You could also use as_completed or just fire and forget if they are truly independent loops
            # But synchronizing via StateManager suggests waiting is safer to avoid race conditions on the lock excessively
            for future in futures:
                future.result()
            
            # Sleep interval
            # logger.info("Cycle completed. Sleeping...")
            time.sleep(10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--config_path", type=str, default='config.yaml', help="配置文件路径")
    parser.add_argument("--seed_box_name", type=str, required=True, help="种子盒子名称")
    parser.add_argument("--home_dl_name", type=str, required=True, help="目标的家宽下载器名称")
    parser.add_argument("--target_download_dir", type=str, help="目标下载目录")

    args = parser.parse_args()

    main(args.config_path, args.seed_box_name, args.home_dl_name, args.target_download_dir)
