#!/usr/bin/env python3
"""TextReview -- correct a transcript next to the video.

    python3 scripts/textreview.py                 # then open http://localhost:8766   (say localhost, not 127.0.0.1:
    python3 scripts/textreview.py --port 9000      #  YouTube's embedded player refuses pages opened by an IP number)

Pick a recording, watch the moment in the player at the top (press ▶ beside any paragraph), fix the
words in the box, press Save. Corrections are stored in data/text-edits/<slug>.json, SEPARATE from the
machine transcript, so re-running the pipeline never loses them. An edit records the exact paragraph it
changes; if the machine text later changes so that paragraph no longer exists, the edit is shown as STALE
(never applied to the wrong place). "Apply corrections to the page" rebuilds that recording's page
(Stage 5) so the correction appears in the corpus, the site and search.

Each edit carries who made it and when (kept for crediting volunteers). Edits made here are "approved";
suggestions imported from elsewhere arrive as "suggested" and are only applied once approved.

Standard library only; serves this Mac only by default. There is no login: bind to an address only you can reach.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from lib_media import label, slug  # noqa: E402

CORPUS_DIR = ROOT / "corpus"
EDITS_DIR = ROOT / "data" / "text-edits"
SLUG_RE = re.compile(r"^(salon|interview|roundtable|presentation)-\d{3}$")
HEADING_RE = re.compile(r"^## (.+?) \[([\d:]+)\]\((.+?)\)\s*$")
MAX_TEXT = 20000
_sessions = {slug(s): s for s in json.load(open(ROOT / "data" / "sessions.json"))}


# ------------------------------------------------------------------ data

def load_edits(sl):
    p = EDITS_DIR / f"{sl}.json"
    return json.loads(p.read_text()) if p.exists() else {"edits": []}


def save_edits(sl, data):
    EDITS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = EDITS_DIR / f"{sl}.json.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(EDITS_DIR / f"{sl}.json")


def parse_corpus(sl):
    """[{speaker, time, start, paras:[str]}] from corpus/<slug>.md."""
    text = (CORPUS_DIR / f"{sl}.md").read_text()
    body = text.split("\n---\n", 1)[1] if text.startswith("---") else text
    blocks, cur = [], None
    for line in body.split("\n"):
        m = HEADING_RE.match(line)
        if m:
            t = re.search(r"[?&]t=(\d+)s", m[3])
            cur = {"speaker": m[1], "time": m[2], "start": int(t[1]) if t else 0, "lines": []}
            blocks.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    for b in blocks:
        b["paras"] = [p.strip() for p in "\n".join(b.pop("lines")).split("\n\n") if p.strip()]
    return blocks


def tokens_with_times(sl):
    """(time, lowercase word) from the recording's transcript, for placing each paragraph in the video."""
    p = ROOT / "raw" / "transcripts" / f"{sl}.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text())
    if d.get("words"):
        return [(w["start"], t) for w in d["words"] for t in re.findall(r"[a-z0-9']+", w["text"].lower())]
    out = []
    for c in d.get("cues", []):
        ws = re.findall(r"[a-z0-9']+", c["text"].lower())
        end = c.get("end") or c["start"] + max(len(ws) * 0.35, 1)
        out += [(c["start"] + (end - c["start"]) * i / max(len(ws), 1), w) for i, w in enumerate(ws)]
    return out


def paragraph_times(blocks, toks):
    """Estimate when each paragraph is spoken: find its first words in the transcript near its block."""
    times = [t for t, _ in toks]
    from bisect import bisect_left
    for bi, b in enumerate(blocks):
        nxt = blocks[bi + 1]["start"] if bi + 1 < len(blocks) else b["start"] + 600
        lo = bisect_left(times, b["start"] - 8)
        hi = bisect_left(times, max(nxt, b["start"] + 5) + 8)
        window = toks[lo:hi]
        b["ptimes"] = []
        for para in b["paras"]:
            first = re.findall(r"[a-z0-9']+", para.lower())[:5]
            best = None
            for k in range(len(window) - len(first) + 1 if first else 0):
                score = sum(1 for a, (_, w) in zip(first, window[k:k + len(first)]) if a == w)
                if score >= max(3, len(first) - 1) and (best is None or score > best[0]):
                    best = (score, window[k][0])
                    if score == len(first):
                        break
            b["ptimes"].append(round(best[1], 1) if best else float(b["start"]))


