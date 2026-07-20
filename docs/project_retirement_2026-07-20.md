# Project retirement — 2026-07-20

Status: **retired indefinitely; research artifacts preserved; no live trading authorized.**

## Research disposition

EXP-2026-001 passed 0/4 locked development gates. EOD OI-GEX level is closed as a standalone
alpha lane. The prospective holdout remains sealed and must not be scored for the failed
specification. Existing negative results remain valid; re-entry may not tune thresholds or slice
old data to rescue the hypothesis.

## VPS disposition

- `gamma-snapshot.timer`: disabled.
- `gamma-drive-backup.timer`: disabled.
- `gamma-drive-backup.service`: inactive.
- Repository and deployment files: retained at `/opt/gamma-research`.
- Former data directory: `/opt/gamma-research/data`, deleted after off-site verification.
- Retirement verification record:
  `/opt/gamma-research/retirement/backup-verification-20260720T001806Z.txt`.

The deployment workflow no longer runs on pushes to `main`. A manual run requires the explicit
boolean confirmation `reactivate=true`; using it requires prior research re-entry approval.

## Off-site evidence

The legacy per-session backup was not accepted as deletion evidence. At audit time, Google Drive
held 2,191 non-empty session archives for 2,649 local sessions, leaving 458 local sessions without
a corresponding archive.

A quiescent full snapshot was therefore created after the backup job was stopped and no process had
the source directory open.

| Field | Value |
|---|---|
| Drive object | `gamma-research/full-snapshots/gamma-research-data-20260720T001806Z.tar.gz` |
| Compressed size | `7,618,440,841` bytes |
| MD5 | `915298bad7661e9656330a01e11c5ff8` |
| Verification | Local and Google Drive byte size and MD5 matched |

An independent post-deletion query returned the same Drive size and MD5. The temporary local
archive was also removed. VPS disk use fell from approximately 25 GB to 15 GB.

## Recovery boundary

The corpus is no longer queryable on the VPS. Recovery requires downloading the full snapshot,
checking its MD5, and extracting it beneath `/opt/gamma-research`. Restoring the corpus does not
authorize data collection, a holdout look, trading, or strategy optimization.

Any re-entry requires:

1. a genuinely different preregistered options hypothesis;
2. a documented need for data not already in the preserved corpus;
3. a fixed acquisition window, retention budget, and stopping rule; and
4. explicit approval before any collector or backup timer is re-enabled.
