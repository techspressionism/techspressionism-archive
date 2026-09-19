#!/usr/bin/env python3
"""NameReview -- put a name on each voice in a recording, once, and every word that voice
speaks is attributed. Stage 3d (03d-diarize.py) separates the voices; this page is where a person
confirms who is who.

    python3 scripts/namereview.py                 # then open http://127.0.0.1:8765
    python3 scripts/namereview.py --port 9000
    python3 scripts/namereview.py --host <private address>   # only an address that nobody but you can reach

Serves only on this Mac by default. There is no login: bind to an address only you can reach.

For each voice you see how much it speaks, up to three short audio clips with the words spoken,
the computer's candidate name(s) and where they came from, and a name box. Choices are saved to
data/voice-names/<slug>.json (small, safe to commit: "voice V3 is Tommy Mintz in this recording";
nothing that identifies a voice across recordings). Stage 5 reads that file. A decision is stored
with a fingerprint of the voice's speech; if 03d is re-run and the voices change, old decisions
are shown as STALE instead of being applied to the wrong voice.

Standard library only. Clips are cut with ffmpeg from the recording's audio (local file when there
is one, else downloaded from YouTube once and kept in raw/review_audio/).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from lib_media import label, slug  # noqa: E402
from lib_speakers import canonical_name  # noqa: E402

DIARIZE_DIR = ROOT / "raw" / "diarize"
DECISIONS_DIR = ROOT / "data" / "voice-names"
CLIP_DIR = ROOT / "raw" / "review_clips"
AUDIO_DIR = ROOT / "raw" / "review_audio"
WHISPER_DIR = ROOT / "raw" / "whisper"
CLIPS_PER_VOICE, CLIP_SECONDS = 3, 9.0
SLUG_RE = re.compile(r"^(salon|interview|roundtable|presentation)-\d{3}$")
VOICE_RE = re.compile(r"^V\d{1,3}$")

os.environ["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
_sessions = {slug(s): s for s in json.load(open(ROOT / "data" / "sessions.json"))}
_stage3 = None


def stage3():
    global _stage3
    if _stage3 is None:
        spec = importlib.util.spec_from_file_location("stage3", HERE / "03-whisper-transcribe.py")
        _stage3 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_stage3)
    return _stage3


# ------------------------------------------------------------------ data

def fingerprint(turns, voice):
    """Changes if 03d is re-run and this voice is no longer the same stretch of speech."""
    mine = [(round(s), round(e)) for s, e, v in turns if v == voice]
    return hashlib.sha1(json.dumps(mine[:40]).encode()).hexdigest()[:10]


def load_decisions(sl):
    p = DECISIONS_DIR / f"{sl}.json"
    return json.loads(p.read_text()) if p.exists() else {"approved_auto": False, "voices": {}}


def save_decisions(sl, data):
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    (DECISIONS_DIR / f"{sl}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def suggestions(sl, voices):
    session = _sessions[sl]
    names = {x["name"] for x in session.get("speakers", [])}
    for key in ("moderator", "interviewer", "interviewee"):
        if session.get(key):
            names.add(session[key])
    for v in voices.values():
        names |= {n for n in (v.get("screen"), v.get("index"), v.get("candidate")) if n}
    return sorted(names)


def remember_alias(screen_name, person):
    """"On screen 'C B Rubin' means Cynthia Beth Rubin": add it to data/speaker_aliases.json so every
    later recording resolves that display name the same way. Never overwrites an existing entry."""
    screen_name = re.sub(r"\s+", " ", screen_name).strip()[:80]
    if not screen_name or not person or screen_name.lower() == person.lower():
        return None
    path = ROOT / "data" / "speaker_aliases.json"
    cfg = json.loads(path.read_text())
    existing = {k.strip().lower(): v for k, v in cfg["aliases"].items()}
    if screen_name.lower() in existing:
        return "already there" if existing[screen_name.lower()] == person else f"not changed: already means {existing[screen_name.lower()]!r}"
    cfg["aliases"][screen_name] = person
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))     # same formatting the file already has
    tmp.replace(path)
    return "remembered"


def all_names():
    names = set(json.load(open(ROOT / "data" / "speaker_aliases.json"))["aliases"].values())
    names |= {a["name"] for a in json.load(open(ROOT / "data" / "artists.json")) if a.get("name")}
    return sorted(names)


def transcript_at(sl, start, end):
    """The words spoken in a clip: Whisper if there is one, else the recording's own transcript."""
    p = WHISPER_DIR / f"{sl}.json"
    if p.exists():
        return " ".join(w["text"] for w in json.loads(p.read_text())["words"] if start - 0.2 <= w["start"] <= end)
    p = ROOT / "raw" / "transcripts" / f"{sl}.json"
    if not p.exists():
        return ""
    data = json.loads(p.read_text())
    if data.get("words"):
        return " ".join(w["text"] for w in data["words"] if start - 0.2 <= w["start"] <= end)
    return " ".join(c["text"] for c in data.get("cues", []) if c["start"] < end and (c.get("end") or c["start"] + 2) > start)


