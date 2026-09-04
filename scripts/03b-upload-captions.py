#!/usr/bin/env python3
"""Stage 3b -- push a session's transcript to YouTube as a real caption
track, for sessions where YouTube never generated auto-captions on its own.

NOT RUNNABLE YET. This requires OAuth to Colin's YouTube channel, which has
not been set up -- see README.md "Stage 3b" section for the one-time setup
steps. This script will refuse to run (see check_credentials below) rather
than attempt any authorization flow itself. Setting up OAuth access to a
personal YouTube channel is a deliberate action for Colin to take, not
something to do incidentally while building this pipeline.

Even once auth is configured: uploading a caption track modifies public
content on Colin's channel. Per standing operating rules, that needs
explicit per-run confirmation -- this script should always be invoked with
--dry-run first to review exactly what would be uploaded, never chained
into an unattended batch job.

Usage (once auth is set up):
    python3 scripts/03b-upload-captions.py 87 101 --dry-run
    python3 scripts/03b-upload-captions.py 87 101
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_PATH = ROOT / "data" / "sessions.json"
CORRECTED_DIR = ROOT / "raw" / "transcripts_corrected"
CREDENTIAL_ENV_VAR = "YT_CLIENT_SECRET_PATH"
TOKEN_CACHE_PATH = ROOT / "raw" / ".yt_oauth_token.json"  # gitignored -- raw/ is gitignored wholesale


def check_credentials():
    client_secret = os.environ.get(CREDENTIAL_ENV_VAR)
    if not client_secret or not Path(client_secret).exists():
        print(
            "Stage 3b is not set up yet.\n\n"
            f"No OAuth client secret found (expected path in ${CREDENTIAL_ENV_VAR}).\n"
            "This is a deliberate, one-time setup step -- see README.md 'Stage 3b' "
            "section for how to create a Google Cloud project, enable the YouTube "
            "Data API v3, and generate an OAuth Client ID.\n\n"
            "This script will not attempt to set up auth on its own.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not TOKEN_CACHE_PATH.exists():
        print(
            f"Client secret found, but no cached auth token at {TOKEN_CACHE_PATH}.\n"
            "Run with --auth to do the one-time browser authorization.",
            file=sys.stderr,
        )
        sys.exit(1)
    return client_secret


def cues_to_srt(cues):
    def fmt(seconds):
        if seconds is None:
            return "00:00:00,000"
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        ms = round((s % 1) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"

    lines = []
    for i, cue in enumerate(cues, 1):
        start = fmt(cue["start"])
        end = fmt(cue.get("end") or (cue["start"] + 4))
        speaker_prefix = f"{cue['speaker']}: " if cue.get("speaker") else ""
        lines.append(f"{i}\n{start} --> {end}\n{speaker_prefix}{cue['text']}\n")
    return "\n".join(lines)


def upload_caption_track(video_id, srt_text, dry_run):
    """Upload via YouTube Data API captions.insert. Requires the `google-api-
    python-client` and `google-auth-oauthlib` packages (not yet a listed
    dependency -- add when this stage is actually enabled)."""
    if dry_run:
        print(f"[dry run] would upload {len(srt_text.splitlines())} SRT lines as a caption track for video {video_id}")
        return

    # from googleapiclient.discovery import build
    # from googleapiclient.http import MediaInMemoryUpload
    # youtube = build("youtube", "v3", credentials=load_cached_credentials())
    # youtube.captions().insert(
    #     part="snippet",
    #     body={"snippet": {"videoId": video_id, "language": "en", "name": "Transcript", "isDraft": False}},
    #     media_body=MediaInMemoryUpload(srt_text.encode(), mimetype="application/octet-stream"),
    # ).execute()
    raise NotImplementedError("Stage 3b upload path is scaffolded but not implemented -- OAuth isn't set up yet")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    check_credentials()  # always exits here until Colin completes setup

    with open(SESSIONS_PATH) as f:
        sessions = {str(s["number"]): s for s in json.load(f)}

    for number in args:
        session = sessions.get(number)
        if not session:
            print(f"Salon {number}: not found in {SESSIONS_PATH}", file=sys.stderr)
            continue
        transcript_path = CORRECTED_DIR / f"salon-{int(number):03d}.json"
        if not transcript_path.exists():
            print(f"Salon {number}: no corrected transcript at {transcript_path}, skipping")
            continue
        with open(transcript_path) as f:
            data = json.load(f)
        srt = cues_to_srt(data["cues"])
        upload_caption_track(session["video_id"], srt, dry_run)


if __name__ == "__main__":
    main()
