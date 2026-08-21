#!/usr/bin/env bash
# WS-18A — daily backup of the irreplaceable state (dot.db + sessions/).
#
# chroma_db/ is deliberately NOT backed up: it is fully derivable from dot.db
# via migrate_sqlite_to_chroma() (memory.py) using free local embeddings.
# That is what keeps this backup small (~9.4 MB/day) instead of ~80 MB, and
# is why the daily Dropbox copy costs nothing extra.
#
# Restore: copy the newest backups/dot-<stamp>.db back to dot.db, delete
# chroma_db/, start the bot — migrate_sqlite_to_chroma() rebuilds the vector
# index on boot. Tested restore procedure: see README.md "Backups & restore".
set -euo pipefail
cd "$(dirname "$0")"
STAMP=$(date +%Y%m%d)
mkdir -p backups

# .backup is WAL-safe and does not block the running bot; a plain cp of
# dot.db is not (it can copy a torn/inconsistent file while WAL is live).
venv/bin/python -c "
import sqlite3, sys
s = sqlite3.connect('dot.db')
d = sqlite3.connect(sys.argv[1])
s.backup(d)
d.close()
s.close()
" "backups/dot-$STAMP.db"

tar czf "backups/sessions-$STAMP.tar.gz" sessions/

# Retention: keep the newest 14 of each.
ls -1t backups/dot-*.db          2>/dev/null | tail -n +15 | xargs -r rm
ls -1t backups/sessions-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm

# Offsite copy of today's pair into Dropbox /Dot Backups/ via the already-
# authenticated dropbox client — no new dependency, no new account, no new
# cost line. Best-effort: a Dropbox hiccup should not fail the local backup,
# which is why this is a separate step after the local files already exist.
venv/bin/python backup_offsite.py "backups/dot-$STAMP.db" "backups/sessions-$STAMP.tar.gz" || \
    echo "Offsite Dropbox copy failed (see above) — local backup is still intact."

echo "Backup complete: backups/dot-$STAMP.db, backups/sessions-$STAMP.tar.gz"
