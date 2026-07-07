#!/usr/bin/env bash
#
# gamma-drive-backup.sh - off-site (Google Drive) backup layer for the gamma store.
#
# Runs nightly ON THE VPS. Uploads ONE tar.gz per captured session to the rclone
# remote, driven by a partition-count MANIFEST: a session is (re)uploaded whenever its
# local partition count differs from the count recorded at last upload. That makes it
# correct under every write pattern: the first run backfills everything, a lost night
# self-heals, and a later backfill phase that ADDS symbols to an already-uploaded
# historic session triggers a re-upload (the old once-only design left stale tarballs).
# Sessions written to in the last 30 minutes are deferred to the next run (a backfill
# may still be adding partitions to them). Data only: the VPS .env is backed up by the
# Mac pull layer, never sent to Drive.
#
# Gated on the rclone remote existing (remote name: gdrive), so it can be installed
# before authorization. SOURCE OF TRUTH is the repo copy (deploy/backup/); the systemd
# unit runs /usr/local/bin/gamma-drive-backup.sh. Re-copy after editing.

set -uo pipefail

DATA=/opt/gamma-research/data
REMOTE="gdrive:gamma-research/store"
LOG="$DATA/.drive-backup.log"
MANIFEST="$DATA/.drive-backup-manifest"   # lines: <session> <n_partitions_at_upload>
RECENT_MIN=30                             # defer sessions written within the last N minutes

ts() { date -u +%FT%TZ; }

if ! rclone listremotes 2>/dev/null | grep -q '^gdrive:'; then
    echo "$(ts) status=skipped reason=rclone-remote-not-configured" >> "$LOG"
    exit 0
fi

touch "$MANIFEST"
mapfile -t sessions < <(find "$DATA" -maxdepth 2 -type d -name 'date=*' -path "$DATA/symbol=*" \
                        | sed 's/.*date=//' | sort -u)

up=0; fail=0; deferred=0; unchanged=0
for s in "${sessions[@]}"; do
    n_local=$(find "$DATA" -maxdepth 2 -type d -name "date=$s" -path "$DATA/symbol=*" | wc -l | tr -d ' ')
    n_manifest=$(awk -v k="$s" '$1==k{print $2}' "$MANIFEST" | tail -1)
    if [ "${n_manifest:-}" = "$n_local" ]; then
        unchanged=$((unchanged + 1)); continue
    fi
    # still being written? defer to the next nightly run
    recent=$(find "$DATA" -maxdepth 3 -path "*date=$s*" -name chain.parquet -mmin "-$RECENT_MIN" | head -1)
    if [ -n "$recent" ]; then
        deferred=$((deferred + 1)); continue
    fi
    tmp="/tmp/gamma-$s.tar.gz"
    if ! (cd "$DATA" && find . -maxdepth 2 -type d -name "date=$s" -path "./symbol=*" \
          | sed 's|^\./||' | tar -czf "$tmp" -T -); then
        fail=$((fail + 1)); rm -f "$tmp"; continue
    fi
    if rclone copyto "$tmp" "$REMOTE/gamma-$s.tar.gz" 2>>"$LOG"; then
        up=$((up + 1))
        grep -v "^$s " "$MANIFEST" > "$MANIFEST.tmp" || true
        echo "$s $n_local" >> "$MANIFEST.tmp"
        mv "$MANIFEST.tmp" "$MANIFEST"
    else
        fail=$((fail + 1))
    fi
    rm -f "$tmp"
done

rclone copyto "$DATA/.last_run.json" "$REMOTE/.last_run.json" 2>/dev/null || true
echo "$(ts) status=$([ $fail -eq 0 ] && echo ok || echo error) uploaded=$up unchanged=$unchanged deferred=$deferred failed=$fail sessions_local=${#sessions[@]}" >> "$LOG"
[ "$fail" -eq 0 ]
