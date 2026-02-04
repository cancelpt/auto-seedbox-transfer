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

## 配置说明

项目主要通过 `config.yaml` 进行配置。配置文件主要包含以下几个部分：

### 1. 传输配置 (`transfer`)

```yaml
transfer:
  original_torrent_path: ./downloads  # 原始种子存放路径
  bt_path: ../bt                      # 转换后的 BT 种子存放路径
  torrent_info_path: torrent_info.json # 种子传输状态记录文件
  max_once_add: 10                    # 每次最大添加种子数，防止瞬间添加过多导致拥堵
  seed_box_bt_category: 'keep'        # 盒子 BT 种子分类，请确保不会被vertex之类的脚本自动删种
  home_bt_category: 'BT'              # 家宽 BT 种子分类
  home_origin_temp_category: 'ORIGIN_TEMP' # 家宽 临时原始种子分类，在 BT 种子还没被移除时的原始种子分类
  home_origin_category: 'ORIGIN'      # 家宽 原始种子分类，在 BT 种子被移除后的原始种子分类，表示转移完成
  bt_trackers:                        # BT 种子使用的 tracker 列表，请确保盒子和家宽都能正常访问
    - http://tracker1
    - http://tracker2
```

### 2. 种子盒子配置 (`seed_box`)

配置种子盒子的 SSH 连接信息和路径。

```yaml
seed_box:
- name: nc                          # 盒子名称
  ssh_host: 123.123.123.123         # SSH 主机 IP
  ipv6: ...                         # IPv6 地址 (可选)
  incoming_port: 12345              # 传入端口
  ssh_user: root                    # SSH 用户名
  ssh_password: ...                 # SSH 密码
  torrents_path: /path/to/backup    # 盒子上的种子路径
```

### 3. 下载器配置 (`downloaders`)

配置 qBittorrent 下载器的连接信息。

```yaml
downloaders:
- name: home-qb                     # 下载器名称
  url: http://127.0.0.1:8080        # WebUI 地址
  username: username                # WebUI 用户名
  password: password                # WebUI 密码
  want_torrent_category: To         # 期望的种子分类
```

## 使用说明

### 1. 获取种子文件 (`fetch_torrent_file.py`)

该脚本用于通过 SFTP 从种子盒子下载指定分类的种子文件。

**命令格式:**

```bash
python fetch_torrent_file.py --seed_box_name <NAME> --category <CATEGORY> --torrent_dir <DIR> [--config_path <PATH>]
```

**参数说明:**

- `--seed_box_name`: (必填) 种子盒子名称，需与配置文件中一致。
- `--category`: (必填) 需要导出的种子分类。
- `--torrent_dir`: (必填) 本地保存种子文件的目录。
- `--config_path`: (选填) 配置文件路径，默认为 `config.yaml`。

**示例:**

```bash
python fetch_torrent_file.py --seed_box_name nc --category To --torrent_dir ./new_torrents
```

### 2. 种子盒子助手 (`seed_box_helper.py`)

该脚本用于持续监控和管理种子状态，将盒子上的种子自动下载到本地下载器。

**命令格式:**

```bash
python seed_box_helper.py --seed_box_name <NAME> --home_dl_name <NAME> [--target_download_dir <DIR>] [--config_path <PATH>]
```

**参数说明:**

- `--seed_box_name`: (必填) 种子盒子名称。
- `--home_dl_name`: (必填) 目标的家宽下载器名称。
- `--target_download_dir`: (选填) 目标下载目录。
- `--config_path`: (选填) 配置文件路径，默认为 `config.yaml`。

**示例:**

```bash
python seed_box_helper.py --seed_box_name nc --home_dl_name home-qb --target_download_dir /downloads/temp
```
