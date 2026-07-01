# Auto Seedbox Transfer Installation and Operation Guide

> This document is written for an AI assistant.
> Users may hand you this file with `README.md` and ask you to configure, install, update, or operate this project on a NAS, VPS, or Linux host, usually over SSH.
>
> Default rule: ask first, read current state, make the smallest safe change, then add scheduling or automation only if needed.

## Treat yourself as a local coordinator plus a remote executor

You are not done after reading the repository locally. Translate the user's intent into concrete actions on the target host.

Typical flow:

1. Read `README.md` and this file locally.
2. Inspect the current state of the target host over SSH.
3. Ask the user for any missing information.
4. Choose the least destructive install or update path.
5. Verify the result before enabling cron or other automation.

If key information is missing, ask instead of guessing.

## Questions to confirm first

Before installing or updating, confirm:

- Is this a fresh install or an update to an existing deployment?
- What is the target host: local machine, NAS, VPS, bare Linux, or Linux inside a container?
- What SSH alias or connection method should be used?
- Does the target machine already have `config.yaml`?
- Does the target machine already have `crontab`, a systemd unit, or another startup wrapper?
- Which operating style does the user want this time?
  - one-shot run
  - scheduled run via cron
  - live progress monitoring (`--progress --watch`)
  - read-only audit (`--audit`)
  - cleanup planning or cleanup execution (`--cleanup-plan` / `--apply-cleanup`)
  - daemon/service mode, if the project version and host support it
- What is the seedbox downloader name? What is the local downloader name?
- What are the relevant download paths?
- Should the current labels, categories, downloader state, and torrent files be preserved?
- Does this run need to pull torrent files or other resources from the seedbox side? If yes, from which source path?
- Is Transmission RPC involved for reconciliation or cleanup?
- Does this run depend on a proxy or network profile? If yes, do qBittorrent and SFTP share one profile or use separate ones?
- Does the user want direct-piece progress visibility or watch mode?

If any answer is unclear, do not invent a default.

## Supported deployment shapes and runtime modes

Separate these ideas:

- Deployment shape: manual invocation, cron, or a long-running loop. This decides who starts the process.
- Runtime mode: `--audit`, `--run_once`, `--progress --watch`, `--cleanup-plan`, `--apply-cleanup`. This decides what the process does.

Cron is only a trigger mechanism. It is not automatically the same thing as `--run_once`.
An existing deployment may use cron with `--run_once`, or cron may call the default loop and rely on `exit_on_finish` to self-terminate. Always read the existing cron entry and `config.yaml` before changing either one.

| Mode | Purpose | Notes |
| --- | --- | --- |
| `--audit` | Read-only inspection and reconciliation | Should not mutate state |
| `--run_once` | Run one pass and exit | Usually the safest cron payload |
| cron | External scheduler | May call `--run_once` or the default loop |
| `--progress --watch` | Live progress display | Mostly for debugging and direct-piece visibility |
| `--cleanup-plan` | Dry-run cleanup planning | Review before execution |
| `--apply-cleanup` | Execute approved cleanup actions | Should be used only after review |

Confirm the intended shape and mode before changing the host.

### 1. One-shot execution

Best for:
- manual verification
- one-time imports or transfers
- cron jobs that should run independently and exit

Properties:
- runs once and exits
- easiest to debug
- best first validation path for a new or updated configuration

### 2. Scheduled execution with cron

Best for:
- periodic scanning, synchronization, or transfer
- users who already operate similar jobs with cron
- hosts that stay online but should not keep a foreground process attached

Guardrails:
- two common styles exist:
  - cron calls `--run_once`
  - cron calls the default loop and relies on `exit_on_finish`
- if the target already has a cron entry, preserve the current style unless the user explicitly asks to change it
- do not add a second near-duplicate cron entry unless the user explicitly wants that

### 3. Progress monitoring (`--progress --watch`)

Best for:
- troubleshooting
- watching direct-piece progress
- checking that progress updates match real state

