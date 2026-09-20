#!/bin/zsh
# Keep a rollback copy of exactly what was deployed: zip the built site (site/) and attach it to a release in the PRIVATE
# repository techspressionism/techspressionism-archive-snapshots. Do it at every launch or WP Engine push (after
# 06-build-site.py has built the version being deployed), and before anything risky.
#
#   scripts/snapshot-release.sh [label]        e.g.  scripts/snapshot-release.sh wpe-staging
#
# The zip holds only the public site files (no sources, no private folder, nothing from the machine). Restore = unzip it
# over the web folder. The release notes name the source commit, so the same version can be rebuilt from the public repo.
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
REPO="techspressionism/techspressionism-archive-snapshots"
LABEL="${1:-snapshot}"
[ -f site/index.html ] && [ -d site/pagefind ] || { echo "Build the site first: scripts/06-build-site.py (with the search index)."; exit 1; }
[ -z "$(git status --porcelain -- . ':!private' | head -1)" ] || echo "Note: uncommitted changes exist; the notes name the last commit."
SHA="$(git rev-parse --short HEAD)"
TAG="$(date +%Y-%m-%d-%H%M)-$LABEL"
TMP="$(mktemp -d)"
ZIP="$TMP/tva-site-$TAG.zip"
( cd site && zip -qr "$ZIP" . -x '*.DS_Store' )
BASE="$(python3 -c "import json;print(json.load(open('data/site-config.json')).get('canonical_base') or '(none: test build)')")"
NOISE="$(python3 -c "import json;print('noindex' if json.load(open('data/site-config.json')).get('beta_noindex') else 'indexable')")"
N="$(python3 -c "import json;print(len(json.load(open('corpus/corpus.json'))))")"
gh release create "$TAG" "$ZIP" --repo "$REPO" --title "$TAG" \
  --notes "Built site of the Techspressionism Video Archive. Source commit: $SHA (public repo techspressionism/techspressionism-archive). Recordings: $N. Canonical address: $BASE. Search engines: $NOISE." \
  && echo "Snapshot $TAG saved to $REPO ($(du -h "$ZIP" | cut -f1))."
rm -rf "$TMP"
