You are working in /home/ryan/cancelpt/auto-seedbox-transfer on branch main with a dirty worktree.

Task: establish a clean baseline for the new direct-piece observability project.

Read first:
- docs/development/direct-piece-pull-progress-observability-plan.md
- main.py
- managers/direct_transfer_manager.py
- tests/test_runtime_controls.py
- /tmp/ast-progress-baseline.patch

What already exists
- The current dirty worktree already contains a verified direct-mode runtime fix:
  1. direct-mode run_once ordering is bounded so HomeManager runs before another large direct backlog monopolizes the cycle
  2. DirectTransferManager respects transfer.max_once_add
  3. targeted tests already passed in controller verification
- The controller also authored the new development doc and tracked prompt docs under docs/development/.

Your job
1. Inspect the current dirty diff in this main worktree.
2. Verify the changes match the baseline intent in /tmp/ast-progress-baseline.patch.
3. Stage and commit the tracked development doc + prompt docs for this observability slice.
4. Stage and commit the current direct-mode runtime fix in code/tests.
5. Run the focused baseline pytest commands.
6. Report exact commit ids and exact pytest commands/results.

Scope boundaries
- Do not start implementing observability yet.
- Do not change feature behavior beyond the already-present baseline fix.
- Keep commits clean and narrow.

Expected commits
- docs commit for the new development doc + prompt docs
- code/test commit for the direct-mode run_once / max_once_add baseline fix

Minimum verification
- pytest -q tests/test_runtime_controls.py::test_run_once_cycle_uses_direct_mode_sequence tests/test_runtime_controls.py::test_main_direct_mode_uses_direct_transfer_manager_in_run_once tests/test_runtime_controls.py::test_direct_transfer_manager_respects_max_once_add tests/test_home_retries.py::test_home_direct_mode_waits_for_payload_ready_without_bt_peer_injection tests/test_home_retries.py::test_home_direct_mode_imports_origin_from_payload_root_without_bt_peer_injection tests/test_seedbox_retries.py::test_seedbox_direct_mode_does_not_add_bt_bridge_torrent tests/test_seedbox_retries.py::test_seedbox_direct_mode_cleans_up_origin_after_home_import_without_bt_bridge
- pytest -q tests/test_runtime_controls.py tests/test_home_retries.py tests/test_seedbox_retries.py

Return with:
- commit ids
- files in each commit
- pytest commands and results
- whether main worktree is clean afterward
