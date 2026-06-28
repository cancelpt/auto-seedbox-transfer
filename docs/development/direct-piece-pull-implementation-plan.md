# Direct Piece Pull Implementation Plan

> For Hermes/tmux Codex: implement this feature in isolated worktrees. Keep controller-side repo edits limited to tracked design/prompt docs and merge orchestration. All code/config/runtime edits happen in pane-visible Codex lanes.

Goal: add a new transfer mode that avoids home qB direct peer connections to the seedbox by pulling payload data over proxied SFTP, validating every v1 torrent piece with the original piece hashes, resuming at piece granularity, and only importing the original torrent into home qB after the payload is fully verified.

Architecture: keep the current qB/SFTP control plane for discovery and metadata, but split the data plane into two explicit modes. Legacy mode keeps the existing BT bridge. New mode (`direct_piece_pull`) never adds a BT bridge torrent to either downloader; instead it resolves the remote payload root from seedbox qB metadata, downloads payload bytes through proxied SFTP with a multi-worker piece scheduler, persists piece-complete state outside the coarse transfer JSON, and hands the finished payload to home qB via the original `.torrent` file.

Tech stack: existing Python 3.10 codebase, qbittorrent-api, paramiko SFTP, existing network profile/proxy support, SHA1 from stdlib, pytest.

---

## Constraints and explicit decisions

1. The problem to solve is “avoid local machine direct connections to the seedbox” rather than “hide all seedbox traffic”.
2. Only v1 torrents are in scope for this slice.
3. The first release uses a single local proxy endpoint such as `127.0.0.1:7890`.
4. Typical payloads are 1–10 GB, so correctness and resume behavior matter more than exotic swarm scheduling.
5. Resume must be tracked at piece granularity.
6. In `direct_piece_pull` mode, home qB must not receive injected seedbox peers and seedbox qB must not receive temporary BT bridge torrents.
7. The existing `torrent_info.json` state file remains coarse workflow state only; per-piece progress moves to sidecar files under a dedicated resume directory.
8. Reuse the existing `seed_box.network_profile.sftp` proxy path for direct payload reads. Do not add a second unrelated proxy surface for MVP.

## Current repo facts this plan builds on

- The current README documents a BT bridge flow where seedbox and home qB transfer over P2P.
- `managers/home_manager.py` currently injects the seedbox peer into home BT tasks.
- `managers/seedbox_manager.py` currently adds temporary BT torrents back into the seedbox.
- `utils/config.py` already models network profiles for qB and SFTP.
- `utils/torrent_utils.py` already parses v1 torrent metadata including file lists, piece length, and piece count.
- `managers/state_manager.py` rewrites the whole JSON file on each update, so it is not suitable for piece-by-piece checkpoints.

## MVP feature surface

### New transfer mode

Add a transfer mode enum / validated config field:

- `qb_bt` — existing behavior, default for backward compatibility
- `direct_piece_pull` — new behavior in this plan

Add direct-mode config fields under `transfer`:

- `data_plane_mode: qb_bt | direct_piece_pull`
- `direct_piece_workers: int = 4`
- `direct_piece_resume_path: str`
- optional retry tuning only if needed for tests; do not add speculative knobs beyond MVP

Proxy behavior:

- qB API continues to use downloader network profiles as today
- direct payload reads use `seed_box.network_profile.sftp`
- the implementation must work when that SFTP profile uses `http_connect` to `127.0.0.1:7890`

### Direct-mode behavior

When `data_plane_mode == direct_piece_pull`:

1. SeedBoxManager still discovers completed source-category torrents and auto-downloads the original `.torrent` metadata when configured.
2. No BT bridge torrent is generated or required.
3. A new DirectTransferManager/worker subsystem pulls the actual payload from the seedbox filesystem over SFTP.
4. Every piece is verified against the original torrent’s `pieces` SHA1 hashes before it is marked complete.
5. Resume state is stored outside `torrent_info.json` using manifest + piece-state sidecars.
6. Once all pieces verify, HomeManager imports the original torrent into home qB using the already-downloaded payload path.
7. SeedBoxManager then performs the existing final cleanup/keep-category behavior against the seedbox origin torrent only.

## Sidecar state design

