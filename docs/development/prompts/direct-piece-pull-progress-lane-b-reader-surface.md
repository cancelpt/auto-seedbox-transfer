You are implementing the reader / ops surface slice for direct-piece observability in an isolated disposable worktree.

Repo: /home/ryan/cancelpt/auto-seedbox-transfer
Read first:
- docs/development/direct-piece-pull-progress-observability-plan.md
- main.py
- managers/audit_manager.py
- utils/config.py
- transfer/torrent_transfer.py
- tests/test_audit_manager.py
- tests/test_runtime_controls.py

Your scope
Implement the read-only operator surface for direct-piece progress:
1. progress report reader/aggregator over the documented telemetry sidecar schema
2. read-only CLI surface in main.py (preferred shape: --progress)
3. route-scoped progress reporting
4. reader-side tests / CLI-path tests / audit/progress aggregation tests

Assume the writer-side lane will generate telemetry snapshots using the schema in the development doc.
Code to that documented contract; do not wait for the other lane.

Hard constraints
- Do not invent a second progress schema.
- Do not rewrite the downloader runtime here.
- Do not move high-frequency progress into torrent_info.json.
- Keep the surface read-only and operational.

Recommended files
- main.py
- managers/audit_manager.py (or a small dedicated helper if cleaner)
- tests/test_audit_manager.py
- tests/test_runtime_controls.py
- new focused test module(s) if needed

Verification
Run the narrowest relevant pytest commands for reader-side progress reporting.

Return with:
- commit id
- exact files changed
- exact pytest commands/results
- any schema assumptions the integration lane must reconcile
