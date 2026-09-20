#!/bin/zsh
# Run before every push: test every outbound link (artist websites, Instagram, Wikipedia, techspressionism.com pages, the YouTube
# videos), so that nothing broken or parked goes live. A link checked in the last day is not fetched again, so only the first
# run of a day is slow. Broken and parked links are then left out of the pages by 06-build-site.py; the results are saved in
# data/link-status.json and review/broken-links.csv and must be committed with the push.
#
#   scripts/pre-push-check.sh
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
"$PY" scripts/check-people-links.py --max-age 1 || exit 1
git add data/link-status.json review/broken-links.csv 2>/dev/null
echo "Link check done. data/link-status.json and review/broken-links.csv are staged: commit them with the rest of the push."
