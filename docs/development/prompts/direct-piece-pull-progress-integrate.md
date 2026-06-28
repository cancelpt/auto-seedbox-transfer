You are the integration lane for the direct-piece observability feature.

Repo: /home/ryan/cancelpt/auto-seedbox-transfer
Read first:
- docs/development/direct-piece-pull-progress-observability-plan.md
- git log --oneline --decorate -10
- git diff --stat main...HEAD
- the writer-lane commits already cherry-picked into this worktree

Your job
1. Integrate the accepted writer-side telemetry slice and reader-side ops-surface slice.
2. Resolve any schema / wiring conflicts.
3. Preserve the baseline direct-mode fix already on main.
4. Run the combined targeted verification set.
5. Run a broader regression set for runtime/home/seedbox direct-mode behavior.
6. Leave the worktree clean with a single coherent integration commit if practical; otherwise a very small number of logical commits is acceptable.

Hard constraints
- Do not broaden into unrelated refactors.
- Do not regress direct-mode no-BT/no-peer guarantees.
- Keep the architecture aligned with the development doc.

Minimum verification
- new telemetry-focused tests
- pytest -q tests/test_runtime_controls.py tests/test_home_retries.py tests/test_seedbox_retries.py
- any audit/progress surface tests you added

Return with:
- final HEAD
- commit ids
- exact pytest commands/results
- concise summary of remaining risks, if any
