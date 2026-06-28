# Codex worker prompt — Lane A: direct piece pull config/coarse state

You are working in the repository:
- /home/ryan/cancelpt/auto-seedbox-transfer

Your assigned worktree/branch for this lane:
- Worktree: /home/ryan/cancelpt/auto-seedbox-transfer-wt-dpp-config-20260628-075432
- Branch: dpp-config-20260628-075432

Read these files first:
1. /home/ryan/cancelpt/auto-seedbox-transfer/docs/development/direct-piece-pull-implementation-plan.md
2. /home/ryan/cancelpt/auto-seedbox-transfer/utils/config.py
3. /home/ryan/cancelpt/auto-seedbox-transfer/transfer/torrent_transfer.py
4. /home/ryan/cancelpt/auto-seedbox-transfer/config.example.yaml
5. Existing related tests under /home/ryan/cancelpt/auto-seedbox-transfer/tests/

Mission:
Implement ONLY the validated configuration surface and minimal coarse workflow state required for the new `direct_piece_pull` mode.

Required scope:
- Add a validated transfer/data-plane mode surface with legacy default preserved.
- Add validated direct-mode config fields for worker count and resume path.
- Update config.example.yaml to document the new mode and direct-mode fields.
- Add only minimal coarse state fields to TorrentTransfer if runtime integration will need them later.
- Add/adjust focused tests for config/state validation.

Hard boundaries:
- Do NOT implement the piece downloader engine.
- Do NOT wire main.py manager selection.
- Do NOT refactor unrelated config/state behavior.
- Do NOT touch README unless strictly required by failing tests (it should not be required for this lane).

Quality bar:
- Keep backward compatibility for existing config paths.
- Keep the default mode on the legacy BT workflow.
- Use precise field names matching the plan unless a better repo-local naming pattern is clearly preferable.
- Add comments/examples only where they materially help users understand the new mode.

Verification:
- Run focused pytest for the tests you changed or added.
- If practical, run the full test file set directly related to config/state.
- Do not claim success without real pytest output.

Commit contract:
- Commit your completed lane work on your lane branch.
- Use commit message: feat: add direct piece pull config scaffolding

Output contract in your final Codex response:
- Summary of changed files
- Exact pytest command(s) run and pass/fail results
- Final commit SHA
- Any assumptions/blockers that the integration lane must know
