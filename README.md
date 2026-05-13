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

   假设你盒子通过 Vertex 自动删种，并且**设置了`BT`和`To`分类的种子不会被删除**，其中`To`是你想**回传到本地下载器的种子分类**，那么配置`seed_box_bt_category`为`BT`用于临时 BT 种子分类，配置`want_torrent_category`为`To`用于回传的种子分类。
   
   `want_torrent_category  `: 支持单个字符串或列表（如 `["To", "Too"]`，或者就是`"To"`），脚本会监控这些分类下的所有种子。

   `seed_box_ignore_complete_time`: 设置种子完成多长时间后才开始转移（单位：秒）。如果种子完成时间小于该值，将被忽略。如果此时没有其他可处理的种子，脚本会提前退出（需开启 `exit_on_finish`）。
  
   `seed_box_keep_torrent`: 默认为 `False`（转移后删除盒子上的原始种子）。如果设为 `True`，则不会删除，而是将其移动到 `seed_box_keep_torrent_category` 指定的分类，允许分类为`""`即空值，这样原始种子的分类会被删除。通常来说可以配合 Vertex 来自动删种这些已经转移的原始种子，并且延迟删种多混一些上传，直到 Vertex 的删种规则触发。

   `seedbox_origin_data_missing_policy`: 必填。用于控制盒子 qBittorrent 任务仍存在、但 qB 状态为`missingFiles`或资源文件不可用时的行为。`pause_transfer`会阻断该任务并等待人工处理，不删除、不重校验、不重建；`skip_transfer`会标记跳过，不删除远端任务或文件；`force_recheck_and_rebuild_bt`会删除错误的 BT 任务但不删除文件，对原始种子执行重新校验，等待原始种恢复后再重新投递 BT。该配置必须显式填写，避免升级后在无人确认的情况下执行破坏性动作。

   BT 的 tracker 列表通常不是必须的，因为当你配置了`ssh host`和`incoming_port`时，脚本会**自动添加盒子的 peer 信息到本地下载器的每个 BT 种子**。

   `pause_after_add_origin`: 默认为 `False`（添加原始种子后直接开始本地下载器做种）。如果设为 `True`，则添加原始种子后暂停，等待用户手动做种。

   `home_origin_tags`: 默认不添加标签，如果你使用一些转移做种插件，它们通常要你配置转移做种的种子标签，此时可以配置这个选项。

   `home_origin_temp_category`和`home_origin_category`影响不大，选择你喜欢的分类即可，用于方便在本地下载器上区分哪些种子是由盒子回传的。

   `auto_dl_torrent_from_seedbox`设置为`True`时，脚本会自动从盒子下载种子文件，否则需要手动从盒子导出种子文件或者你本地就有种子文件，总之**就放在`original_torrent_path`里**。

   如果本地已存在的原始`.torrent`无法严格解析，脚本不会仅因为文件存在就跳过下载；对于“有效 bencode 后存在尾随垃圾”的文件，会记录明确日志并在开启`auto_dl_torrent_from_seedbox`时尝试从盒子重新下载覆盖。新下载的临时`.torrent`必须能被解析后才会替换本地最终文件，避免把损坏传输结果落盘成正式原种。

   `exit_on_finish`设置为`True`时，脚本会在所有种子都完成回传后自动退出，否则脚本会持续运行监测。

   脚本会复用 qBittorrent 登录会话，并优先通过 qBittorrent 的`sync/maindata`增量快照维护下载器状态；如果客户端或接口不支持增量同步，会自动回退到`torrents_info()`全量列表，保证兼容性。

   脚本会把回传任务状态、盒子源可用性和相关失败次数持久化到`torrent_info_path`。对于盒子删种、远端`.torrent`文件丢失、添加 BT/原始种失败等异常情况，同一条已进入回传状态的任务连续失败 3 次后会被自动标记为跳过，避免无限重试；对于 qB 任务存在但资源文件缺失的情况，会按`seedbox_origin_data_missing_policy`处理，`is_bt_in_seed_box`只表示盒子 BT 源当前可用，不再仅表示 qB 任务存在。如需重新尝试，删除对应状态文件记录后再运行即可。开启`exit_on_finish`时，已标记跳过的任务不会阻止程序退出。

   注意，对于盒子下载器`seed_box`配置项内的`name`与`downloaders`配置项内的 **`name`必须一致时**，脚本才能正常工作。
   
   `torrents_path`是盒子上的**种子文件存放路径**，对于大部分盒子，这个路径通常是 `/home/{username}/.local/share/qBittorrent/BT_backup`。`incoming_port`是盒子的传入端口，**如果没有配置`bt_trackers`，那么请确保传入端口可正确，而不是随机**。

   `downloaders`配置项内`want_torrent_category`对于本地下载器不需要配置。

1. **运行程序**
   
   使用 `main.py` 启动程序，只需指定盒子名称和本地下载器名称（需与配置文件中一致）。

    **参数说明:**

    - `--seed_box_name`: (必填) 盒子名称。
    - `--home_dl_name`: (必填) 目标的本地下载器名称。
    - `--target_download_dir`: (选填) 目标下载目录，如果不配置，则默认使用本地下载器的下载目录。
    - `--config_path`: (选填) 配置文件路径，默认为 `config.yaml`。
    - `--run_once`: (选填) 单次执行并退出，同时使用`{torrent_info_path}.lock`避免定时任务并发重复运行；适合放到 cron。脚本还会生成内部状态文件锁`{torrent_info_path}.state.lock`，这是正常的并发保护文件。


    ```bash
    python main.py --seed_box_name remote-qb --home_dl_name home-qb --target_download_dir /Disk1/Downloads/seedbox
    ```

    如果要放到定时任务里，推荐使用：

    ```bash
    python main.py --seed_box_name remote-qb --home_dl_name home-qb --target_download_dir /Disk1/Downloads/seedbox --run_once
    ```

    这里，`remote-qb`：盒子下载器名称，`home-qb`：本地下载器名称，`/Disk1/Downloads/seedbox`：回传的下载目录。

    程序启动后会自动：
    - 扫描本地 `downloads` 目录下的种子。
    - 转换并在本地 qBittorrent 添加 BT 任务。
    - 监控盒子上的任务，自动回传完成的种子。