Create a dedicated resume directory, for example:

- `${transfer.direct_piece_resume_path}/{info_hash}.manifest.json`
- `${transfer.direct_piece_resume_path}/{info_hash}.pieces`

Manifest responsibilities:

- bind the sidecar to `info_hash`
- record torrent name, piece length, piece count, total size
- record the target local root path
- record the resolved remote root path / per-file remote paths
- record the per-file local paths and lengths
- record timestamps / schema version for compatibility checks

Piece-state file responsibilities:

- one fixed slot per piece index
- MVP may use one byte per piece (`0` incomplete, `1` complete) for simplicity
- updates must be thread-safe and durable enough for abrupt process stops
- never trust a piece as complete unless its state slot is set after hash verification succeeds

Reset rules:

- if manifest schema version, piece count, total size, or target root mismatch, discard the old sidecar pair and rebuild
- legacy `torrent_info.json` state should not be polluted with thousands of per-piece booleans

## New code modules / responsibilities

### 1) Config and coarse state

Files:
- Modify: `utils/config.py`
- Modify: `config.example.yaml`
- Modify: `transfer/torrent_transfer.py`
- Add/modify tests under `tests/`

Responsibilities:
- introduce the direct mode config surface and validation
- add only the coarse state fields actually needed by managers, for example a boolean like `is_direct_payload_ready` and maybe `direct_payload_root`
- do not move piece arrays into `TorrentTransfer`

### 2) Torrent piece metadata helpers

Files:
- Modify: `utils/torrent_utils.py`
- Add tests under `tests/test_torrent_utils.py` or a new focused test module

Responsibilities:
- expose the raw v1 piece hash list as 20-byte SHA1 values / hex helpers
- expose deterministic piece-to-file-span iteration
- support pieces that cross file boundaries
- support both single-file and multi-file torrents

### 3) Random-access SFTP reads

Files:
- Modify: `utils/sftp_utils.py`
- Add tests: `tests/test_sftp_proxy.py` or a new focused test module

Responsibilities:
- keep existing connect/upload/download behavior working
- add an API for random-access reads, e.g. open-and-read-range per remote file
- keep per-worker session ownership explicit; do not share one live SFTP session across many worker threads
- preserve proxy behavior and secret-redaction in logs

### 4) Direct piece transfer engine

Files to add (names can vary if the lane finds a better local fit):
- `transfer/direct_piece_manifest.py`
- `transfer/direct_piece_resume.py`
- `transfer/direct_piece_downloader.py`

Responsibilities:
- build the manifest from torrent metadata + seedbox qB save path
- pre-create local directory/file layout at the target payload root
- schedule incomplete pieces across a bounded worker pool
- read remote byte ranges via SFTP, hash-check the assembled piece in memory, then write verified bytes locally
- mark piece completion in the sidecar state only after verification succeeds
- support restart/resume from an existing sidecar pair
- fail fast with explicit errors on repeated piece mismatches or missing remote files

Scheduling guidance:
- prefer simple bounded concurrency over bittorrent-like rarest-first logic
- sequential windows are acceptable for MVP because the source is a single seedbox and the target sizes are moderate
- correctness and clean retries beat theoretical maximal throughput

### 5) Runtime integration

Files:
- Add: `managers/direct_transfer_manager.py`
- Modify: `main.py`
- Modify: `managers/seedbox_manager.py`
- Modify: `managers/home_manager.py`
- Add/modify runtime tests under `tests/`

Responsibilities:
- instantiate `DirectTransferManager` when `data_plane_mode == direct_piece_pull`
- keep `LocalManager` / BT conversion path for legacy mode only
- prevent seedbox BT add logic from running in direct mode
- prevent home BT add / peer-injection logic from running in direct mode
- after direct payload completion, import the original torrent into home qB with the final payload path and existing final-category/tag behavior where it still makes sense
- preserve the existing seedbox keep/delete logic after `is_torrent_in_home_dl` becomes true

## Expected runtime flow in direct mode

