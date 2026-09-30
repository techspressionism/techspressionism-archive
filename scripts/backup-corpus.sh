#!/bin/zsh
# Dated safeguard copy of corpus/corpus.json (the single file every page on the archive is built from) --
# in addition to it already being tracked in git, a plain dated copy in two places that don't depend on
# git/GitHub being reachable: on this Mac, and on the WP Engine server (private, outside any site's web
# root, so it is never publicly downloadable). Run automatically by push-live.sh on every live push, or
# by hand any time:
#
#   scripts/backup-corpus.sh
#
# Needs the WP Engine SSH key unlocked (Colin runs: ssh-add --apple-use-keychain ~/.ssh/id_ed25519_wpengine).
# If that fails or isn't unlocked, the local copy still happens -- this never blocks a push live.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

STAMP="$(date +%Y-%m-%d-%H%M)"
LOCAL_DIR="$HOME/Desktop/Techspressionism-Corpus-Backups"
LOCAL_FILE="$LOCAL_DIR/corpus-$STAMP.json"
mkdir -p "$LOCAL_DIR"
cp corpus/corpus.json "$LOCAL_FILE"
echo "Local backup: $LOCAL_FILE ($(du -h "$LOCAL_FILE" | cut -f1))"

KEY="${WPE_SSH_KEY:-$HOME/.ssh/id_ed25519_wpengine}"
INSTALL="${WPE_INSTALL:-techspression}"
if [[ -f "$KEY" ]]; then
  if scp -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=10 -o BatchMode=yes \
      corpus/corpus.json "${INSTALL}@${INSTALL}.ssh.wpengine.net:private-backups/corpus/corpus-$STAMP.json" 2>/tmp/backup-corpus-wpe.log; then
    echo "WP Engine backup: ~/private-backups/corpus/corpus-$STAMP.json on $INSTALL (not web-accessible)"
  else
    echo "WP Engine backup FAILED (local copy above still succeeded) -- see /tmp/backup-corpus-wpe.log. Likely cause: the SSH key isn't unlocked (ssh-add --apple-use-keychain $KEY)."
  fi
else
  echo "WP Engine backup skipped: no SSH key at $KEY"
fi
