#!/bin/zsh
# "Push live": everything needed to put the current commit on techspressionism.com/archive, in one command.
#
#   scripts/push-live.sh            # the whole thing
#   scripts/push-live.sh --rehearse # every step up to and including the rehearsal; copies nothing
#
# Steps: 1 check the commit is on GitHub and its staging deploy succeeded; 2 production build; 3 rollback snapshot;
# 4 rehearsal (stops if it would delete anything important); 5 the real copy to WP Engine; 6 check the live site.
# Needs the WP Engine key unlocked (ssh-add --apple-use-keychain ~/.ssh/id_ed25519_wpengine) and `gh` logged in.
# Nothing secret is stored here. Cache clearing (WP Engine, Cloudflare) is a manual step, printed at the end.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
INSTALL="${WPE_INSTALL:-techspression}"
BASE="https://techspressionism.com/archive"
REPO="techspressionism/techspressionism-archive"
REHEARSE_ONLY=0; [[ "${1:-}" == "--rehearse" ]] && REHEARSE_ONLY=1
step() { print -P "\n%F{red}== $1%f"; }
die()  { print -P "%F{red}STOPPED: $1%f"; exit 1; }

step "1/6  Is this commit on GitHub, and did staging deploy?"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"; [[ "$BRANCH" == "main" ]] || die "on branch $BRANCH, not main"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || die "uncommitted changes to tracked files: commit and push to staging first"
git fetch -q origin || die "could not reach GitHub"
SHA="$(git rev-parse HEAD)"; SHORT="$(git rev-parse --short HEAD)"
[[ "$SHA" == "$(git rev-parse origin/main)" ]] || die "$SHORT is not the same as origin/main: push to staging first"
RUN="$(curl -s "https://api.github.com/repos/$REPO/actions/runs?head_sha=$SHA&per_page=1" | python3 -c "import sys,json; r=json.loads(sys.stdin.read(),strict=False).get('workflow_runs') or [{}]; r=r[0]; print(r.get('status','none'), r.get('conclusion'))")"
[[ "$RUN" == "completed success" ]] || die "staging deploy for $SHORT is '$RUN' (wait for success, then run again)"
print "commit $SHORT is on GitHub and its staging deploy succeeded"

step "2/6  Production build"
TVA_CANONICAL_BASE="$BASE" python3 scripts/06-build-site.py > /tmp/push-live-build.log 2>&1 || { tail -20 /tmp/push-live-build.log; die "build failed"; }
grep -q "<link rel=\"canonical\" href=\"$BASE/\">" site/index.html || die "built home page does not carry the production canonical address"
[[ -f site/sitemap.xml && -f site/llms.txt && -f site/pagefind/pagefind.js ]] || die "build is missing sitemap.xml, llms.txt or the search index"
print "built: $(grep -c . /tmp/push-live-build.log) log lines, canonical $BASE/"

step "3/6  Rollback snapshot"
scripts/snapshot-release.sh "live-$SHORT" 2>&1 | tail -1 || die "snapshot failed"

step "4/6  Rehearsal (nothing copied yet)"
WPE_INSTALL="$INSTALL" scripts/deploy-wpengine.sh > /tmp/push-live-rehearsal.log 2>&1 || { tail -5 /tmp/push-live-rehearsal.log; die "rehearsal failed (is the WP Engine key unlocked?)"; }
DEL="$(grep -c '^\*deleting' /tmp/push-live-rehearsal.log)"
print "files that would change: $(grep -c '^<f' /tmp/push-live-rehearsal.log); files that would be deleted from live: $DEL"
grep '^\*deleting' /tmp/push-live-rehearsal.log | grep -v '/$' | sed 's/\*deleting */  - /' | head -40
if grep '^\*deleting' /tmp/push-live-rehearsal.log | grep -Eq 'deleting (index\.html|style\.css|sitemap\.xml|llms\.txt|robots\.txt|pagefind/|about/|salons/|artists/)'; then die "the copy would delete an essential file: look at /tmp/push-live-rehearsal.log"; fi
(( DEL <= 100 )) || die "the copy would delete $DEL files (over 100): look at /tmp/push-live-rehearsal.log"
(( REHEARSE_ONLY )) && { print "\nRehearsal only: nothing was copied."; exit 0; }

step "5/6  Copying to WP Engine"
WPE_INSTALL="$INSTALL" scripts/deploy-wpengine.sh --go 2>&1 | tail -3 || die "the copy failed"

step "6/6  Checking the live site"
N="$(date +%s)"; FAIL=0
check() { local code; code="$(curl -s -m 30 -o /dev/null -w '%{http_code}' "$BASE/$1?nocache=$N")"; sleep 1; if [[ "$code" == "200" ]]; then print "  ok   $1"; else print -P "  %F{red}FAIL%f $1 ($code)"; FAIL=1; fi; }
for p in "" sitemap.xml llms.txt robots.txt pagefind/pagefind.js og/default.jpg salons/ artists/ about/ interview-030-carla-gannis/; do check "$p"; done
HOME_HTML="$(curl -s -m 30 "$BASE/?nocache=$N")"
[[ "$HOME_HTML" == *"<link rel=\"canonical\" href=\"$BASE/\">"* ]] && print "  ok   home carries the production canonical" || { print -P "  %F{red}FAIL%f home canonical (old page still cached?)"; FAIL=1; }
[[ "$HOME_HTML" == *'href="artists/"'* ]] && print "  ok   home Artists link goes to the archive Artists page" || { print -P "  %F{yellow}note%f home Artists link is not the new one yet (cache?)"; }
print "$SHORT $(date '+%Y-%m-%d %H:%M')" > private/last-live-build.txt
print "\nSyncing the Notes checklist..."; python3 scripts/update-notes-checklist.py 2>&1 | tail -3 || true
print -P "\n%F{green}Live copy finished for commit $SHORT.%f"
(( FAIL )) && print -P "%F{red}Some checks failed: clear the caches below and run the checks again.%f"
cat <<MSG

Still by hand:
  1. Clear the WP Engine cache (User Portal > Caching > Clear all) and Cloudflare (Purge Everything): the site sends max-age=600.
  2. Search Console / Bing: the archive sitemap is $BASE/sitemap.xml
MSG
