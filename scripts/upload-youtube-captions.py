#!/usr/bin/env python3
"""Upload the archive's caption files (scripts/make-youtube-captions.py) to the recordings' YouTube videos, through the YouTube Data API.

Uses only Python's standard library (nothing to install). One-time setup is in the admin manual ("Uploading captions to YouTube"):
a Google Cloud project with the YouTube Data API on, an OAuth client of type "Desktop app" saved as
private/youtube-oauth/client_secret.json, and one sign-in by the channel owner.

    python3 scripts/upload-youtube-captions.py --login            # sign in once (opens your browser); Colin runs this himself
    python3 scripts/upload-youtube-captions.py --status           # what is uploaded, what is left, what a day's quota allows
    python3 scripts/upload-youtube-captions.py                    # REHEARSAL: lists what would be uploaded, touches nothing
    python3 scripts/upload-youtube-captions.py --go --only salon-081        # upload one, to try it
    python3 scripts/upload-youtube-captions.py --go --limit 20    # a day's batch (stops by itself when the day's quota is used up)

Options: --order newest|oldest (default newest)   --language en   --name "English"   --replace-existing   --limit N (default 20)

What it does and does not do. It adds an English caption track to each video. If the video already has a track with that language and
name, that video is SKIPPED and listed (your own earlier caption files are never overwritten) unless --replace-existing is given, which
updates that existing uploaded track instead. YouTube's automatic captions are never touched and nothing is ever deleted. A file that
changes later (a corrected transcript, re-made with make-youtube-captions.py) is updated in place, using the track id remembered in
private/youtube-captions/upload-state.json. The default daily API quota (10,000 units) allows about 25 uploads: an upload costs 400
units, an update 450, looking up an existing track 50. When the quota runs out the script stops cleanly; run it again the next day
(the quota resets at midnight Pacific time).
"""
import base64
import csv
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OAUTH_DIR = ROOT / "private" / "youtube-oauth"
CLIENT_FILE = OAUTH_DIR / "client_secret.json"
TOKEN_FILE = OAUTH_DIR / "token.json"
CAPTIONS = ROOT / "private" / "youtube-captions"
STATE_FILE = CAPTIONS / "upload-state.json"
API = os.environ.get("YT_API_BASE", "https://www.googleapis.com")
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
COST = {"insert": 400, "update": 450, "list": 50}
DAILY_QUOTA = 10000


class QuotaExhausted(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, reason, message):
        super().__init__(f"{status} {reason}: {message}")
        self.status, self.reason, self.message = status, reason, message


# ---------------------------------------------------------------- sign-in
def load_client():
    if not CLIENT_FILE.exists():
        sys.exit(f"Missing {CLIENT_FILE.relative_to(ROOT)}. Create an OAuth client (type: Desktop app) in the Google Cloud console and save its JSON there "
                 "(see the admin manual, \"Uploading captions to YouTube\").")
    data = json.loads(CLIENT_FILE.read_text())
    c = data.get("installed") or data.get("web") or {}
    for k in ("client_id", "client_secret"):
        if not c.get(k):
            sys.exit(f"{CLIENT_FILE.name} has no {k}: download the JSON of a Desktop-app OAuth client.")
    c.setdefault("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth")
    c.setdefault("token_uri", os.environ.get("YT_TOKEN_URI", "https://oauth2.googleapis.com/token"))
    return c


def post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(body)
        except ValueError:
            j = {}
        raise ApiError(e.code, j.get("error", "http_error"), j.get("error_description", body[:200]))


