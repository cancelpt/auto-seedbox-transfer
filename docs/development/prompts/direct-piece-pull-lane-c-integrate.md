# Codex worker prompt — Lane C: integrate direct piece pull runtime workflow

You are working in the repository:
- /home/ryan/cancelpt/auto-seedbox-transfer

Your assigned worktree/branch for this lane:
- Worktree: /home/ryan/cancelpt/auto-seedbox-transfer-wt-dpp-integrate-20260628-075432
- Branch: dpp-integrate-20260628-075432

Prerequisite inputs:
- Design doc: /home/ryan/cancelpt/auto-seedbox-transfer/docs/development/direct-piece-pull-implementation-plan.md
- Lane A branch: dpp-config-20260628-075432
- Lane B branch: dpp-engine-20260628-075432

Read first:
1. The design doc above
2. main.py
3. managers/seedbox_manager.py
4. managers/home_manager.py
5. Any new transfer/direct_piece_* modules and config/state changes from lanes A and B

Mission:
Integrate the completed lane A + lane B work into a functioning `direct_piece_pull` runtime mode while preserving the legacy `qb_bt` path.

Required scope:
- Bring in the finished lane A and lane B commits into this integration branch.
- Add runtime manager selection / orchestration for the new data-plane mode.
- Ensure direct mode does NOT add BT bridge torrents to seedbox qB.
- Ensure direct mode does NOT add BT bridge torrents to home qB.
- Ensure direct mode does NOT inject seedbox peers into home qB.
- Ensure completed direct payloads lead to original-torrent import into home qB against the local verified payload.
- Keep existing final seedbox origin cleanup / keep-category behavior working once the transfer is complete.
- Add/adjust integration tests for the new branching behavior and direct-mode runtime flow.

Hard boundaries:
- Do NOT add v2 torrent support, proxy pools, HTTP sidecars, or other scope expansions.
- Do NOT rewrite the legacy workflow beyond what is required to branch between legacy and direct mode safely.
- Keep per-piece state in sidecar files, not in torrent_info.json.

Suggested flow:
1. Inspect lane A and lane B final commits.
2. Cherry-pick or otherwise integrate them into this worktree.
3. Resolve interface drift cleanly.
4. Implement the runtime wiring and tests.
5. Run focused pytest for direct-mode runtime behavior.
6. If practical, run the broader full pytest suite before finalizing.

Verification:
- Include exact pytest commands run.
- Do not claim success without real passing output.

Commit contract:
- Commit the integrated runtime work on this branch.
- Suggested commit message: feat: integrate direct piece pull workflow

Output contract in your final Codex response:
- Which lane commits were integrated
- Summary of changed files
- Exact pytest command(s) run and pass/fail results
- Final commit SHA
- Any known risks for reviewer attention
