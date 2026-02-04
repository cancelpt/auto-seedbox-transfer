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


def run_manager_loop(manager, name, interval):
    """Run a manager's run method safely in a loop."""
    logger.info(f"Starting {name} loop with interval {interval}s")
    while True:
        try:
            manager.run()
        except Exception as e:
            logger.error(f"Error in {name}: {e}")
        time.sleep(interval)


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
        # Submit tasks with independent intervals
        # Local manager
        executor.submit(run_manager_loop, local_manager, "LocalManager", config.transfer.local_interval)
        # Seedbox manager (Remote interactions)
        executor.submit(run_manager_loop, seedbox_manager, "SeedBoxManager", config.transfer.seedbox_interval)
        # Home manager
        executor.submit(run_manager_loop, home_manager, "HomeManager", config.transfer.home_interval)



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--config_path", type=str, default='config.yaml', help="配置文件路径")
    parser.add_argument("--seed_box_name", type=str, required=True, help="种子盒子名称")
    parser.add_argument("--home_dl_name", type=str, required=True, help="目标的家宽下载器名称")
    parser.add_argument("--target_download_dir", type=str, help="目标下载目录")

    args = parser.parse_args()

    main(args.config_path, args.seed_box_name, args.home_dl_name, args.target_download_dir)
