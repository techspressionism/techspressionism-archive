#!/bin/zsh
# Go back to an earlier push live (Colin, 26 Sep 2026): restores the rollback snapshot of push N (see the note "Archive: Changelog" or private/push-log.json).
#
#   scripts/revert-live.sh 5           # rehearsal: fetches push 005's snapshot, lists what WOULD change on the live site; copies nothing
#   scripts/revert-live.sh 5 --go      # really copies it (after that the live site is exactly push 005)
#
# Needs the WP Engine key unlocked and `gh` logged in. It replaces the local site/ folder with the snapshot (rebuild before the next push live).
# Afterwards the git history is untouched: to make the reverted state permanent, also revert those commits in git and push to staging.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
N="${1:?usage: revert-live.sh <push number> [--go]}"
TAG="$(python3 -c "import json,sys; log=json.load(open('private/push-log.json')); e=[x for x in log if x['n']==int(sys.argv[1])]; print(e[0]['snapshot'] if e else '')" "$N")"
[[ -n "$TAG" ]] || { echo "No push number $N in private/push-log.json"; exit 1; }
echo "Push $(printf %03d "$N"): snapshot $TAG"
TMP="$(mktemp -d)"
gh release download "$TAG" --repo techspressionism/techspressionism-archive-snapshots --pattern '*.zip' --dir "$TMP" || { echo "could not download the snapshot"; exit 1; }
rm -rf site && mkdir site && unzip -q "$TMP"/*.zip -d site && rm -rf "$TMP"
[[ -f site/index.html ]] || { echo "the snapshot has no index.html"; exit 1; }
if [[ "${2:-}" == "--go" ]]; then
  WPE_INSTALL="${WPE_INSTALL:-techspression}" scripts/deploy-wpengine.sh --go
  echo "Live is now push $(printf %03d "$N"). Clear the WP Engine and Cloudflare caches."
else
  WPE_INSTALL="${WPE_INSTALL:-techspression}" scripts/deploy-wpengine.sh | tail -15
  echo "Rehearsal only. Add --go to make push $(printf %03d "$N") live."
fi