Properties:
- operational or debugging mode, not the default production mode
- enable only when the user explicitly wants live progress output

Also confirm `data_plane_mode` before using watch mode:

- `qb_bt`: the default bridge workflow
- `direct_piece_pull`: an alternate mode; only switch to it when the user explicitly wants it, and confirm `direct_piece_workers`, `direct_piece_resume_path`, proxy/network profile requirements, and whether live progress output is desired

### 4. Read-only audit (`--audit`)

Best for:
- inspecting the current state without changing data
- confirming that configuration and live state can be read correctly
- pre-update health checks

Properties:
- should not change task state or delete content
- if the user says “just inspect the current state first”, start here

### 5. Cleanup planning and execution

Best for:
- cleaning residual tasks, labels, files, or reconciliation anomalies
- reviewing safe cleanup candidates before execution

Recommended flow:
1. run `--cleanup-plan`
2. ask the user to approve the plan
3. run `--apply-cleanup`

Do not skip the planning step.

### 6. Daemon or service mode

Use this only when the user explicitly asks for it and the current project version plus host environment support it. If support is unclear, ask.

## Fresh install workflow

For a new target host, use this order.

### Step 1: confirm the environment

Check:
- whether the Python version is compatible with the repository's current requirements
- whether the target path already exists or should be created
- whether the user already has the seedbox, downloader, and directory layout ready

### Step 2: create an isolated Python environment and install dependencies

On the target host, create an isolated environment and install the repository dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Reuse an existing managed Python environment only after explicit confirmation. Do not silently use a system environment.

### Step 3: prepare the configuration file

The repository provides an example configuration file:

- `config.example.yaml`

A typical fresh install starts with:

```bash
cp config.example.yaml config.yaml
```

Then fill in the real values.

Pay special attention to these configuration areas:

- `transfer`
  - source torrent directory
  - BT output directory
  - state files
  - operating mode
  - poll, retry, and recovery parameters
  - cleanup and retention policy
  - `data_plane_mode`
    - `qb_bt` is the default path
    - `direct_piece_pull` is not the default path; confirm all related knobs before switching
- `seed_box`
  - seedbox connection settings
  - seedbox-side torrent directory
- `downloaders`
  - local downloader connection settings
  - categories to scan or reconcile
  - `source_categories` is preferred; `want_torrent_category` is still supported. If an existing configuration already uses the legacy key, do not rename it casually unless the user explicitly wants migration.

Ask for any missing required field.

### Step 4: first verification

Run a read-only check first, then run a single pass.

Recommended order:

```bash
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --audit
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --run_once
```

If the user wants live progress output, also run:

```bash
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --progress --watch
```

### Step 5: decide whether to enable cron

Only decide on scheduling after the one-shot run succeeds.

If the user wants cron:
- confirm whether the job should be an independent periodic execution or a wrapped execution style with `flock`
- do not delete an existing cron by default
- do not replace an existing lock file path or log path by default

## Existing installation / update workflow

If the target host already has this project, treat it as an update. Do not jump to reinstall.

### Step 1: read the current state first

Inspect these items first:
- current code directory
- current `config.yaml`
- current `crontab`

If the target directory is not a git repository, do not assume `git pull` is available. In that case, update by copying or syncing the required files from the user's known-good local source.

If the target directory is a git repository, inspect at least:
- `git branch --show-current`
- `git rev-parse HEAD`
- `git status --short`
- `git remote -v`

If the worktree is dirty or the remote is unclear, do not run `git pull` blindly. Preserve current state, then choose a minimal file update or repository sync.

### Step 2: back up before editing

Before an update, back up at least:
- `config.yaml`
- the current cron entries
- any user-defined wrapper scripts or helper launchers

Do not overwrite these, especially a user-tuned cron setup.

### Step 3: change only what actually needs to change

Common update classes include:
- code update only
- configuration update only
- cron update only
- one-shot invocation update only
- cleanup or audit parameter update only
- network or proxy profile update only

