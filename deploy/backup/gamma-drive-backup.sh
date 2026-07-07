#!/usr/bin/env bash
#
# gamma-drive-backup.sh - off-site (Google Drive) backup layer for the gamma store.
#
# Runs nightly ON THE VPS (the always-on box backs itself up; the Mac pull is the
# second, local layer). Uploads ONE tar.gz per captured session to the rclone
# remote, idempotently: every run lists the remote and uploads any session tarball
# that is missing, so a night lost to downtime self-heals on the next run and the
# very first run backfills the whole store. Session tarballs are unique data (the
# store is append-only), so nothing is ever pruned. Data only: the VPS .env is
# backed up by the Mac pull layer, never sent to Drive.
#
# Gated on the rclone remote existing (remote name: gdrive, scope drive.file), so
# this can be installed before authorization and goes live the moment
# /home/ubuntu/.config/rclone/rclone.conf carries the token.
#
# SOURCE OF TRUTH is the repo copy (deploy/backup/gamma-drive-backup.sh); the
# systemd unit runs /usr/local/bin/gamma-drive-backup.sh. Re-copy after editing.

set -uo pipefail

DATA=/opt/gamma-research/data
REMOTE="gdrive:gamma-research/store"
LOG="$DATA/.drive-backup.log"   # lives in the store, so the Mac pull mirrors it too

ts() { date -u +%FT%TZ; }

if ! rclone listremotes 2>/dev/null | grep -q '^gdrive:'; then
    echo "$(ts) status=skipped reason=rclone-remote-not-configured" >> "$LOG"
    exit 0
fi

# Sessions present locally (date=YYYY-MM-DD partition dirs).
mapfile -t sessions < <(find "$DATA" -maxdepth 2 -type d -name 'date=*' -path "$DATA/symbol=*" \
                        | sed 's/.*date=//' | sort -u)
# Session tarballs already on the remote.
have="$(rclone lsf "$REMOTE" 2>/dev/null | sed -n 's/^gamma-\(.*\)\.tar\.gz$/\1/p')"

up=0; fail=0
for s in "${sessions[@]}"; do
    printf '%s\n' "$have" | grep -qx "$s" && continue
    tmp="/tmp/gamma-$s.tar.gz"
    # Partition paths are shell-safe by construction (symbol charset is [A-Z0-9.-]).
    if ! (cd "$DATA" && find . -maxdepth 2 -type d -name "date=$s" -path "./symbol=*" \
          | sed 's|^\./||' | tar -czf "$tmp" -T -); then
        fail=$((fail + 1)); rm -f "$tmp"; continue
    fi
    if rclone copyto "$tmp" "$REMOTE/gamma-$s.tar.gz" 2>>"$LOG"; then
        up=$((up + 1))
    else
        fail=$((fail + 1))
    fi
    rm -f "$tmp"
done

# Heartbeat mirror (tiny; overwritten each run).
rclone copyto "$DATA/.last_run.json" "$REMOTE/.last_run.json" 2>/dev/null || true

echo "$(ts) status=$([ $fail -eq 0 ] && echo ok || echo error) uploaded=$up failed=$fail sessions_local=${#sessions[@]}" >> "$LOG"
[ "$fail" -eq 0 ]
