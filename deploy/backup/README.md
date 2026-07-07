# Store backup (local pull from the VPS)

The VPS store `/opt/gamma-research/data` is the project's **only non-re-derivable
asset**: the data source is snapshot-only, so open interest and greeks cannot be
re-bought or re-computed after the fact. One disk failure resets the project to zero
(quant review Tier 1, item 1). This job pulls a nightly mirror of the store (and the
VPS `.env`) to local disk on this Mac.

## What it does

`pull_backup.sh`:

- `rsync -a` (no `--delete`) `ubuntu@40.160.233.235:/opt/gamma-research/data/` into
  `~/Backups/gamma-research/data/`, over `ssh -i ~/.ssh/id_ed25519` with
  `IdentitiesOnly=yes` (and `BatchMode`, `accept-new` for a safe unattended run).
- `scp` `/opt/gamma-research/.env` to `~/Backups/gamma-research/secrets/env-vps`
  (file `chmod 600`, dir `chmod 700`).
- appends a dated status line to `~/Library/Logs/gamma-backup.log`, e.g.
  `2026-07-06T20:30:01-0400 status=ok size=299M partitions=5290`.

`com.gamma-research.backup.plist` is a launchd agent that runs the **installed** copy
of the script at `~/Backups/gamma-research/bin/pull_backup.sh` every day at **20:30
local** time, logging stdout/stderr to the same log file.

The plist deliberately points at a copy of the script under `~/Backups/...`, not at
the repo path, because the repo is often checked out in a temporary deploy worktree.
The repo copy here is the **source of truth**; the install step copies it out.

## Install (one time)

```sh
REPO=~/dev/gamma-research

# 1) Install the runner script (source of truth is the repo copy).
mkdir -p ~/Backups/gamma-research/bin
cp "$REPO/deploy/backup/pull_backup.sh" ~/Backups/gamma-research/bin/pull_backup.sh
chmod 755 ~/Backups/gamma-research/bin/pull_backup.sh

# 2) Install and load the launchd agent.
cp "$REPO/deploy/backup/com.gamma-research.backup.plist" \
   ~/Library/LaunchAgents/com.gamma-research.backup.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gamma-research.backup.plist

# 3) Kick one run now to verify (does not wait for 20:30).
launchctl kickstart -k gui/$(id -u)/com.gamma-research.backup
```

Verify it landed:

```sh
tail -n 3 ~/Library/Logs/gamma-backup.log
du -sh ~/Backups/gamma-research/data
ls ~/Backups/gamma-research/data | head
ls -l ~/Backups/gamma-research/secrets/env-vps   # should be -rw------- (600)
```

## Updating the script

After editing `deploy/backup/pull_backup.sh` in the repo, re-copy it (the launchd
job runs the installed copy, not the repo file):

```sh
cp ~/dev/gamma-research/deploy/backup/pull_backup.sh \
   ~/Backups/gamma-research/bin/pull_backup.sh
```

If you change the **plist** (schedule, paths), reload the agent:

```sh
launchctl bootout gui/$(id -u)/com.gamma-research.backup
cp ~/dev/gamma-research/deploy/backup/com.gamma-research.backup.plist \
   ~/Library/LaunchAgents/com.gamma-research.backup.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gamma-research.backup.plist
```

## Restore (rebuild the VPS store from the local backup)

If the VPS store is lost, push the mirror back:

```sh
# Recreate the data dir on a fresh VPS, then:
rsync -a -e "ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes" \
  ~/Backups/gamma-research/data/ ubuntu@<NEW_VPS>:/opt/gamma-research/data/

# Restore the secret (only if provisioning a new box from scratch):
scp -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  ~/Backups/gamma-research/secrets/env-vps ubuntu@<NEW_VPS>:/opt/gamma-research/.env
ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes ubuntu@<NEW_VPS> \
  'chmod 600 /opt/gamma-research/.env'
```

The store is immutable/append-only (one partition per symbol per session), so a
restored mirror is a byte-for-byte valid store: `read_canonical` / `read_symbol_history`
work against it unchanged.

## Uninstall

```sh
launchctl bootout gui/$(id -u)/com.gamma-research.backup
rm ~/Library/LaunchAgents/com.gamma-research.backup.plist
# (the mirror under ~/Backups/gamma-research is left in place on purpose)
```
