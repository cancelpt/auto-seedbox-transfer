# Codex reviewer prompt — direct piece pull final review

You are reviewing the repository:
- /home/ryan/cancelpt/auto-seedbox-transfer

Your assigned review worktree/branch:
- Worktree: /home/ryan/cancelpt/auto-seedbox-transfer-wt-dpp-review-20260628-075432
- Branch: dpp-review-20260628-075432

Review target:
- The integrated direct-piece-pull branch prepared by Lane C

Read first:
1. /home/ryan/cancelpt/auto-seedbox-transfer/docs/development/direct-piece-pull-implementation-plan.md
2. The final integrated diff versus main
3. New/changed tests relevant to direct mode

Mission:
Perform an independent review of the final integrated direct piece pull implementation and provide a crisp verdict with concrete blockers or approval.

Review checklist:
- Confirm `direct_piece_pull` never triggers seedbox BT bridge add paths.
- Confirm `direct_piece_pull` never injects seedbox peers into home qB.
- Confirm per-piece progress is stored outside torrent_info.json.
- Confirm the implementation stays explicitly v1-only.
- Confirm direct payload reads still use the existing SFTP network-profile proxy path.
- Confirm backward-compatible legacy `qb_bt` behavior is preserved.
- Confirm tests cover the new mode sufficiently for the touched behavior.

Required verification:
- Run the reviewer’s chosen targeted pytest command(s) and record results.
- Inspect the final diff, not only prose summaries.

Output contract:
- Verdict: APPROVED or CHANGES_REQUESTED
- If changes requested: numbered blockers with exact files/tests affected
- If approved: note any residual non-blocking risks
- Exact pytest command(s) run and pass/fail results
- Exact reviewed HEAD SHA

Do not implement features or refactor unless a tiny review-time fix is strictly necessary and you clearly disclose it. Prefer review-first, verdict-first behavior.