def pick_clips(turns, voice):
    """Up to CLIPS_PER_VOICE clean, well-spread stretches where only this voice speaks."""
    others = sorted((s, e) for s, e, v in turns if v != voice)

    def overlap(a, b):
        return sum(max(0, min(b, e) - max(a, s)) for s, e in others if s < b and e > a)

    mine = sorted(((e - s, s, e) for s, e, v in turns if v == voice and e - s >= 5.0), reverse=True)
    chosen = []
    for length, s, e in mine:
        a, b = s + 0.4, min(e, s + 0.4 + CLIP_SECONDS)
        if overlap(a, b) > 0.15 * (b - a):
            continue
        if all(abs(a - c[0]) > 300 for c in chosen):
            chosen.append((a, b))
        if len(chosen) == CLIPS_PER_VOICE:
            break
    if len(chosen) < CLIPS_PER_VOICE:                 # short talkers: relax the spacing
        for length, s, e in mine:
            a, b = s + 0.4, min(e, s + 0.4 + CLIP_SECONDS)
            if (a, b) not in chosen and all(abs(a - c[0]) > 30 for c in chosen):
                chosen.append((a, b))
            if len(chosen) == CLIPS_PER_VOICE:
                break
    return sorted(chosen)


def recording(sl):
    d = json.loads((DIARIZE_DIR / f"{sl}.json").read_text())
    turns = [tuple(t) for t in d["turns"]]
    dec = load_decisions(sl)
    total = sum(v["seconds"] for v in d["voices"].values()) or 1
    out = []
    for voice, info in sorted(d["voices"].items(), key=lambda kv: -kv[1]["seconds"]):
        clips = [{"start": round(a, 1), "end": round(b, 1), "text": transcript_at(sl, a, b),
                  "url": f"/clip/{sl}/{voice}/{i}.wav"} for i, (a, b) in enumerate(pick_clips(turns, voice))]
        saved = dec["voices"].get(voice)
        fp = fingerprint(turns, voice)
        out.append({"voice": voice, "seconds": info["seconds"], "share": round(100 * info["seconds"] / total, 1),
                    "tier": info.get("tier", "none"), "auto_name": info.get("name") if info.get("tier") == "confirmed" else None,
                    "candidate": info.get("candidate"), "screen": info.get("screen"), "index": info.get("index"),
                    "why": info.get("why", ""), "votes": info.get("votes", {}), "clips": clips, "fingerprint": fp,
                    "saved": None if not saved else {**saved, "stale": saved.get("fingerprint") != fp}})
    s = _sessions[sl]
    return {"slug": sl, "label": label(s), "title": s.get("session_title") or "", "video_id": s["video_id"],
            "approved_auto": dec.get("approved_auto", False), "voices": out,
            "suggestions": suggestions(sl, d["voices"])}


def listing():
    rows = []
    for p in sorted(DIARIZE_DIR.glob("*.json")):
        sl = p.stem
        if sl not in _sessions:
            continue
        d = json.loads(p.read_text())
        turns = [tuple(t) for t in d["turns"]]
        dec = load_decisions(sl)
        done = sum(1 for v in d["voices"] if v in dec["voices"] and dec["voices"][v].get("fingerprint") == fingerprint(turns, v))
        secs = {v: i["seconds"] for v, i in d["voices"].items()}
        rows.append({"slug": sl, "label": label(_sessions[sl]), "title": _sessions[sl].get("session_title") or "",
                     "voices": len(secs), "decided": done, "auto": dec.get("approved_auto", False),
                     "share_decided": round(100 * sum(secs[v] for v in secs if v in dec["voices"]) / (sum(secs.values()) or 1))})
    return rows