def recording(sl):
    s = _sessions[sl]
    blocks = parse_corpus(sl)
    paragraph_times(blocks, tokens_with_times(sl))
    edits = load_edits(sl)["edits"]
    by_new = {e["new"]: e for e in edits}
    by_old = {e["old"]: e for e in edits}
    present = {p for b in blocks for p in b["paras"]}
    out_blocks = []
    for b in blocks:
        paras = []
        for text, t in zip(b["paras"], b["ptimes"]):
            e = by_new.get(text) or by_old.get(text)
            info = None
            if e:
                info = {**e, "state": "applied" if e["new"] == text and e["old"] != text else "pending"}
            paras.append({"text": text, "t": t, "edit": info})
        out_blocks.append({"speaker": b["speaker"], "time": b["time"], "start": b["start"], "paras": paras})
    stale = [e for e in edits if e["old"] not in present and e["new"] not in present]
    return {"slug": sl, "label": label(s), "title": s.get("session_title") or "", "video_id": s["video_id"],
            "blocks": out_blocks, "stale": stale}


def listing():
    rows = []
    for p in sorted(CORPUS_DIR.glob("*.md")):
        sl = p.stem
        if sl not in _sessions or not SLUG_RE.match(sl):
            continue
        n = len(load_edits(sl)["edits"])
        rows.append({"slug": sl, "label": label(_sessions[sl]), "title": _sessions[sl].get("session_title") or "",
                     "edits": n, "source": _sessions[sl].get("transcript_source") or ""})
    return rows


SUGG_DIR = ROOT / "raw" / "suggestions"   # git-ignored: may hold names and notes; only approved edits go to data/text-edits


def load_suggestions(sl):
    p = SUGG_DIR / f"{sl}.json"
    return json.loads(p.read_text()) if p.exists() else {"suggestions": []}


def current_for(sl, old):
    """The paragraph as it reads on the page now for the machine paragraph `old`: `old` itself, or the text
    of an approved correction to it. None if that paragraph is no longer in the transcript."""
    present = {p for b in parse_corpus(sl) for p in b["paras"]}
    if old in present:
        return old
    return next((e["new"] for e in load_edits(sl)["edits"] if e["old"] == old and e["new"] in present), None)


def pending_suggestions():
    out = []
    for p in sorted(SUGG_DIR.glob("*.json")) if SUGG_DIR.exists() else []:
        sl = p.stem
        if sl not in _sessions or not (CORPUS_DIR / f"{sl}.md").exists():
            continue
        for s in load_suggestions(sl)["suggestions"]:
            if s.get("status") == "pending":
                out.append({**s, "label": label(_sessions[sl]), "video_id": _sessions[sl]["video_id"],
                            "state": "ready" if current_for(sl, s["old"]) is not None else "stale"})
    return out


def save_one(sl, current, text, t, by, note):
    """Record a correction to the paragraph currently reading `current`. Returns an error string or None."""
    blocks = parse_corpus(sl)
    if not any(current == p for b in blocks for p in b["paras"]):
        return "That paragraph has changed on disk since this page loaded -- reload the page."
    data = load_edits(sl)
    edits = data["edits"]
    e = next((x for x in edits if x["new"] == current and x["old"] != current), None) or next((x for x in edits if x["old"] == current), None)
    old = e["old"] if e else current
    if text == old:                                    # back to the machine text: drop the edit
        if e:
            edits.remove(e)
    elif e:
        e.update({"new": text, "by": by, "at": time.strftime("%Y-%m-%d %H:%M"), "note": note})
    else:
        edits.append({"id": hashlib.sha1((old + "|" + str(int(t))).encode()).hexdigest()[:10], "t": round(float(t), 1),
                      "old": old, "new": text, "by": by, "at": time.strftime("%Y-%m-%d %H:%M"), "note": note, "status": "approved"})
    save_edits(sl, data)
    return None


