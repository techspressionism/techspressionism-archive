#!/bin/zsh
# Copy the built site (site/) to a folder on a WP Engine install, over the SSH gateway, with rsync.
# Nothing secret is stored here: the install name and key come from the environment.
#
#   WPE_INSTALL=<install name> ./scripts/deploy-wpengine.sh           # rehearsal: lists what WOULD change
#   WPE_INSTALL=<install name> ./scripts/deploy-wpengine.sh --go      # really copies
#
# Optional: WPE_SUBDIR (default "archive": the folder under the install's web root that holds the site),
#           WPE_SSH_KEY (default ~/.ssh/id_ed25519_wpengine).
# Do staging first (its install name differs from production), look at it, then production.
# Build first with the final address in place:  python3 scripts/06-build-site.py
# (data/site-config.json canonical_base = the address the site will have; beta_noindex per the launch plan).
set -euo pipefail

INSTALL="${WPE_INSTALL:?set WPE_INSTALL to the WP Engine install name}"
SUBDIR="${WPE_SUBDIR:-archive}"
KEY="${WPE_SSH_KEY:-$HOME/.ssh/id_ed25519_wpengine}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/site/"

[[ -f "${SRC}index.html" ]] || { echo "No built site in ${SRC} -- run scripts/06-build-site.py first"; exit 1; }
[[ -n "$SUBDIR" && "$SUBDIR" != /* && "$SUBDIR" != *..* ]] || { echo "WPE_SUBDIR must be a plain folder name, not empty, absolute, or containing .."; exit 1; }
[[ -f "$KEY" ]] || { echo "SSH key not found: $KEY"; exit 1; }

MODE=(--dry-run)
[[ "${1:-}" == "--go" ]] && MODE=()

# --delete removes files in the archive folder that are no longer part of the site; it never leaves that folder.
rsync -az --delete --itemize-changes "${MODE[@]}" \
  -e "ssh -i $KEY -o IdentitiesOnly=yes" \
  "$SRC" "${INSTALL}@${INSTALL}.ssh.wpengine.net:sites/${INSTALL}/${SUBDIR}/"

if (( ${#MODE[@]} )); then echo; echo "Rehearsal only. Add --go to copy for real."; else echo; echo "Copied to ${INSTALL}/${SUBDIR}/."; fi
