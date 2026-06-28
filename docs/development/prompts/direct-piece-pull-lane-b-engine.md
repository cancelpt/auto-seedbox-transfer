# Codex worker prompt — Lane B: piece metadata, random-access SFTP, direct engine

You are working in the repository:
- /home/ryan/cancelpt/auto-seedbox-transfer

Your assigned worktree/branch for this lane:
- Worktree: /home/ryan/cancelpt/auto-seedbox-transfer-wt-dpp-engine-20260628-075432
- Branch: dpp-engine-20260628-075432

Read these files first:
1. /home/ryan/cancelpt/auto-seedbox-transfer/docs/development/direct-piece-pull-implementation-plan.md
2. /home/ryan/cancelpt/auto-seedbox-transfer/utils/torrent_utils.py
3. /home/ryan/cancelpt/auto-seedbox-transfer/utils/sftp_utils.py
4. /home/ryan/cancelpt/auto-seedbox-transfer/tests/test_torrent_utils.py
5. /home/ryan/cancelpt/auto-seedbox-transfer/tests/test_sftp_proxy.py

Mission:
Implement the core direct piece pull building blocks: v1 piece metadata helpers, random-access SFTP reads, resume sidecar helpers, and the direct piece download engine modules.

Required scope:
- Extend torrent metadata helpers to expose v1 piece hashes and deterministic piece-to-file-span iteration.
- Support pieces crossing file boundaries.
- Extend SFTP utilities with safe random-access read capability suitable for per-worker usage.
- Add new direct-piece modules for manifest/resume/downloader logic.
- Add focused tests for these helpers and engine behavior.

Hard boundaries:
- Do NOT wire main.py manager selection.
- Do NOT modify HomeManager/SeedBoxManager runtime flow beyond tiny interface helpers if truly unavoidable.
- Do NOT add speculative features like proxy pools, HTTP sidecars, v2 torrents, or streaming import.
- Keep piece progress out of torrent_info.json / TorrentTransfer.

Implementation guidance:
- Prefer clear/simple bounded concurrency over bittorrent-style rarest-first complexity.
- Correctness and resume semantics matter more than maximum throughput.
- Assume only v1 torrents are in scope for this lane.
- Preserve existing SFTP proxy behavior and secret redaction.

Verification:
- Run focused pytest for touched/new modules.
- Include any new tests for resume sidecars, piece mismatch handling, and random-access SFTP.
- Do not claim success without real pytest output.

Commit contract:
- Commit your completed lane work on your lane branch.
- Use commit message: feat: add direct piece pull engine

Output contract in your final Codex response:
- Summary of changed files
- Exact pytest command(s) run and pass/fail results
- Final commit SHA
- Any integration notes or public interfaces that Lane C must consume