def login():
    c = load_client()
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body style='font-family:sans-serif'><h3>Signed in.</h3><p>You can close this tab and go back to the terminal.</p></body></html>")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = c["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
    print("Opening your browser to sign in with the Google account that owns the YouTube channel.\nIf it does not open, copy this address into a browser:\n\n" + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    t.join(300)
    server.server_close()
    if got.get("state") != state or "code" not in got:
        sys.exit("Sign-in was not completed (" + (got.get("error") or "no answer within 5 minutes") + ").")
    tok = post_form(c["token_uri"], {"code": got["code"], "client_id": c["client_id"], "client_secret": c["client_secret"],
                                     "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if not tok.get("refresh_token"):
        sys.exit("Google did not return a refresh token. Remove this app under https://myaccount.google.com/permissions and run --login again.")
    OAUTH_DIR.mkdir(parents=True, exist_ok=True)
    save_token({"refresh_token": tok["refresh_token"], "access_token": tok["access_token"], "expires_at": time.time() + int(tok.get("expires_in", 3600))})
    print("Signed in. The permission is stored in", TOKEN_FILE.relative_to(ROOT), "(private, never committed).")


def save_token(t):
    TOKEN_FILE.write_text(json.dumps(t))
    os.chmod(TOKEN_FILE, 0o600)


def access_token():
    if not TOKEN_FILE.exists():
        sys.exit("Not signed in yet: run  python3 scripts/upload-youtube-captions.py --login")
    t = json.loads(TOKEN_FILE.read_text())
    if t.get("access_token") and t.get("expires_at", 0) > time.time() + 60:
        return t["access_token"]
    c = load_client()
    try:
        r = post_form(c["token_uri"], {"client_id": c["client_id"], "client_secret": c["client_secret"], "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
    except ApiError as e:
        if e.reason == "invalid_grant":
            sys.exit("The stored sign-in has expired (Google expires it after 7 days while the app is in \"Testing\" mode). Run --login again.")
        raise
    t.update({"access_token": r["access_token"], "expires_at": time.time() + int(r.get("expires_in", 3600))})
    save_token(t)
    return t["access_token"]


# ---------------------------------------------------------------- API
def api(method, path, params=None, body=None, content_type=None, retries=3):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(retries):
        headers = {"Authorization": "Bearer " + access_token()}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(raw).get("error", {})
                reason = (err.get("errors") or [{}])[0].get("reason", "") or err.get("status", "")
                msg = err.get("message", raw[:200])
            except ValueError:
                reason, msg = "", raw[:200]
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise QuotaExhausted(msg)
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise ApiError(e.code, reason, msg)
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise ApiError(0, "network", str(e))


def multipart(meta, data, media_type="application/octet-stream"):
    b = "tva" + secrets.token_hex(12)
    body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{b}\r\nContent-Type: {media_type}\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/related; boundary={b}"


def insert_caption(video_id, data, lang, name):
    body, ct = multipart({"snippet": {"videoId": video_id, "language": lang, "name": name, "isDraft": False}}, data)
    return api("POST", "/upload/youtube/v3/captions", {"part": "snippet", "uploadType": "multipart"}, body, ct)


def update_caption(caption_id, data, lang, name):
    body, ct = multipart({"id": caption_id, "snippet": {"isDraft": False}}, data)
    return api("PUT", "/upload/youtube/v3/captions", {"part": "snippet", "uploadType": "multipart"}, body, ct)


def find_track(video_id, lang, name):
    r = api("GET", "/youtube/v3/captions", {"part": "snippet", "videoId": video_id})
    for it in r.get("items", []):
        sn = it.get("snippet", {})
        if sn.get("language") == lang and sn.get("trackKind", "standard") != "asr" and sn.get("name", "") == name:
            return it["id"]
    return None


# ---------------------------------------------------------------- state
def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(st):
    STATE_FILE.write_text(json.dumps(st, indent=1, sort_keys=True))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def recordings(order):
    index = CAPTIONS / "index.csv"
    if not index.exists():
        sys.exit("No caption files yet: run  python3 scripts/make-youtube-captions.py")
    rows = list(csv.DictReader(open(index)))
    dates = {}
    for e in json.loads((ROOT / "corpus" / "corpus.json").read_text()):
        dates[f"{e.get('type', 'salon')}-{int(e['number']):03d}"] = e.get("date_recorded") or ""
    rows.sort(key=lambda r: (dates.get(r["slug"], ""), r["slug"]), reverse=(order != "oldest"))
    return rows


def status(rows, st):
    done = [r for r in rows if st.get(r["slug"], {}).get("status") == "uploaded" and st[r["slug"]].get("sha") == sha(CAPTIONS / r["file"])]
    changed = [r for r in rows if st.get(r["slug"], {}).get("status") == "uploaded" and st[r["slug"]].get("sha") != sha(CAPTIONS / r["file"])]
    exists = [r for r in rows if st.get(r["slug"], {}).get("status") == "exists"]
    failed = [r for r in rows if st.get(r["slug"], {}).get("status") == "error"]
    todo = len(rows) - len(done) - len(exists) - len(failed)
    print(f"{len(rows)} recordings: {len(done)} uploaded and current, {len(changed)} uploaded but changed since, {len(exists)} skipped (a track already exists), "
          f"{len(failed)} failed, {todo} still to do.")
    print(f"A day's default quota ({DAILY_QUOTA:,} units) covers about {DAILY_QUOTA // COST['insert']} new uploads, so about {-(-max(todo, 0) // (DAILY_QUOTA // COST['insert']))} more day(s).")
    for r in exists:
        print("  already has a track:", r["slug"], r["video_id"])
    for r in failed:
        print("  failed:", r["slug"], st[r["slug"]].get("error"))


def main():
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d
    if "--login" in a:
        return login()
    order, lang, name = opt("--order", "newest"), opt("--language", "en"), opt("--name", "English")
    limit, only, go, replace = int(opt("--limit", 20)), opt("--only"), "--go" in a, "--replace-existing" in a
    rows, st = recordings(order), load_state()
    if "--status" in a:
        return status(rows, st)
    if only:
        rows = [r for r in rows if r["slug"] == only]
        if not rows:
            sys.exit(f"No caption file for {only}")
    todo = []
    for r in rows:
        s, f = st.get(r["slug"], {}), CAPTIONS / r["file"]
        if s.get("status") == "uploaded" and s.get("sha") == sha(f):
            continue
        if s.get("status") == "exists" and not replace:
            continue
        if s.get("status") == "error" and not only:
            pass                                    # a failed one is tried again
        todo.append(r)
    todo = todo[:limit]
    print(f"{'UPLOADING' if go else 'REHEARSAL (nothing is sent; add --go to upload)'}: {len(todo)} of {len(rows)} recordings, {order} first, language {lang}, track name \"{name}\".")
    if not go:
        for r in todo:
            s = st.get(r["slug"], {})
            print(f"  {r['slug']:18} {r['video_id']}  {r['cues']} cues  {'update' if s.get('caption_id') else 'new'}")
        return
    used = 0
    for r in todo:
        slug, f = r["slug"], CAPTIONS / r["file"]
        data = f.read_bytes()
        s = st.setdefault(slug, {})
        try:
            if s.get("caption_id"):
                res, act = update_caption(s["caption_id"], data, lang, name), "update"
                used += COST["update"]
            else:
                try:
                    res, act = insert_caption(r["video_id"], data, lang, name), "insert"
                    used += COST["insert"]
                except ApiError as e:
                    if e.status == 409 or e.reason in ("nameConflict", "captionExists", "trackExists", "duplicate"):
                        used += COST["insert"]
                        if not replace:
                            s.update({"status": "exists", "video_id": r["video_id"]})
                            save_state(st)
                            print(f"  {slug}: a {lang} track named \"{name}\" already exists: skipped (use --replace-existing to update it)")
                            continue
                        cid = find_track(r["video_id"], lang, name)
                        used += COST["list"]
                        if not cid:
                            raise
                        res, act = update_caption(cid, data, lang, name), "replace"
                        used += COST["update"]
                    else:
                        raise
            s.update({"status": "uploaded", "sha": sha(f), "caption_id": res.get("id", s.get("caption_id")), "video_id": r["video_id"],
                      "when": time.strftime("%Y-%m-%d %H:%M:%S"), "action": act})
            s.pop("error", None)
            save_state(st)
            print(f"  {slug}: {act} ok ({r['cues']} cues)")
            time.sleep(1)
        except QuotaExhausted:
            save_state(st)
            print(f"\nThe day's YouTube quota is used up (about {used:,} units this run). Run the same command tomorrow; it carries on where it stopped.")
            return
        except ApiError as e:
            s.update({"status": "error", "error": str(e)[:300], "video_id": r["video_id"]})
            save_state(st)
            print(f"  {slug}: FAILED {e}")
    print("\nDone for this run. Check them in YouTube Studio > the video > Subtitles.")
    status(recordings(order), load_state())


if __name__ == "__main__":
    main()