# ------------------------------------------------------------------ web page

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin"><title>TextReview</title><style>
:root{--bg:#fafaf7;--card:#fff;--ink:#1c1c1a;--mute:#6b6b66;--line:#e2e0d8;--acc:#b0392b;--ok:#2f7d4f;--warn:#a86a00}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#eeeeea;--mute:#a3a39b;--line:#34342f;--acc:#e0705f;--ok:#5fbf85;--warn:#e0a850}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}main{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:1.3rem;margin:.2rem 0}a{color:var(--acc)}.mute{color:var(--mute)}.small{font-size:.85rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.grow{flex:1}
.badge{font-size:.75rem;border-radius:999px;padding:2px 9px;border:1px solid var(--line)}.ok{color:var(--ok);border-color:var(--ok)}.warn{color:var(--warn);border-color:var(--warn)}
input[type=text],textarea{width:100%;box-sizing:border-box;font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}textarea{resize:vertical;min-height:3.2em}
button{font:inherit;padding:6px 11px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}button.pri{background:var(--acc);color:#fff;border-color:var(--acc)}button:disabled{opacity:.4;cursor:default}
.vid{position:sticky;top:0;z-index:5;background:var(--bg);padding:6px 0 8px}.vid .frame{aspect-ratio:16/9;width:100%;max-height:36vh;background:#000;border-radius:10px;overflow:hidden}.vid .frame iframe{width:100%;height:100%;border:0}.vid.off .frame{display:none}
.spk{margin:16px 0 4px;font-weight:600}.orig{background:var(--bg);border:1px dashed var(--line);border-radius:8px;padding:8px;margin:6px 0;white-space:pre-wrap}.hide{display:none}
mark{background:#ffe680;color:#000}
</style></head><body><main id="app">Loading…</main><script>
const $=(s,e=document)=>e.querySelector(s), api=(u,o)=>fetch(u,o).then(r=>r.json());
const mmss=t=>Math.floor(t/60)+':'+String(Math.floor(t%60)).padStart(2,'0');
const el=(tag,props={},...kids)=>{const e=document.createElement(tag);for(const[k,v]of Object.entries(props)){if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else if(k.startsWith('on'))e[k]=v;else if(v===false||v==null){}else e.setAttribute(k,v===true?'':v)}kids.flat().forEach(c=>e.append(c));return e};
async function home(){
  const rows=await api('/api/list'), sug=await api('/api/suggestions'), app=$('#app');
  app.replaceChildren(el('h1',{text:'TextReview'}),el('p',{class:'mute small',text:'Correct a transcript next to the video. Pick a recording.'}),
    sug.length?el('a',{class:'card row',href:'#/suggestions',style:'text-decoration:none;color:inherit'},el('b',{text:sug.length+' public suggestion'+(sug.length>1?'s':'')+' waiting for review →'})):'',
    Object.assign(el('input',{type:'text',placeholder:'Filter recordings…'}),{oninput:e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('.rec').forEach(a=>a.classList.toggle('hide',!a.dataset.q.includes(q)))}}),
    ...rows.map(r=>el('a',{class:'card row rec',href:'#/'+r.slug,'data-q':(r.label+' '+r.title).toLowerCase(),style:'text-decoration:none;color:inherit'},
      el('div',{class:'grow'},el('b',{text:r.label+' '}),el('span',{class:'mute',text:r.title})),r.edits?el('span',{class:'badge ok',text:r.edits+' correction'+(r.edits>1?'s':'')}):'')));
}
let player=null,playerReady=false;
function loadPlayer(id){
  playerReady=false;if(player&&player.destroy){try{player.destroy()}catch(e){}}player=null;
  const make=()=>{player=new YT.Player('yt',{videoId:id,width:'100%',height:'100%',playerVars:{rel:0,playsinline:1,modestbranding:1},events:{onReady:()=>{playerReady=true},
    onError:e=>{playerReady=false;const n=$('#vidnote');if(n)n.textContent='The embedded player reported an error (code '+e.data+'), so ▶ opens YouTube at that moment instead.'}}})};
  if(window.YT&&YT.Player)make();else{window.onYouTubeIframeAPIReady=make;if(!document.getElementById('ytapi')){const s=document.createElement('script');s.id='ytapi';s.src='https://www.youtube.com/iframe_api';document.head.appendChild(s)}}
}
function jump(vid,t){const at=Math.max(0,t-3);   // 3 s lead-in, same as the site's watch links
$('#vidbox').classList.remove('off');
  if(player&&playerReady){const st=player.getPlayerState(),cur=((player.getVideoData&&player.getVideoData())||{}).video_id;
    if(cur===vid&&(st===1||st===2))player.seekTo(at,true);else player.loadVideoById({videoId:vid,startSeconds:at});player.playVideo()}
  else window.open('https://www.youtube.com/watch?v='+vid+'&t='+Math.floor(at)+'s','_blank','noopener')}
async function page(sl){
  const d=await api('/api/rec/'+sl), app=$('#app');
  const who=el('input',{type:'text',placeholder:'Your name (kept with each correction)',value:localStorage.getItem('tr_name')||''});who.oninput=()=>localStorage.setItem('tr_name',who.value);
  const msg=el('div',{class:'small mute'}), find=el('input',{type:'text',placeholder:'Find in this transcript…'}), count=el('span',{class:'small mute'});
  const applyBtn=el('button',{class:'pri',text:'Apply corrections to the page',onclick:async()=>{applyBtn.disabled=true;msg.textContent='Rebuilding this page…';const r=await api('/api/apply',{method:'POST',body:JSON.stringify({slug:sl})});msg.textContent=r.ok?'Page rebuilt: '+(r.summary||'done'):'Rebuild failed: '+(r.error||'');applyBtn.disabled=false}});
  const paras=[];
  const body=el('div');
  d.blocks.forEach(b=>{
    body.append(el('div',{class:'spk'},b.speaker+' ',el('span',{class:'mute small',text:b.time})));
    b.paras.forEach(p=>{
      const ta=el('textarea',{rows:Math.max(2,Math.ceil(p.text.length/85))});ta.value=p.text;
      const save=el('button',{class:'pri',text:'Save',disabled:true}), revert=el('button',{text:'Undo my correction',class:p.edit?'':'hide'});
      const status=el('span',{class:'small mute'}), orig=el('div',{class:'orig hide',text:p.edit?p.edit.old:''});
      const badge=el('span',{class:'badge ok'+(p.edit?'':' hide'),text:p.edit?('corrected by '+(p.edit.by||'?')+' · '+(p.edit.at||'')+(p.edit.state==='pending'?' · not applied to the page yet':'')):''});
      const card=el('div',{class:'card'},el('div',{class:'row'},el('button',{text:'▶ '+mmss(p.t),onclick:()=>jump(d.video_id,p.t)}),badge,
         p.edit?el('button',{class:'small',text:'show original',onclick:()=>orig.classList.toggle('hide')}):''),orig,ta,el('div',{class:'row',style:'margin-top:6px'},save,revert,status));
      let current=p.text;
      ta.oninput=()=>{save.disabled=ta.value===current};
      save.onclick=async()=>{
        if(!who.value.trim()){who.focus();status.textContent='Enter your name at the top first.';return}
        save.disabled=true;const r=await api('/api/edit',{method:'POST',body:JSON.stringify({slug:sl,current:current,text:ta.value,t:p.t,by:who.value.trim(),note:''})});
        if(r.ok){current=ta.value;status.textContent='saved ✓ (press “Apply corrections to the page” to publish it in the corpus)';badge.classList.remove('hide');badge.textContent='corrected by '+who.value.trim()+' · just now'}else{status.textContent=r.error||'could not save';save.disabled=false}};
      revert.onclick=async()=>{const r=await api('/api/revert',{method:'POST',body:JSON.stringify({slug:sl,id:p.edit.id})});if(r.ok){ta.value=p.edit.old;current=ta.value;p.edit=null;badge.classList.add('hide');revert.classList.add('hide');status.textContent='correction removed ✓'}};
      paras.push({card,ta});body.append(card)})});
  find.oninput=()=>{const q=find.value.toLowerCase();let n=0;paras.forEach(x=>{const hit=!q||x.ta.value.toLowerCase().includes(q);x.card.classList.toggle('hide',!hit);if(hit&&q)n++});count.textContent=q?n+' passages':''};
  app.replaceChildren(el('p',{},el('a',{href:'#/',text:'← all recordings'})),el('h1',{},d.label+' ',el('span',{class:'mute',text:d.title})),
    el('div',{class:'vid',id:'vidbox'},el('div',{class:'row'},el('button',{text:'Hide video',id:'vidtoggle',onclick:e=>{const off=$('#vidbox').classList.toggle('off');e.target.textContent=off?'Show video':'Hide video'}}),
      el('span',{class:'small mute',id:'vidnote',text:'Press ▶ beside a paragraph to watch that moment here.'})),el('div',{class:'frame'},el('div',{id:'yt'}))),
    el('div',{class:'card'},el('div',{class:'row'},el('div',{class:'grow'},who)),el('div',{class:'row',style:'margin-top:8px'},el('div',{class:'grow'},find),count),
      el('div',{class:'row',style:'margin-top:8px'},applyBtn,msg)),
    d.stale.length?el('div',{class:'card'},el('b',{text:d.stale.length+' correction(s) no longer match the transcript (STALE)'}),el('p',{class:'small mute',text:'The machine text they refer to has changed, so they were not applied. Re-make them on the current text if still needed.'}),
      d.stale.map(e=>el('div',{class:'orig'},'“'+e.old+'”  →  “'+e.new+'”'))):'',body);
  loadPlayer(d.video_id);
}
function wordDiff(a,b){                                  // words removed (red) and added (green) between two texts
  const x=a.split(/\s+/),y=b.split(/\s+/),n=x.length,m=y.length,L=Array.from({length:n+1},()=>new Uint16Array(m+1)),out=[];
  for(let i=n-1;i>=0;i--)for(let j=m-1;j>=0;j--)L[i][j]=x[i]===y[j]?L[i+1][j+1]+1:Math.max(L[i+1][j],L[i][j+1]);
  const del=w=>{const d=el('del',{text:w+' '});d.style.cssText='background:#f8d7da;color:#000';return d},ins=w=>{const s=el('ins',{text:w+' '});s.style.cssText='background:#d4edda;color:#000;text-decoration:none';return s};
  let i=0,j=0;while(i<n&&j<m){if(x[i]===y[j]){out.push(document.createTextNode(x[i]+' '));i++;j++}else if(L[i+1][j]>=L[i][j+1]){out.push(del(x[i]));i++}else{out.push(ins(y[j]));j++}}
  while(i<n)out.push(del(x[i++]));while(j<m)out.push(ins(y[j++]));return out}
async function suggestionsPage(){
  const list=await api('/api/suggestions'), app=$('#app'), done=new Set(), msg=el('span',{class:'small mute'});
  const rebuild=el('button',{class:'pri',text:'Rebuild pages with the approvals',disabled:true,onclick:async()=>{rebuild.disabled=true;msg.textContent='Rebuilding…';
    const r=await api('/api/apply-many',{method:'POST',body:JSON.stringify({slugs:[...done]})});msg.textContent=r.ok?'Done: '+r.summary:'Rebuild failed: '+(r.error||'');rebuild.disabled=false}});
  const cards=list.map(s=>{
    const box=el('div',{class:'card'}), diff=el('div',{class:'orig'},wordDiff(s.old,s.new)), edit=el('textarea',{class:'hide',rows:Math.max(3,Math.ceil(s.new.length/85))});edit.value=s.new;
    const result=el('span',{class:'small mute'}), act=async(action,text)=>{const r=await api('/api/suggestion',{method:'POST',body:JSON.stringify({slug:s.slug,sid:s.sid,action,text})});
      if(r.ok){box.replaceChildren(el('div',{class:'row'},el('b',{text:s.label+' '}),el('span',{class:'badge '+(action==='approve'?'ok':''),text:action==='approve'?'approved ✓ — rebuild to publish it':'rejected'})));if(action==='approve'){done.add(s.slug);rebuild.disabled=false}}else result.textContent=r.error||'could not save'};
    const approve=el('button',{class:'pri',text:'Approve as written',disabled:s.state==='stale',onclick:()=>act('approve')});
    const approveEdited=el('button',{class:'pri hide',text:'Approve my edited version',onclick:()=>act('approve',edit.value)});
    const editBtn=el('button',{text:'Edit first…',disabled:s.state==='stale',onclick:()=>{edit.classList.toggle('hide');approveEdited.classList.toggle('hide')}});
    box.append(el('div',{class:'row'},el('b',{text:s.label}),el('button',{text:'▶ '+mmss(s.t),onclick:()=>jump(s.video_id,s.t)}),
        el('span',{class:'badge',text:s.credit?'from '+s.by+' (agreed to be credited)':'anonymous'}),s.match!=='exact'?el('span',{class:'badge warn',text:s.match==='prefix'?'long passage: only its start was offered — check the ending is untouched':'matched approximately — check it'}):'',s.state==='stale'?el('span',{class:'badge warn',text:'STALE: that passage has since changed'}):''),
      s.note?el('div',{class:'small mute',style:'margin:6px 0',text:'Note: '+s.note}):'',diff,edit,el('div',{class:'row',style:'margin-top:6px'},approve,editBtn,approveEdited,el('button',{text:'Reject',onclick:()=>act('reject')}),result));
    return box});
  app.replaceChildren(el('p',{},el('a',{href:'#/',text:'← all recordings'})),el('h1',{text:'Suggestions ('+list.length+')'}),
    el('div',{class:'vid',id:'vidbox'},el('div',{class:'row'},el('button',{text:'Hide video',id:'vidtoggle',onclick:e=>{const off=$('#vidbox').classList.toggle('off');e.target.textContent=off?'Show video':'Hide video'}}),
      el('span',{class:'small mute',id:'vidnote',text:'Press ▶ on a suggestion to watch that moment here. Red = removed, green = added.'})),el('div',{class:'frame'},el('div',{id:'yt'}))),
    el('div',{class:'row'},rebuild,msg),...(cards.length?cards:[el('p',{class:'mute',text:'No suggestions waiting.'})]));
  loadPlayer(list[0]&&list[0].video_id);
}
const route=()=>{const m=location.hash.match(/^#\/(.+)$/);m?(m[1]==='suggestions'?suggestionsPage():page(m[1])):home()};addEventListener('hashchange',route);route();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json", code=200):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
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
            if path == "/api/suggestions":
                return self._send(pending_suggestions())
            m = re.fullmatch(r"/api/rec/([a-z]+-\d{3})", path)
            if m and SLUG_RE.match(m[1]) and (CORPUS_DIR / f"{m[1]}.md").exists():
                return self._send(recording(m[1]))
            self._send({"error": "not found"}, code=404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 3 * MAX_TEXT:
                return self._send({"error": "too large"}, code=413)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/apply-many":     # rebuild every recording that gained an approved correction
                slugs = [s for s in body.get("slugs", []) if SLUG_RE.match(str(s)) and (CORPUS_DIR / f"{s}.md").exists()]
                if not slugs:
                    return self._send({"ok": True, "summary": "nothing to rebuild"})
                r = subprocess.run([sys.executable, str(HERE / "05-build-corpus.py"), *slugs, "--no-review"], capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
                lines = [l.strip() for l in r.stdout.splitlines() if "text corrections" in l]
                return self._send({"ok": r.returncode == 0, "summary": "; ".join(lines) or "rebuilt", "error": r.stderr[-300:]})
            sl = body.get("slug", "")
            if not SLUG_RE.match(sl) or not (CORPUS_DIR / f"{sl}.md").exists():
                return self._send({"error": "unknown recording"}, code=400)
            if self.path == "/api/edit":
                text, current = str(body.get("text", "")).strip(), str(body.get("current", ""))
                by = re.sub(r"\s+", " ", str(body.get("by", ""))).strip()[:60]
                if not text or len(text) > MAX_TEXT or "\n\n" in text or not by:
                    return self._send({"error": "The correction must be one non-empty paragraph, with your name."}, code=400)
                err = save_one(sl, current, text, float(body.get("t", 0)), by, str(body.get("note", ""))[:300])
                return self._send({"ok": True} if not err else {"error": err}, code=200 if not err else 409)
            if self.path == "/api/suggestion":
                store = load_suggestions(sl)
                s = next((x for x in store["suggestions"] if x["sid"] == str(body.get("sid", "")) and x.get("status") == "pending"), None)
                if not s:
                    return self._send({"error": "That suggestion is no longer pending."}, code=404)
                if body.get("action") == "reject":
                    s["status"] = "rejected"
                elif body.get("action") == "approve":
                    text = " ".join(str(body.get("text") or s["new"]).split())
                    if not text or len(text) > MAX_TEXT:
                        return self._send({"error": "The correction must be a non-empty single paragraph."}, code=400)
                    current = current_for(sl, s["old"])
                    if current is None:
                        return self._send({"error": "The passage this refers to is no longer in the transcript, so it cannot be applied."}, code=409)
                    err = save_one(sl, current, text, s["t"], s["by"] if s.get("credit") else "Public suggestion", "from a public suggestion")
                    if err:
                        return self._send({"error": err}, code=409)
                    s.update({"status": "approved", "approved_text": text, "approved_at": time.strftime("%Y-%m-%d %H:%M")})
                else:
                    return self._send({"error": "unknown action"}, code=400)
                (SUGG_DIR / f"{sl}.json").write_text(json.dumps(store, indent=2, ensure_ascii=False))
                return self._send({"ok": True})
            if self.path == "/api/revert":
                data = load_edits(sl)
                before = len(data["edits"])
                data["edits"] = [e for e in data["edits"] if e["id"] != str(body.get("id", ""))]
                save_edits(sl, data)
                return self._send({"ok": len(data["edits"]) < before})
            if self.path == "/api/apply":
                r = subprocess.run([sys.executable, str(HERE / "05-build-corpus.py"), sl, "--no-review"], capture_output=True, text=True, timeout=600, cwd=str(ROOT))
                line = next((l.strip() for l in r.stdout.splitlines() if "text corrections" in l), "no corrections to apply" if r.returncode == 0 else "")
                return self._send({"ok": r.returncode == 0, "summary": line, "error": r.stderr[-300:]})
            self._send({"error": "not found"}, code=404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description="TextReview: correct transcripts next to the video")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TextReview: http://localhost:{args.port}   (Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
