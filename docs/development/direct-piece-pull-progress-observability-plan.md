# Direct Piece Pull Progress & Stall Observability Plan

> For Hermes/tmux Codex: implement this slice in pane-visible Codex lanes. Controller-side repo edits are limited to this development doc, tracked prompt docs, supervision, verification, merge orchestration, and cleanup of task-local disposable worktrees/watchers.

Goal: make direct-piece-pull transfers operationally observable for CLI users and operators, so a long-running direct payload pull can be distinguished as healthy-progressing, stalled, failed, or completed without reading raw `.pieces` sidecars by hand.

Architecture: keep the current transfer workflow state coarse (`TorrentTransfer` + `StateManager`), keep piece durability in the existing manifest + `.pieces` sidecars, and add a separate throttled observability projection for humans and operational tools. The downloader emits progress events, a telemetry projector turns those events into read-model snapshots, and both periodic logs and read-only CLI/audit surfaces consume that same snapshot format.

Tech stack: existing Python 3.10 codebase, qbittorrent-api, paramiko SFTP, existing `StateManager`, direct-piece engine modules, pytest.

---

## Why this slice exists

Current direct-piece-pull behavior is correct but not operationally legible:
- payload bytes can be moving with no human-readable progress output
- `.pieces` encodes truth, but only as an internal resume bitmap
- a user cannot easily tell “slow but healthy” from “stalled”
- operators must infer health from sidecar growth, file mtimes, or final completion logs

This is an observability gap, not a transport-correctness gap.

---

## Design principles

1. Separate workflow truth from observability truth.
   - `torrent_info.json` remains coarse workflow state.
   - manifest + `.pieces` remain the durable correctness layer.
   - human-readable transfer progress becomes a separate read model.

2. Do not rewrite coarse JSON state on every piece.
   - `StateManager` rewrites the whole file; that is the wrong place for high-frequency progress.

3. The read model must be reconstructible.
   - if a process crashes, progress can be recovered from manifest + `.pieces`.
   - the observability sidecar is a projection, not the source of correctness.

4. Logs and query surfaces must read from the same schema.
   - avoid one format for logs and another for `--progress` / audit.

5. Stall detection is a first-class state transition, not a grep heuristic.
   - “no verified piece progress for N seconds” should become explicit telemetry state.

6. This slice must not reopen the BT bridge path.
   - no seedbox BT bridge reintroduction
   - no home BT bridge reintroduction
   - no peer injection in direct mode

---

## Domain model and patterns

### 1) Read-model projection pattern

Introduce a direct-piece telemetry projection sidecar, for example under the same resume directory:
- `${direct_piece_resume_path}/{info_hash}.status.json`

This file is a throttled, human-readable projection derived from:
- manifest metadata
- `.pieces` completion truth
- live downloader runtime metrics

It is not the source of transfer correctness.

### 2) Observer pattern

`DirectPieceDownloader` should not know about CLI, logs, or audit.
It should emit progress notifications through an observer/callback interface, for example:
- `on_start(snapshot)`
- `on_piece_verified(piece_index, snapshot)`
- `on_periodic_snapshot(snapshot)`
- `on_stalled(snapshot)`
- `on_resumed(snapshot)`
- `on_failed(snapshot)`
- `on_completed(snapshot)`

The exact callback surface can be a single `progress_callback(event)` if that fits the codebase better, but the separation of concerns must remain.

### 3) Policy/strategy separation

Telemetry policy should be explicit and configurable where operationally necessary:
- progress log interval
- stall timeout

Do not over-expose every internal throttle as config. Keep flush heuristics internal unless they materially affect operations.

### 4) State machine

The telemetry layer should use explicit runtime states such as:
- `running`
- `stalled`
- `completed`
- `failed`

Optional `idle` / `resuming` can be added only if they simplify actual logic and tests.

---

## Proposed telemetry schema

Add a new module, for example:
- `transfer/direct_piece_telemetry.py`

Suggested snapshot fields:
- `info_hash`
- `name`
- `state` (`running|stalled|completed|failed`)
- `piece_count`
- `completed_pieces`
- `remaining_pieces`
- `total_bytes`
- `completed_bytes`
- `percent`
- `workers`
- `started_at`
- `updated_at`
- `last_piece_completed_at`
- `seconds_since_last_progress`
- `bytes_per_second_recent`
- `bytes_per_second_average`
- `eta_seconds`
- `local_root`
- `remote_root`
- `last_error`

Notes:
- `completed_bytes` should be derived from verified pieces, not file size alone.
- `eta_seconds` may be `null` when throughput is not yet meaningful.
- `bytes_per_second_recent` should be based on a rolling interval, not lifetime average only.

---

## Runtime behavior contract

### During active direct transfer

The system should periodically emit a human-readable log line such as:
- info hash / short name
- `completed_pieces / piece_count`
- `completed_bytes / total_bytes`
- percent
- current recent speed
- ETA
- state (`running` or `stalled`)

### On stall

If no new verified piece completes for `direct_piece_stall_timeout_seconds`, the system should:
- transition the telemetry state to `stalled`
- write a fresh telemetry snapshot
- emit a warning log once on the transition

### On resume from stall

When a new verified piece arrives after a stalled state, the system should:
- transition back to `running`
- emit a resume log
- update the telemetry snapshot

### On completion

When the torrent finishes:
- write final telemetry state `completed`
- keep the final snapshot queryable until overwritten by a new run or cleaned up intentionally
- existing `Direct payload ready for ...` completion summary remains valid

