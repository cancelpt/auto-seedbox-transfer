You are implementing the writer-side telemetry slice for direct-piece observability in an isolated disposable worktree.

Repo: /home/ryan/cancelpt/auto-seedbox-transfer
Read first:
- docs/development/direct-piece-pull-progress-observability-plan.md
- transfer/direct_piece_downloader.py
- transfer/direct_piece_resume.py
- managers/direct_transfer_manager.py
- utils/config.py
- transfer/torrent_transfer.py
- tests/test_direct_piece_engine.py
- tests/test_runtime_controls.py

Your scope
Implement the writer-side telemetry pipeline only:
1. telemetry/read-model module for direct-piece status snapshots (new module is fine)
2. downloader progress callback/observer support
3. periodic progress snapshots while downloading
4. stall detection and resumed transition
5. periodic human-readable progress logs from runtime integration
6. writer-side tests

Do NOT implement the reader/query CLI surface in this lane unless absolutely required by a shared helper.
That belongs to another lane.

Hard constraints
- Do not put high-frequency progress fields into torrent_info.json or TorrentTransfer.
- Keep manifest + .pieces as the correctness layer.
- Telemetry sidecar is a projection.
- Do not reopen BT bridge behavior.
- Match the schema/contract described in the development doc.

Recommended files
- utils/config.py
- transfer/direct_piece_downloader.py
- transfer/direct_piece_resume.py (only if genuinely needed)
- transfer/direct_piece_telemetry.py (or equivalent)
- managers/direct_transfer_manager.py
- tests: new telemetry-focused test module and/or direct engine/runtime test extensions

Verification
Run the narrowest relevant pytest commands for writer-side telemetry and direct engine/runtime behavior.

Return with:
- commit id
- exact files changed
- exact pytest commands/results
- any schema contract the integration lane must honor
