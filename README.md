# Auto Seedbox Transfer

这是一个用于在盒子（Seedbox）和本地下载器之间同步和管理种子的工具。它支持从盒子下载种子文件（依赖于 ssh sftp），以及将盒子上特定分类的种子自动下载到本地下载器。

仅支持 qBittorrent 下载器（4.3.9~4.6.7）。

## 环境要求

- Python 3.8+
- 依赖库列表见 `requirements.txt`

### 安装依赖

```bash
pip install -r requirements.txt
```

## 工作流程

- 一个完整的工作流通常是由盒子上的下载器选定了一部分种子需要回传到本地下载器，此时将这部分种子分类改为**指定分类**。

- 脚本运行时，会自动**通过 sftp 文件传输**从盒子下载指定分类的种子文件**同时补齐 tracker 信息**，然后转换成 BT 种子文件，**用于盒子和本地下载器传输**。

- 脚本先向盒子添加 BT 种子，再向本地下载器添加 BT 种子，此时如果顺利，那么本地下载器则会**与盒子 P2P 传输**。

- 等待本地下载器完成 BT 种子下载后，脚本会先将**原始种子辅种**在本地下载器，然后**删除本地的 BT 种子**（不删资源文件）。

- 最后脚本会将原始种子和 BT 种子从盒子上删除。

## 快速开始

1. **配置环境**
   复制 `config.example.yaml` 为 `config.yaml`，并填写正确的盒子和下载器信息。

   ```bash
   cp config.example.yaml config.yaml
   ```

   假设你盒子通过 Vertex 自动删种，并且设置了`keep`和`To`分类的种子不会被删除，其中`To`是你想回传到本地下载器的种子分类，那么配置`seed_box_bt_category`为`keep`用于临时 BT 种子分类，配置`want_torrent_category`为`To`用于回传的种子分类。

   BT 的 tracker 列表通常不是必须的，因为当你配置了`ssh host`和`incoming_port`时，脚本会自动添加盒子的 peer 信息到本地下载器的每个 BT 种子。

   `home_origin_temp_category`和`home_origin_category`影响不大，选择你喜欢的分类即可，用于方便在本地下载器上区分哪些种子是由盒子回传的。

   `auto_dl_torrent_from_seedbox`设置为`True`时，脚本会自动从盒子下载种子文件，否则需要手动从盒子导出种子文件或者你本地就有种子文件，总之那就放在`original_torrent_path`里。

   `exit_on_finish`设置为`True`时，脚本会在所有种子都完成回传后自动退出，否则脚本会持续运行监测。

   注意，对于盒子下载器`seed_box`配置项内的`name`与`downloaders`配置项内的`name`必须一致时，脚本才能正常工作。`torrents_path`是盒子上的种子文件存放路径，对于大部分盒子，这个路径通常是 `/home/{username}/.local/share/qBittorrent/BT_backup`。`incoming_port`是盒子的传入端口，如果没有配置`bt_trackers`，那么请确保传入端口可正确，而不是随机。

  `downloaders`配置项内`want_torrent_category`对于本地下载器不需要配置。

2. **运行程序**
   使用 `main.py` 启动程序，只需指定盒子名称和本地下载器名称（需与配置文件中一致）。

  **参数说明:**

  - `--seed_box_name`: (必填) 盒子名称。
  - `--home_dl_name`: (必填) 目标的家宽下载器名称。
  - `--target_download_dir`: (选填) 目标下载目录，如果不配置，则默认使用家宽下载器的下载目录。
  - `--config_path`: (选填) 配置文件路径，默认为 `config.yaml`。


   ```bash
   python main.py --seed_box_name remote-qb --home_dl_name home-qb --target_download_dir /Disk1/Downloads/seedbox
   ```

   程序启动后会自动：
   - 扫描本地 `downloads` 目录下的种子。
   - 转换并在本地 qBittorrent 添加 BT 任务。
   - 监控盒子上的任务，自动回传完成的种子。

