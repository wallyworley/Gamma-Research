#!/usr/bin/env bash
#
# pull_backup.sh - nightly pull-backup of the irreplaceable gamma-research store.
#
# /opt/gamma-research/data on the VPS is the project's only non-re-derivable asset:
# the data source is snapshot-only, so open interest and greeks are NOT backfillable.
# One VPS disk failure would reset the project to zero (quant review Tier 1, item 1).
# This script mirrors the store (and the VPS .env) to local disk over ssh and appends a
# dated status line to a log, so a silent failure is visible the next morning.
#
# SOURCE OF TRUTH is the repo copy:  ~/dev/gamma-research/deploy/backup/pull_backup.sh
# The launchd job runs the INSTALLED copy: ~/Backups/gamma-research/bin/pull_backup.sh.
# After editing the repo copy, re-install it (see deploy/backup/README.md). Keeping the
# runner outside the repo means the temporary deploy worktree can come and go safely.

set -uo pipefail

VPS="ubuntu@40.160.233.235"
SSH_KEY="$HOME/.ssh/id_ed25519"
# IdentitiesOnly: use only this key. BatchMode: never prompt (fail instead) so an
# unattended run can't hang. accept-new: trust a first-seen host key but pin it after.
SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
          -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30)

REMOTE_DATA="/opt/gamma-research/data/"
REMOTE_ENV="/opt/gamma-research/.env"

DEST_ROOT="$HOME/Backups/gamma-research"
DEST_DATA="$DEST_ROOT/data/"
DEST_SECRETS="$DEST_ROOT/secrets"
DEST_ENV="$DEST_SECRETS/env-vps"
LOG="$HOME/Library/Logs/gamma-backup.log"

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }

mkdir -p "$DEST_DATA" "$DEST_SECRETS" "$(dirname "$LOG")"
# The secret lives in a 700 dir; keep the backup root private too.
chmod 700 "$DEST_ROOT" "$DEST_SECRETS" 2>/dev/null || true

status="ok"
detail=""

# 1) Mirror the store. -a preserves mtimes/perms so unchanged partitions are skipped.
#    NO --delete: a remote glitch that empties data/ must never wipe the local backup;
#    the store is immutable/append-only, so we only ever add. rsync writes to its own
#    temp files and renames, so an interrupted run can't corrupt a partition.
if ! rsync -a -e "ssh ${SSH_OPTS[*]}" "$VPS:$REMOTE_DATA" "$DEST_DATA"; then
    status="error"; detail="${detail}rsync-data-failed "
fi

# 2) Copy the VPS .env (tiny, rarely changes). 600 file inside the 700 secrets dir.
if scp "${SSH_OPTS[@]}" "$VPS:$REMOTE_ENV" "$DEST_ENV" >/dev/null 2>&1; then
    chmod 600 "$DEST_ENV" 2>/dev/null || true
else
    status="error"; detail="${detail}scp-env-failed "
fi

# Evidence for the log line: mirrored size + partition count.
size="$(du -sh "$DEST_DATA" 2>/dev/null | cut -f1)"
parts="$(find "$DEST_DATA" -name chain.parquet 2>/dev/null | wc -l | tr -d ' ')"
echo "$(ts) status=$status size=${size:-?} partitions=${parts:-?} ${detail}" >> "$LOG"

# Non-zero exit on failure so launchd records it and a caller can react.
[ "$status" = "ok" ]