### On failure

When the downloader raises:
- write final telemetry state `failed`
- carry forward `last_error`
- preserve enough fields for `--progress` / audit to explain where it died

---

## Query surface

The project needs a read-only operational query path that does not require tailing cron logs.

Preferred shape:
- extend `main.py` with a read-only `--progress` mode
- reuse existing route args (`--seed_box_name`, `--home_dl_name`) so the report is route-scoped
- build the report through a manager/helper rather than embedding logic directly in `main.py`

Good fit options:
1. extend `AuditManager` with `build_progress_report()`
2. or add a dedicated small progress-report helper used by `main.py`

Either is acceptable, but avoid duplicating selection/filter logic in multiple places.

The report should include:
- route identifiers
- active direct transfers by state
- per-transfer progress snapshots
- a summary count (`running`, `stalled`, `completed-ready-not-imported`, `failed`)

---

## Config surface

Add only operationally meaningful knobs to `Transfer` in `utils/config.py`:
- `direct_piece_progress_log_interval_seconds: int = 30`
- `direct_piece_stall_timeout_seconds: int = 180`

Do not add speculative tuning knobs for every flush detail.

Keep telemetry files under the existing `direct_piece_resume_path`; do not create a second unrelated directory unless implementation proves it necessary.

---

## File-level implementation map

### A. Writer-side telemetry slice
Files likely touched:
- `utils/config.py`
- `transfer/direct_piece_downloader.py`
- `transfer/direct_piece_resume.py` (only if helper hooks are needed)
- `transfer/direct_piece_telemetry.py` (new)
- `managers/direct_transfer_manager.py`
- tests: new focused telemetry tests + direct engine/runtime tests

Responsibilities:
- define telemetry snapshot/store/projector
- emit progress events from the downloader
- perform periodic progress logging
- detect stall/resume transitions
- write throttled `.status.json` snapshots

### B. Reader / ops surface slice
Files likely touched:
- `main.py`
- `managers/audit_manager.py` or a small dedicated progress-report helper
- tests around CLI/audit/read-model behavior

Responsibilities:
- expose progress snapshots through a read-only command surface
- summarize state for operators without reading raw sidecars
- keep route scoping consistent with existing audit behavior

### C. Baseline fix precondition
Before this observability slice branches out, preserve the already-verified direct-mode runtime fix as a clean base commit:
- direct-mode `run_once` ordering should be bounded so HomeManager runs before direct backlog monopolizes the cycle
- `DirectTransferManager` must respect `max_once_add`
- tests for that base behavior must be committed before parallel observability work starts

---

## Test strategy

### Writer-side telemetry tests
Add focused tests for:
- snapshot creation from manifest + initial `.pieces`
- periodic snapshot updates as pieces complete
- completed bytes / percent calculation
- stall transition after timeout without new verified pieces
- resumed transition after a stalled state receives a new verified piece
- failure snapshot emission when downloader raises
- final completed snapshot emission

### Reader-side tests
Add focused tests for:
- `--progress` or progress-report builder output format
- aggregation by route/state
- reading telemetry sidecars when coarse workflow state already says `is_direct_payload_ready`
- behavior when status sidecar is missing but manifest + `.pieces` exist

### Regression tests
Re-run and preserve direct-mode invariants:
- no seedbox BT bridge add in direct mode
- no home BT bridge add in direct mode
- no peer injection in direct mode
- existing run_once direct ordering/base tests remain green

### Verification commands
At integration/review time, at minimum run:
- focused telemetry tests
- direct engine tests
- runtime controls tests
- home/seedbox direct-mode tests

And before merge to `main`, re-run at least:
- `pytest -q tests/test_runtime_controls.py tests/test_home_retries.py tests/test_seedbox_retries.py`
- plus the new telemetry-focused test module(s)

---

## Parallel work split

### Baseline lane (source main worktree)
Purpose:
- commit the already-proven direct-mode run_once / `max_once_add` fix
- commit this development doc and the tracked Codex prompt docs so later worktrees inherit them cleanly

### Lane A: writer-side telemetry
Purpose:
- telemetry domain model
- throttled status sidecar projection
- downloader callbacks / monitor thread
- runtime log + stall detection wiring

### Lane B: reader / ops surface
Purpose:
- progress report reader
- `main.py` read-only progress command surface
- audit/progress aggregation tests

### Lane C: integration
Purpose:
- merge A + B into one clean implementation branch
- resolve schema/wiring conflicts
- run the combined verification set

### Reviewer lane
Purpose:
- independently review Lane C head only
- confirm both engineering intent and test evidence

This split is justified because writer telemetry and reader/query surfaces are separable bounded contexts with a documented schema boundary between them.

---

## Non-goals for this slice

- do not redesign the BT bridge mode
- do not move high-frequency progress into `torrent_info.json`
- do not add a GUI/dashboard
- do not add proxy-pool scheduling here
- do not refactor unrelated qB/SFTP code outside what telemetry integration requires

---

## Merge / closeout requirements

The task is not complete until all of the following are true:
1. baseline direct-mode fix is on `main`
2. observability doc and tracked prompt docs are committed
3. telemetry writer + reader slices are integrated
4. independent reviewer approves the integrated head
5. `main` is fast-forwarded or otherwise updated to the accepted integrated head
6. verification is rerun on `main`
7. disposable task-local worktrees / watcher processes / tmux sessions are cleaned up
