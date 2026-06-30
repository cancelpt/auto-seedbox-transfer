# Auto Seedbox Transfer 安装与运行指南

> 这份文档是写给 AI 助手看的。用户通常会把它和本仓库的 `README.md` 一起交给你，然后让你在本地读取需求、再通过 SSH 去目标机器（常见是 NAS、VPS、Linux 服务器）完成安装、更新和运行。
>
> 默认工作方式：先问清楚，再动手；先读现状，再改配置；先做最小改动，再扩展到定时或自动化。

## 你应该先把自己当成“本地协调器 + 远端执行者”

你不是直接登录到用户桌面就结束了，而是要把用户的意图转换成远端主机上的实际操作。通常流程是：

1. 在本地读 `README.md` 和本文件。
2. 通过 SSH 读取目标主机现状。
3. 先问清用户缺失的信息。
4. 选择最保守、最少破坏的安装/更新路径。
5. 改完后做验证，再决定是否启用定时任务或其他自动化。

如果你发现信息不完整，不要猜。先停下来问用户。

## 先问用户的关键问题

在任何安装或更新前，先确认下面这些问题：

- 这是全新安装，还是更新已有安装？
- 目标主机是什么？是本机、NAS、VPS，还是容器里的 Linux？
- SSH 别名或连接方式是什么？
- 目标机器上是否已经有 `config.yaml`？
- 目标机器上是否已经有 `crontab` 或其他启动项？
- 这次想用哪种运行方式？
  - 单次运行（one-shot）
  - 定时运行（crontab）
  - 进度监控（watch / progress）
  - 只读检查（audit）
  - 残留清理（cleanup-plan / apply-cleanup）
  - 常驻服务/守护进程（如果该版本和环境支持）
- Seedbox 下载器叫什么？本地下载器叫什么？
- 下载目录是什么？
- 是否需要保留当前的标签、分类、下载状态和 torrent 文件？
- 这次是否需要从 Seedbox 侧拉取种子文件或其他资源？如果需要，源路径是什么？
- 是否需要 Transmission RPC 做对账或清理？
- 这次是否涉及代理 / 网络画像（如果有，qB 和 SFTP 是共用一套还是分别配置）？
- 是否需要 direct-piece 进度显示和 watch 模式？

如果其中任何一项不明确，就不要默认。

## 这套项目支持哪些运行方式

先分清两件事：

- 部署形状：cron、手动执行、常驻循环。它决定谁来触发。
- 运行模式：`--audit`、`--run_once`、`--progress/--watch`、`--cleanup-plan`、`--apply-cleanup`。它决定这次做什么。

cron 只是触发方式，不天然等于 `--run_once`。现有 crontab 可能是 `--run_once` 风格，也可能是默认循环配合 `exit_on_finish` 风格；更新前先读现有 crontab 和 `config.yaml` 再改。

| 模式 | 用途 | 备注 |
| --- | --- | --- |
| `--audit` | 只读检查/对账 | 不改状态 |
| `--run_once` | 单次执行后退出 | 最适合放进 cron |
| cron | 外部调度入口 | 可能调用 `--run_once`，也可能调用默认循环 |
| `--progress/--watch` | 实时进度查看 | 主要用于排障或直拉分片进度 |
| `--cleanup-plan` | 只读清理计划 | 先审查再执行 |
| `--apply-cleanup` | 执行已批准的安全清理项 | 默认不删资源文件 |

安装前，先和用户确认要走哪一种或哪几种方式。不要默认只有一种。

### 1. 单次运行（one-shot）

适合：
- 手动验证
- 一次性导入/转移
- 配合 cron 做每次独立执行

特点：
- 运行一次后退出
- 最容易排查
- 最适合先验证配置是否正确

### 2. 定时运行（crontab）

适合：
- 需要定期扫描、同步或转移
- 用户已有 cron 习惯
- 机器长期运行但不想常驻一个前台进程

注意：
- 这类部署常见有两种风格：
  - cron 里直接调用 `--run_once`
  - cron 里调用默认循环，并靠 `exit_on_finish` 决定是否自退出
- 如果目标机器上已经有 cron 条目，优先保留现有风格，不要无脑重写成另一种。
- 如果用户没有明确要求，不要重复添加第二条类似 cron。

### 3. 进度监控（watch / progress）

适合：
- 排障
- 查看 direct-piece 进度
- 验证下载状态变化