Prefer the smallest diff. Do not refactor the whole deployment casually.

### Step 4: verify again after the update

After the update, rerun at least:

```bash
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --audit
python main.py --config_path config.yaml --seed_box_name <seed_box_name> --home_dl_name <home_dl_name> --run_once
```

If cron exists, confirm that the next scheduled execution will not create duplicate entries or duplicate workers.

## If the target host already has cron entries

This matters a lot.

If you see existing cron lines involving things like:
- `main.py --seed_box_name ... --home_dl_name ...`
- `flock ...`
- other user-defined jobs, monitoring jobs, or watchdog jobs

Your default behavior should be:
1. read the current entries
2. understand the current style
3. decide whether to preserve, modify, or replace it

Do not:
- wipe the whole crontab blindly
- append a second equivalent job by default
- change an execution style that the user has already validated without asking

When updating cron, preserve the schedule, `flock` path, log path, `--run_once` usage, and any `exit_on_finish` pairing unless the user explicitly wants changes.

## How to think about the configuration file

This project's configuration usually breaks into three areas.

### `transfer`

This section mainly controls:
- input and output paths
- synchronization cadence
- retention or deletion policy
- recovery and retry behavior
- optional features such as direct-piece or cleanup-related logic

### `seed_box`

This section mainly controls:
- seedbox connection details
- where torrents live on the seedbox
- which categories or directories should be read from the source side

### `downloaders`

This section mainly controls:
- how to reach the local downloader
- which downloader is the final landing target
- which categories should be scanned or reconciled

Ask before changing any field you do not understand.

## Acceptance criteria

A minimal install success bar usually means:

1. the configuration can be read correctly
2. `--audit` works
3. `--run_once` can complete one pass
4. if cron is enabled, the next schedule will not duplicate the same job
5. if watch or progress mode is enabled, the output keeps updating and matches real state
6. if cleanup is enabled, a plan is reviewed before execution

If the user wants stricter criteria, add the relevant host checks or runtime validation.

## Common problems

### 1. Configuration parsing fails

Check:
- whether `config.yaml` still contains example placeholders
- whether field names were mistyped
- whether a required value was left as an empty string

### 2. SSH cannot reach the target host

Check:
- whether the SSH alias exists
- whether the username is correct
- whether the port is correct
- whether a jump host or `ProxyJump` is required
- whether non-interactive login works in BatchMode

### 3. The task seems to run twice

Check:
- whether cron already exists
- whether another persistent process is already running
- whether the lock file is effective

### 4. Download paths or torrent resources look wrong

Check:
- whether the target path exists
- whether the seedbox-side resource path is correct
- whether the run needs torrent files, data paths, or both

### 5. The cleanup plan looks too aggressive

Stop and ask the user before execution.

### 6. Behavior changed after an update

Compare:
- the old `config.yaml`
- the new `config.example.yaml`
- the old cron line
- the updated logs

## Rollback guidance

If an update causes problems:

1. restore the `config.yaml` backup
2. restore the previous cron entries
3. roll back the most recent code sync
4. run `--audit` again
5. run `--run_once` again

Do not make broad replacements without a backup.

## Execution principles for an AI assistant

When you receive this document, follow these defaults:

- ask first; do not guess
- read the current state before making changes
- preserve before replacing; back up before overwriting
- validate with a one-shot run before enabling cron or other automation
- review the cleanup plan before executing cleanup
- if this is an update to an existing install, prefer incremental change over reinstall
- if the user already has a deployment directory, `config.yaml`, or cron entries, treat them as the source of truth

## Related files

- `README.md`
- `config.example.yaml`
- `main.py`
- `tests/test_runtime_controls.py`
- `tests/test_audit_manager.py`

## Final note

This file is meant to be handed to an AI assistant by a real user. The goal is not to sound clever. The goal is to help the AI perform installation and update work correctly, conservatively, and repeatably.
If the user later wants a stricter or more specialized deployment style, extend this document with targeted branches instead of replacing the whole workflow.