# ------------------------------------------------------------------ audio clips

def recording_audio(sl):
    """16 kHz wav of the whole recording, made once and kept while it is being reviewed."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    wav = AUDIO_DIR / f"{sl}.wav"
    if wav.exists():
        return wav
    s3, session = stage3(), _sessions[sl]
    local = s3.find_local_audio(session)
    if local:
        s3.resample_to_whisper_wav(local, wav)
    else:
        raw = s3.extract_audio_via_ytdlp(session["video_id"], AUDIO_DIR)
        s3.resample_to_whisper_wav(raw, wav)
        raw.unlink()
    return wav


def clip_file(sl, voice, i):
    rec = recording(sl)
    v = next(x for x in rec["voices"] if x["voice"] == voice)
    c = v["clips"][i]
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    out = CLIP_DIR / f"{sl}_{voice}_{i}_{v['fingerprint']}.wav"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(c["start"]), "-t", str(c["end"] - c["start"]),
                        "-i", str(recording_audio(sl)), "-ac", "1", str(out)], check=True)
    return out


# ------------------------------------------------------------------ web page

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin"><title>NameReview</title><style>
:root{--bg:#fafaf7;--card:#fff;--ink:#1c1c1a;--mute:#6b6b66;--line:#e2e0d8;--acc:#b0392b;--ok:#2f7d4f;--warn:#a86a00}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#eeeeea;--mute:#a3a39b;--line:#34342f;--acc:#e0705f;--ok:#5fbf85;--warn:#e0a850}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif}main{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:1.3rem;margin:.2rem 0}a{color:var(--acc)}.mute{color:var(--mute)}.small{font-size:.85rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.grow{flex:1}
.badge{font-size:.75rem;border-radius:999px;padding:2px 9px;border:1px solid var(--line)}
.confirmed{color:var(--ok);border-color:var(--ok)}.single,.conflict,.stale{color:var(--warn);border-color:var(--warn)}
input[type=text]{width:100%;box-sizing:border-box;font:inherit;padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
button{font:inherit;padding:8px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.pri{background:var(--acc);color:#fff;border-color:var(--acc)}audio{width:100%;height:34px}
.clip{margin:8px 0}.clip q{display:block;color:var(--mute);font-size:.88rem;margin-top:2px}
.vid{position:sticky;top:0;z-index:5;background:var(--bg);padding:6px 0 8px}
.vid .frame{aspect-ratio:16/9;width:100%;max-height:38vh;background:#000;border-radius:10px;overflow:hidden;margin:0 auto}
.vid .frame iframe{width:100%;height:100%;border:0}.vid.off .frame{display:none}
button.seek{padding:4px 10px;font-size:.85rem}
.saved{color:var(--ok);font-weight:600}.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin:6px 0}.bar i{display:block;height:100%;background:var(--acc)}
</style></head><body><main id="app">Loading…</main><script>
const $=(s,e=document)=>e.querySelector(s), api=(u,o)=>fetch(u,o).then(r=>r.json());
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function home(){
  const rows=await api('/api/list');
  $('#app').innerHTML=`<h1>NameReview</h1><p class="mute small">Recordings whose voices have been separated. Open one and say who each voice is; every word that voice speaks is then attributed.</p>`+
   (rows.length?rows.map(r=>`<a class="card row" href="#/${r.slug}" style="text-decoration:none;color:inherit"><div class="grow"><b>${esc(r.label)}</b> <span class="mute">${esc(r.title)}</span>
   <div class="bar"><i style="width:${r.share_decided}%"></i></div><span class="small mute">${r.decided} of ${r.voices} voices decided · ${r.share_decided}% of speech</span></div>
   ${r.auto?'<span class="badge confirmed">auto-names approved</span>':''}</a>`).join(''):'<p>No separated recordings yet.</p>');
}
async function page(sl){
  const d=await api('/api/rec/'+sl);
  const dl='<datalist id="names">'+[...new Set([...d.suggestions])].map(n=>`<option value="${esc(n)}">`).join('')+'</datalist>';
  $('#app').innerHTML=`<p><a href="#/">← all recordings</a></p><h1>${esc(d.label)} <span class="mute">${esc(d.title)}</span></h1>
   <div class="vid" id="vidbox"><div class="row"><button class="seek" id="vidtoggle">Hide video</button><span class="small mute" id="vidnote">Press <b>▶ Show in video</b> under a clip to watch that moment here.</span></div>
    <div class="frame"><div id="yt"></div></div></div>
   <p class="small mute">Play the clips. If the suggested name is right, press <b>Confirm</b>; otherwise type the right name (suggestions appear as you type). Choose <b>Leave unattributed</b> if you can't tell — that is always safe.</p>
   <div class="card row"><label class="grow"><input type="checkbox" id="auto" ${d.approved_auto?'checked':''}> Approve the automatically confirmed names (both the screen and the speaker list agreed) for this recording</label></div>${dl}`+
   d.voices.map(v=>{
     const guess=v.saved&&!v.saved.stale?v.saved.name:(v.auto_name||v.candidate||'');
     const badge=v.saved&&!v.saved.stale?'<span class="badge confirmed">decided</span>':v.saved?'<span class="badge stale">STALE decision — voices changed</span>':v.tier==='confirmed'?'<span class="badge confirmed">2 sources agree</span>':v.tier==='conflict'?'<span class="badge conflict">sources disagree</span>':v.tier==='single'?'<span class="badge single">1 source</span>':'<span class="badge">no guess</span>';
     return `<div class="card" data-v="${v.voice}" data-fp="${v.fingerprint}"><div class="row"><b>${v.voice}</b><span class="mute">${(v.seconds/60).toFixed(1)} min · ${v.share}% of the recording</span>${badge}</div>
      <div class="bar"><i style="width:${Math.min(100,v.share*3)}%"></i></div>
      <div class="small mute">On screen while this voice speaks: ${Object.entries(v.votes||{}).slice(0,3).map(([n,c])=>`${esc(n)} (${c})`).join(', ')||'nothing readable'} · Speaker list: ${esc(v.index)||'—'}</div>
      <div class="small mute">${esc(v.why)}</div>
      ${v.clips.map(c=>`<div class="clip"><audio controls preload="none" src="${c.url}"></audio><q>${esc(c.text)||'(no transcript here)'}
        <button class="seek" data-t="${c.start}">▶ Show in video (${Math.floor(c.start/60)}:${String(Math.floor(c.start%60)).padStart(2,'0')})</button>
        <a class="small" href="https://www.youtube.com/watch?v=${d.video_id}&t=${Math.floor(c.start)}s" target="_blank" rel="noopener">open in YouTube ↗</a></q></div>`).join('')||'<p class="mute small">No clean stretch to play for this voice.</p>'}
      <div class="row" style="margin-top:8px"><div style="flex:1 1 100%"><input type="text" list="names" placeholder="Who is this?" value="${esc(guess)}"></div>
       ${Object.keys(v.votes||{})[0]?`<label class="small mute" style="flex:1 1 100%"><input type="checkbox" class="alias" data-from="${esc(Object.keys(v.votes)[0])}"> Remember that the on-screen name “${esc(Object.keys(v.votes)[0])}” means this person in future recordings</label>`:''}
       <button class="pri" data-act="name">${guess?'Confirm':'Save'}</button><button data-act="none">Leave unattributed</button><span class="saved" hidden>saved ✓</span></div></div>`}).join('');
  loadPlayer(d.video_id);
  $('#vidtoggle').onclick=()=>{const off=$('#vidbox').classList.toggle('off');$('#vidtoggle').textContent=off?'Show video':'Hide video'};
  document.querySelectorAll('button.seek[data-t]').forEach(b=>b.onclick=()=>{
    document.querySelectorAll('audio').forEach(a=>a.pause());                 // one sound at a time
    $('#vidbox').classList.remove('off');$('#vidtoggle').textContent='Hide video';
    if(player&&playerReady){const t=Math.max(0,+b.dataset.t-3),st=player.getPlayerState();   // 3 s lead-in, same as the site's watch links
      if(st===1||st===2)player.seekTo(t,true);else player.loadVideoById({videoId:d.video_id,startSeconds:t});   // a fresh player must be loaded at the time
      player.playVideo()}
    else window.open(`https://www.youtube.com/watch?v=${d.video_id}&t=${Math.floor(b.dataset.t)}s`,'_blank','noopener')});
  document.querySelectorAll('audio').forEach(a=>a.onplay=()=>{if(player&&playerReady&&player.pauseVideo)player.pauseVideo()});
  $('#auto').onchange=e=>api('/api/approve',{method:'POST',body:JSON.stringify({slug:sl,approved_auto:e.target.checked})});
  document.querySelectorAll('.card[data-v] button[data-act]').forEach(b=>b.onclick=async()=>{   // only Confirm/Save/Leave, never the ▶ buttons
    const card=b.closest('.card'), name=b.dataset.act==='none'?'':card.querySelector('input').value.trim();
    if(b.dataset.act==='name'&&!name){card.querySelector('input').focus();return}
    const al=card.querySelector('.alias'), from=(al&&al.checked&&name)?al.dataset.from:'';
    const r=await api('/api/decision',{method:'POST',body:JSON.stringify({slug:sl,voice:card.dataset.v,name:name,fingerprint:card.dataset.fp,alias_from:from})});
    const s=card.querySelector('.saved');s.textContent='saved ✓'+(r.alias?` (on-screen name ${r.alias})`:'');s.hidden=false;setTimeout(()=>s.hidden=true,4000);});
}
let player=null, playerReady=false;
function loadPlayer(id){                                  // YouTube's own embedded player, seekable from the page
  playerReady=false; if(player&&player.destroy){try{player.destroy()}catch(e){}} player=null;
  const make=()=>{player=new YT.Player('yt',{videoId:id,width:'100%',height:'100%',playerVars:{rel:0,playsinline:1,modestbranding:1},events:{onReady:()=>{playerReady=true},
      onError:e=>{playerReady=false;window.ytError=e.data;const n=$('#vidnote');if(n)n.textContent=`The embedded player reported an error (code ${e.data}), so ▶ opens the video in YouTube at that moment instead.`}}})};
  if(window.YT&&YT.Player){make()}
  else{window.onYouTubeIframeAPIReady=make;if(!document.getElementById('ytapi')){const s=document.createElement('script');s.id='ytapi';s.src='https://www.youtube.com/iframe_api';document.head.appendChild(s)}}
}
const route=()=>{const m=location.hash.match(/^#\/(.+)$/);m?page(m[1]):home()};addEventListener('hashchange',route);route();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json", code=200):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/":
                return self._send(PAGE, "text/html")
            if path == "/api/list":
                return self._send(listing())
            m = re.fullmatch(r"/api/rec/([a-z]+-\d{3})", path)
            if m and SLUG_RE.match(m[1]) and (DIARIZE_DIR / f"{m[1]}.json").exists():
                return self._send(recording(m[1]))
            m = re.fullmatch(r"/clip/([a-z]+-\d{3})/(V\d+)/(\d)\.wav", path)
            if m and SLUG_RE.match(m[1]) and VOICE_RE.match(m[2]) and (DIARIZE_DIR / f"{m[1]}.json").exists():
                return self._send(clip_file(m[1], m[2], int(m[3])).read_bytes(), "audio/wav")
            self._send({"error": "not found"}, code=404)
        except Exception as e:  # keep the server up; show the reason
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 20000)) or b"{}")
            sl = body.get("slug", "")
            if not SLUG_RE.match(sl) or not (DIARIZE_DIR / f"{sl}.json").exists():
                return self._send({"error": "unknown recording"}, code=400)
            dec = load_decisions(sl)
            if self.path == "/api/approve":
                dec["approved_auto"] = bool(body.get("approved_auto"))
            elif self.path == "/api/decision":
                voice = body.get("voice", "")
                if not VOICE_RE.match(voice):
                    return self._send({"error": "bad voice"}, code=400)
                name = re.sub(r"\s+", " ", str(body.get("name", ""))).strip()[:80]
                dec["voices"][voice] = {"name": canonical_name(name) if name else "", "fingerprint": str(body.get("fingerprint", "")),
                                        "at": time.strftime("%Y-%m-%d %H:%M")}
                alias = remember_alias(str(body.get("alias_from", "")), dec["voices"][voice]["name"])
                if alias:
                    save_decisions(sl, dec)
                    return self._send({"ok": True, "alias": alias})
            else:
                return self._send({"error": "not found"}, code=404)
            save_decisions(sl, dec)
            self._send({"ok": True})
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description="NameReview: review and name the voices found by 03d-diarize.py")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if not DIARIZE_DIR.exists():
        sys.exit("nothing to review yet: run 03d-diarize.py first")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"NameReview: http://{args.host}:{args.port}   (Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