特点：
- 更像调试工具，不是默认生产模式
- 需要用户明确想看实时进度时再启用

另外先确认 `data_plane_mode`：

- `qb_bt`：当前默认桥接流程。
- `direct_piece_pull`：另一条模式，只有用户明确要求才切换；需要确认 `direct_piece_workers`、`direct_piece_resume_path`、代理/网络画像，以及是否要 `--progress/--watch`。

### 4. 只读检查（audit）

适合：
- 先看现状，不动数据
- 先确认配置和状态能否被正确读取
- 更新前做健康检查

特点：
- 不应修改任务状态或删除内容
- 如果用户只是说“先看看现在什么情况”，优先用它

### 5. 残留清理（cleanup-plan / apply-cleanup）

适合：
- 需要清理残留任务、标签、文件或对账异常
- 需要先审查哪些项安全，之后再执行

建议流程：
1. 先跑 `--cleanup-plan`
2. 让用户确认计划
3. 再跑 `--apply-cleanup`

不要跳过计划阶段。

### 6. 常驻服务 / 守护进程

只有在用户明确要求，且当前版本/环境支持时才启用。

如果你不确定项目版本是否支持，先问用户，不要把它当默认选项。

## 全新安装流程

如果目标主机上还没有这套项目，按下面顺序做。

### 第一步：确认环境

先确认：
- Python 版本是否满足要求（本项目按 `requirements.txt` 和仓库现状运行）
- 目标路径是否存在或是否需要创建
- 用户是否已经准备好 seedbox / downloader / 下载目录

### 第二步：准备虚拟环境和依赖

在目标主机上创建独立环境，然后安装依赖。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果用户已经有自己的 Python 环境，也可以复用，但你要先问清楚，不要擅自复用系统环境。

### 第三步：准备配置文件

仓库根目录有示例配置：

- `config.example.yaml`

新装时通常要：

```bash
cp config.example.yaml config.yaml
```

然后把真实值填进去。

你应该重点确认这些配置区：

- `transfer`
  - 原始种子目录
  - BT 输出目录
  - 状态文件
  - 运行模式
  - 轮询/重试/恢复相关参数
  - 清理/保留策略
  - `data_plane_mode`
    - `qb_bt` 是默认路径
    - `direct_piece_pull` 不是默认路径，切换前要单独确认对应参数
- `seed_box`
  - Seedbox 连接信息
  - Seedbox 上的 torrent 目录
- `downloaders`
  - 本地下载器连接信息
  - 需要回传或扫描的分类
  - `source_categories` 优先；`want_torrent_category` 仍兼容。现有配置若已经使用旧键，除非用户明确要求迁移，不要顺手改名。

如果用户没说明某个字段，你要停下来问，不要随便填一个看起来合理的默认值。

### 第四步：第一次验证

先做只读检查，再做单次运行。

建议顺序：

```bash
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --audit
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --run_once
```

如果用户要看进度，再加：

```bash
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --progress --watch
```

### 第五步：决定是否启用 cron

只有在单次运行成功后，再决定要不要上定时任务。

如果用户要 cron：
- 先问清楚是“周期性独立执行”还是“常驻逻辑外面再加 flock 防并发”
- 不要默认删除现有 cron
- 不要默认替换现有日志或 lock 文件

## 现有安装 / 更新流程

如果目标主机上已经装过这套项目，默认把它当作“更新”，不要直接重装。

### 第一步：先读现状

先看这三样：

- 当前代码目录
- 当前 `config.yaml`
- 当前 `crontab`

如果目标目录不是 git 仓库，也不要假定能直接 `git pull`。这种情况下，应该从用户本地的最新代码拷贝/同步需要更新的文件。
如果目标目录是 git 仓库，先检查 `git branch --show-current`、`git rev-parse HEAD`、`git status --short`、`git remote -v`。工作树不干净或远端不明确时，不要直接 `git pull`；先保留现状，再决定是局部更新还是同步整仓。

### 第二步：先备份，再修改

更新前，至少备份：
- `config.yaml`
- 当前 cron 记录
- 任何用户自定义脚本或 wrapper

不要直接覆盖，尤其不要覆盖用户已经调好的 crontab。

### 第三步：只改需要改的部分

常见更新分几类：
- 只更新代码
- 只更新配置
- 只更新 cron
- 只更新一次运行命令
- 只更新 cleanup / audit 参数
- 只更新网络 / 代理画像