1. SeedBoxManager sees a completed origin torrent in a managed source category.
2. SeedBoxManager ensures a coarse `TorrentTransfer` entry exists and downloads the original `.torrent` metadata if needed.
3. DirectTransferManager loads the original torrent metadata, resolves the seedbox payload root from qB `save_path`, builds/validates the manifest, and starts/resumes the piece scheduler.
4. Workers fetch remote byte ranges through proxied SFTP, verify each piece, and materialize the local payload under the target download root.
5. When all pieces are verified, DirectTransferManager marks the coarse transfer state as payload-ready.
6. HomeManager imports the original torrent into home qB against the already-present payload path and marks the transfer complete.
7. SeedBoxManager performs final cleanup or keep-category handling on the seedbox origin torrent.

## Non-goals for this slice

- v2 or hybrid torrents
- multiple proxies / rotating proxy pools
- HTTP range sidecar service on the seedbox
- streaming/partial import into home qB before the payload is fully verified
- replacing the legacy BT bridge mode
- redesigning audit/cleanup semantics beyond what direct mode minimally needs

## Test plan

### Config / validation
- new mode and worker/resume-path validation
- backward compatibility for default legacy mode
- network-profile reuse remains valid

### Torrent metadata helpers
- v1 piece-hash extraction
- single-file piece slicing
- multi-file piece slicing with cross-file boundary pieces

### Resume sidecars
- manifest create/load/reset behavior
- piece-state mark/read behavior
- restart resumes only incomplete pieces

### SFTP random access
- read-range API on fake/opened remote files
- existing proxy-mode tests still pass

### Direct transfer engine
- downloads a small multi-piece sample payload correctly
- resumes from a partially complete piece-state file
- rejects a piece hash mismatch and leaves the piece incomplete
- fails clearly when a remote file is missing

### Runtime integration
- `main.py` selects the correct manager set by mode
- direct mode never calls BT bridge add paths
- direct mode never injects seedbox peers into home qB
- completed direct payload triggers original-torrent import to home qB
- legacy tests remain green

## Parallel worktree implementation plan

### Lane A — config and coarse-state scaffolding

Objective: land the validated config surface and minimal coarse-state additions needed by later lanes.

Expected files:
- `utils/config.py`
- `config.example.yaml`
- `transfer/torrent_transfer.py`
- focused tests for config/state validation

Must not do:
- deep runtime integration
- direct piece scheduler implementation
- unrelated README refactors

Suggested commit message:
- `feat: add direct piece pull config scaffolding`

### Lane B — piece engine and random-access SFTP

Objective: land the core piece metadata helpers, sidecar resume layer, and direct download engine with focused tests.

Expected files:
- `utils/torrent_utils.py`
- `utils/sftp_utils.py`
- new `transfer/direct_piece_*` modules
- focused engine tests

Must not do:
- `main.py` manager wiring
- SeedBoxManager/HomeManager mode branching beyond small helper surfaces required by tests

Suggested commit message:
- `feat: add direct piece pull engine`

### Lane C — runtime integration after A+B are merged into the integration worktree

Objective: wire the new direct mode into runtime manager selection and home/seedbox behavior.

Expected files:
- `main.py`
- `managers/seedbox_manager.py`
- `managers/home_manager.py`
- `managers/direct_transfer_manager.py`
- integration tests

Must do:
- keep legacy `qb_bt` path green
- ensure direct mode skips BT bridge logic end-to-end
- run the relevant pytest targets and then the full test suite if practical

Suggested commit message:
- `feat: integrate direct piece pull workflow`

### Reviewer lane

Objective: review the final integrated branch, run targeted validation, and emit a crisp verdict.

Reviewer checklist:
- confirm direct mode never triggers BT peer injection or seedbox BT add paths
- confirm piece progress is not stored in `torrent_info.json`
- confirm v1-only assumptions are explicit in code/tests/docs
- confirm proxy behavior still routes direct payload reads through the existing SFTP network profile
- confirm legacy mode tests still pass

## Merge / verification expectations

1. Each worker lane commits its own slice in its own worktree.
2. The integration lane cherry-picks/merges completed worker branches into a fresh integration worktree and resolves any interface drift there.
3. The reviewer lane reviews the final integrated diff only once it is stable.
4. Merge back to `main` only after review passes and repo-local tests are green.
5. Final closeout must report:
   - worker worktree paths / branch names
   - reviewer verdict
   - final merged commit(s)
   - exact pytest command(s) that passed