优先做最小差异，不要顺手重构整套部署。

### 第四步：更新后重新验证

更新之后至少重跑：

```bash
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --audit
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --run_once
```

如果 cron 存在，确认下一次计划执行没有新增重复条目。

## 如果目标主机已经有 crontab

这点非常重要。

如果你看到目标主机上已经有类似：
- `main.py --seed_box_name ... --home_dl_name ...`
- `flock ...`
- 其他用户自定义任务、监控任务或 watchdog 任务

你的默认动作应该是：
1. 先读出来
2. 先理解现有风格
3. 再决定是保留、修改还是替换

不要：
- 直接清空整份 crontab
- 直接追加第二条同类任务
- 直接改变用户已经验证过的执行风格

更新 cron 时，默认保留调度时间、`flock` 锁路径、日志路径、`--run_once` 是否出现，以及 `exit_on_finish` 的配套行为，除非用户明确要求改。

## 配置文件怎么理解

这套项目的配置通常分成三块：

### `transfer`

这一块主要管：
- 输入/输出路径
- 同步节奏
- 保留/删除策略
- 恢复/重试逻辑
- 是否启用额外功能（例如 direct-piece 或 cleanup 相关逻辑）

### `seed_box`

这一块主要管：
- Seedbox 连接方式
- Seedbox 上的 torrent 存放位置
- 需要从哪些分类/目录读取资源

### `downloaders`

这一块主要管：
- 本地下载器的连接方式
- 哪个下载器是最终落地的目标
- 哪些分类需要回传或扫描

如果你不确定某个字段含义，优先问用户，不要自己猜。

## 验收标准

一个“安装成功”的最低标准通常是：

1. 配置能被正确读取
2. `--audit` 正常
3. `--run_once` 能正常跑一轮
4. 如果启用了 cron，下一次调度不会重复启动同一任务
5. 如果启用了 watch/progress，画面能持续刷新且和实际状态一致
6. 如果启用了 cleanup，先有 plan，再执行

如果用户要求更严格的验收，再补充对应测试或现场检查。

## 常见问题

### 1. 配置解析失败

先检查：
- `config.yaml` 是否还保留了示例占位符
- 字段名是否拼错
- 是否把某些值填成了空字符串但实际上必填

### 2. SSH 连不上目标主机

先确认：
- SSH 别名是否存在
- 用户名是否正确
- 端口是否正确
- 是否需要跳板 / ProxyJump
- 是否能通过 BatchMode 免交互登录

### 3. 任务看起来重复跑了

先检查：
- 是否已经存在 cron
- 是否同时启用了另一个常驻进程
- 是否 lock 文件生效

### 4. 下载目录或 torrent 资源不对

先确认：
- 目标路径是否存在
- Seedbox 侧的资源目录是否正确
- 需要的是种子文件、数据路径，还是两者都要

### 5. cleanup 计划看起来会删太多

先停下来，问用户确认。不要直接执行。

### 6. 更新后行为变了

优先比对：
- 旧的 `config.yaml`
- 新的 `config.example.yaml`
- 旧的 cron 行
- 更新后的日志

## 回滚建议

如果更新后有问题：

1. 先恢复 `config.yaml` 备份
2. 恢复原有 crontab 条目
3. 回退最近一次代码同步
4. 再跑一次 `--audit`
5. 再跑一次 `--run_once`

不要在没有备份的情况下做大范围替换。

## 给 AI 的执行原则

当你拿到这份文档时，默认遵守下面几条：

- 先问，不要猜
- 先读现状，不要直接改
- 先保留，再替换；先备份，再覆盖
- 先单次验证，再启用 cron 或其他自动化
- 先审查 cleanup 计划，再执行
- 如果是更新已有安装，默认做增量修改，而不是重装
- 如果用户已经有 crontab、`config.yaml`、部署目录，就把它们当作真相来源

## 相关文件

- `README.md`
- `config.example.yaml`
- `main.py`
- `tests/test_runtime_controls.py`
- `tests/test_audit_manager.py`

## 备注

这份文档是给用户拿去喂给 AI 的，所以重点不是“炫技”，而是“AI 能不能稳稳把安装和更新做对”。
如果用户后续要求更严格的部署风格，再在这份文档基础上增加针对性的安装分支即可。
