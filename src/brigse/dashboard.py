"""Local window for Brigse: a single page served on this machine only.

It shows what is going on and lets the user act: pick the board, approve or
reject what the agent asks for, repeat a build, load a build onto the board,
start or stop an add-on's VM. Every button is refused while something else is
running, and the page greys the buttons out for the same reason.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import actions, boards, installer, registry, store
from .backends.base import BackendError

DEFAULT_PORT = 8765
TOKEN = secrets.token_urlsafe(24)
_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def _job_view(meta: dict, st: dict) -> dict:
    view = {"job_id": meta["job_id"], "top": meta["top"], "part": meta["part"],
            "backend": meta["backend"], "project": Path(meta["project_dir"]).name,
            "started": meta["started"], "by": meta.get("by", "agent"),
            "can_rebuild": "args" in meta}
    view.update({k: st.get(k) for k in (
        "state", "steps", "step_order", "current_step", "current_step_seconds", "remaining_steps",
        "log_tail", "error_tail", "outputs_dir", "report", "message")})
    board = actions.board_for_part(meta["part"])
    has_bit = bool((st.get("report") or {}).get("bitstream"))
    view["load_target"] = board["name"] if board and has_bit and st.get("state") == "done" else None
    return view


def snapshot() -> dict:
    with _lock:
        if _cache["data"] and time.time() - _cache["at"] < 3:
            return _cache["data"]
        backends = {}
        for b in registry.BACKENDS:
            st = b.status()
            buttons = []
            if b.implemented and b.has_runtime and st["installed"]:
                buttons.append("stop" if st["ready"] else "start")
            if b.implemented and b.auto_install:
                buttons.append("uninstall" if st["installed"] else "install")
            backends[b.name] = {"name": b.name, "title": b.title, "implemented": b.implemented,
                                "disk_cost": b.size_hint, "buttons": buttons, **st}
        metas = store.list_jobs()
        statuses = {m["job_id"]: actions.job_status(m) for m in metas}
        data = {
            "backends": list(backends.values()),
            "boards": [{**b, "backend_ready": any(
                backends[n]["implemented"] and backends[n].get("usable", backends[n]["installed"])
                for n in b["backends"])} for b in boards.BOARDS],
            "selected_board": store.selected_board(),
            "requests": store.list_requests()[:10],
            "jobs": [_job_view(m, statuses[m["job_id"]]) for m in metas],
            "busy": actions.busy(statuses),
            "last_program": store.get_state().get("last_program"),
            "now": time.time(),
        }
        _cache.update(at=time.time(), data=data)
        return data


def _backend_runtime(name: str, action: str) -> None:
    """Start or stop an add-on's VM in the background, holding a lock so the page shows it."""
    def run():
        with store.lock("backend", "user", f"{action}ing the {name} VM".replace("stoping", "stopping")) as holder:
            if holder:
                return
            try:
                registry.by_name(name).control(action)
            except BackendError as e:
                store.set_state(last_error={"text": str(e), "at": time.time()})
    threading.Thread(target=run, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "brigse-dashboard"

    def log_message(self, *args) -> None:   # stdout belongs to the MCP protocol: stay silent
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost") or host.endswith(".localhost")

    def do_GET(self) -> None:
        if not self._local():
            return self._json(403, {"error": "local access only"})
        if self.path == "/":
            return self._send(200, PAGE.replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._json(200, snapshot())
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._local() or self.headers.get("X-Token") != TOKEN:
            return self._json(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        _cache["data"] = None
        parts = self.path.strip("/").split("/")
        try:
            # Decisions on the agent's requests are always possible.
            if len(parts) == 4 and parts[:2] == ["api", "requests"] and parts[3] in ("approve", "reject"):
                req = store.decide_request(parts[2], approve=parts[3] == "approve")
                if req and req["state"] == "approved":
                    installer.start(req["id"])      # no-op unless the plan has an automatic installer
                    req = store.get_request(req["id"])
                return self._json(200 if req else 404, {"request": req})

            # Everything else is refused while something is running.
            running = actions.busy()
            if running:
                who = "the agent" if running[0]["by"] == "agent" else "you"
                return self._json(409, {"error": f"Busy: {running[0]['what']} (started by {who}). "
                                                 "Try again when it has finished."})
            if parts == ["api", "board"]:
                board_id = body.get("id")
                if not boards.get(board_id or ""):
                    return self._json(400, {"error": "unknown board"})
                store.select_board(board_id, by="user")
                return self._json(200, {"ok": True})
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "rebuild":
                return self._json(200, actions.rebuild(parts[2], by="user"))
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "load":
                return self._json(200, actions.load_job(parts[2], by="user"))
            if len(parts) == 4 and parts[:2] == ["api", "backends"] and parts[3] in ("start", "stop"):
                registry.by_name(parts[2])           # validates the name
                _backend_runtime(parts[2], parts[3])
                time.sleep(0.3)                      # let the lock appear before the page refreshes
                return self._json(200, {"ok": True})
            if len(parts) == 4 and parts[:2] == ["api", "backends"] and parts[3] == "install":
                return self._json(200, actions.install_now(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "backends"] and parts[3] == "uninstall":
                return self._json(200, actions.uninstall_now(parts[2]))
        except BackendError as e:
            return self._json(400, {"error": str(e)})
        self._json(404, {"error": "not found"})


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def url(port: int = DEFAULT_PORT) -> str:
    return f"http://brigse.localhost:{port}/"     # *.localhost always means this machine


def serve(port: int = DEFAULT_PORT) -> None:
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def start_in_background(port: int = DEFAULT_PORT) -> bool:
    """Start the dashboard inside the MCP server process unless one is already running."""
    if port_in_use(port):
        return False
    threading.Thread(target=serve, args=(port,), daemon=True).start()
    return True


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brigse</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%232f6fed' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M3 10h10a5 5 0 0 1 5 5'/%3E%3Cpath d='M9 10V6M6 6h6'/%3E%3Cpath d='M3 7v6'/%3E%3Cpath d='M18 18.2c-.9 1.2-1.3 1.9-1.3 2.5a1.3 1.3 0 0 0 2.6 0c0-.6-.4-1.3-1.3-2.5z' fill='%232f6fed' stroke='none'/%3E%3C/svg%3E">
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2330;--mute:#667085;--line:#e3e6eb;--accent:#2f6fed;
--ok:#1a8f4c;--bad:#c9372c;--warn:#b7791f;--chip:#eef1f6}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--ink:#e6e9ef;--mute:#98a2b3;
--line:#2a313d;--accent:#6c9cff;--ok:#4cc38a;--bad:#f0776c;--warn:#e0b25c;--chip:#252c38}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:760px;margin:0 auto;padding:22px 16px 56px}
header{display:flex;align-items:center;gap:12px;margin-bottom:16px}.logo{color:var(--accent)}
h1{font-size:20px;margin:0;line-height:1.2}
h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:26px 0 8px;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.grow{flex:1;min-width:160px}
.mute{color:var(--mute)}.small{font-size:13px}
.tag{display:inline-block;padding:2px 10px;border-radius:999px;background:var(--chip);font-size:12px;white-space:nowrap}
.tag.ok{color:var(--ok)}.tag.bad{color:var(--bad)}.tag.warn{color:var(--warn)}.tag.run{color:var(--accent)}
button{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--card);color:var(--ink);
border-radius:8px;padding:7px 14px;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.danger{color:var(--bad)}
button:disabled{opacity:.4;cursor:not-allowed;filter:grayscale(1)}
#status{border-radius:12px;padding:12px 16px;margin-bottom:6px;display:flex;gap:10px;align-items:center}
#status.idle{background:color-mix(in srgb,var(--ok) 14%,var(--card));border:1px solid color-mix(in srgb,var(--ok) 40%,var(--line))}
#status.busy{background:color-mix(in srgb,var(--accent) 16%,var(--card));border:1px solid color-mix(in srgb,var(--accent) 45%,var(--line))}
.dot{width:10px;height:10px;border-radius:50%;background:var(--ok);flex:none}
.busy .dot{background:var(--accent);animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.busy .dot{animation:none}}
.boards{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-top:10px}
.board{cursor:pointer;margin:0}.board.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.board.off{cursor:not-allowed;opacity:.5}
.steps{display:flex;gap:4px;margin:10px 0;flex-wrap:wrap}
.step{flex:1;min-width:54px;text-align:center;padding:4px 2px;border-radius:6px;background:var(--chip);font-size:12px}
.step.done{background:color-mix(in srgb,var(--ok) 22%,var(--card))}
.step.run{background:color-mix(in srgb,var(--accent) 26%,var(--card));font-weight:600}
.step.fail{background:color-mix(in srgb,var(--bad) 26%,var(--card))}
pre{margin:8px 0 0;padding:8px;background:var(--chip);border-radius:6px;overflow-x:auto;font-size:12px;white-space:pre-wrap}
.bar{height:6px;background:var(--chip);border-radius:3px;overflow:hidden;min-width:80px}
.bar>i{display:block;height:100%;background:var(--accent)}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:4px 10px 4px 0;font-size:13px;vertical-align:top}
th{color:var(--mute);font-weight:500}dl{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;margin:8px 0;font-size:13px}
dt{color:var(--mute)}dd{margin:0;overflow-wrap:anywhere}
details>summary{cursor:pointer;color:var(--mute);font-size:13px;margin-top:8px}
#err{color:var(--bad);margin:6px 2px;min-height:0}
</style></head><body><main>
<header><svg class="logo" viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 10h10a5 5 0 0 1 5 5"/><path d="M9 10V6M6 6h6"/><path d="M3 7v6"/><path d="M18 18.2c-.9 1.2-1.3 1.9-1.3 2.5a1.3 1.3 0 0 0 2.6 0c0-.6-.4-1.3-1.3-2.5z" fill="currentColor" stroke="none"/></svg><div><h1>Brigse</h1><div class="mute small">Bridge to Semiconductor</div></div></header>
<div id="statusbox"></div><div id="err" class="small"></div>
<div id="approvals"></div>
<h2>Board</h2><div id="boards"></div>
<h2>Builds</h2><div id="jobs"></div>
<h2>Add-ons</h2><div id="backends"></div>
</main><script>
const TOKEN="__TOKEN__";
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const last={},open={};let showBoards=false,errAt=0;
function put(id,h){if(last[id]!==h){last[id]=h;document.getElementById(id).innerHTML=h;}}
function tog(id,on){open[id]=on;}
const dur=s=>s==null?"":s<60?s+" s":Math.floor(s/60)+" min "+(s%60)+" s";
const who=b=>b==="agent"?"the agent":"you";
const ORDER=["xst","ngdbuild","map","par","trce","bitgen"];
function fail(t){document.getElementById("err").textContent=t;errAt=Date.now();}
async function act(path,body){
  try{const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify(body||{})});
    const d=await r.json().catch(()=>({}));if(!r.ok)fail(d.error||"That did not work.");else fail("");}
  catch(e){fail("The Brigse server is not reachable.");}
  tick();}
function btn(label,path,busy,cls,body,ask){
  const go=`act(${JSON.stringify(path)},${JSON.stringify(body||{})})`;
  return `<button class="${cls||""}" ${busy?'disabled title="Wait until the current work has finished"':""} onclick='${ask?`if(confirm(${JSON.stringify(ask)}))`:""}${go}'>${esc(label)}</button>`;}
function statusView(d){
  let h;
  if(d.busy.length){h=d.busy.map(b=>`<div id="status" class="busy"><span class="dot"></span><div class="grow"><b>Working:</b> ${esc(b.what)}
    <div class="small mute">started by ${who(b.by)} · ${dur(Math.round(d.now-b.since))} ago${b.progress?" · "+esc(b.progress):""}. Buttons are paused until it finishes.</div></div></div>`).join("");}
  else h=`<div id="status" class="idle"><span class="dot"></span><div><b>Ready.</b> <span class="mute">Nothing is running.</span></div></div>`;
  const p=d.last_program;
  if(p)h+=`<div class="small mute" style="margin:6px 2px">Last load: ${esc(p.file)} to ${esc(p.target)} (${p.flash?"flash, permanent":"temporary"}) by ${who(p.by)} at ${new Date(p.at*1000).toLocaleTimeString()} —
    <span class="tag ${p.ok?"ok":"bad"}">${p.ok?"loaded":"failed"}</span></div>`;
  put("statusbox",h);}
function approvals(d){
  const pend=d.requests.filter(r=>r.state==="pending");let h="";
  if(pend.length){h+="<h2>The agent is asking</h2>";
    for(const r of pend){h+=`<div class="card"><div class="row"><div class="grow"><b>${esc(r.title)}</b></div>
      ${btn("Approve","/api/requests/"+r.id+"/approve",false,"primary")}${btn("Reject","/api/requests/"+r.id+"/reject",false,"danger")}</div>
      <dl><dt>What</dt><dd>${esc(r.plan.what)}</dd><dt>From</dt><dd>${esc(r.plan.source)}</dd>
      <dt>Disk</dt><dd>${esc(r.plan.disk)}</dd><dt>Licence</dt><dd>${esc(r.plan.licence)}</dd>
      ${r.reason?`<dt>Why</dt><dd>${esc(r.reason)}</dd>`:""}</dl>
      <div class="small mute">${r.kind==="uninstall_backend"?"Approve deletes the folder shown above.":r.plan.automatic?"Approve starts the download and install.":"Approve only records your decision; nothing is downloaded."}</div></div>`;}}
  put("approvals",h);}
function boardsView(d){
  const busy=d.busy.length>0,sel=d.boards.find(b=>b.id===d.selected_board);
  let h="";
  if(sel)h+=`<div class="card"><div class="row"><div class="grow"><b>${esc(sel.name)}</b>
    <div class="mute small">${esc(sel.vendor)} · ${esc(sel.part)}</div></div>
    <span class="tag ${sel.backend_ready?"ok":"warn"}">${sel.backend_ready?"ready to build":"add-on missing"}</span>
    <button ${busy?'disabled title="Wait until the current work has finished"':""} onclick="showBoards=!showBoards;last.boards=null;tick()">${showBoards?"Close":"Change"}</button></div>
    ${sel.note?`<div class="small mute" style="margin-top:6px">${esc(sel.note)}</div>`:""}</div>`;
  if(!sel||showBoards)h+=`<div class="boards">`+d.boards.map(b=>`<div class="card board ${b.id===d.selected_board?"sel":""} ${busy?"off":""}"
    ${busy?"":`onclick='showBoards=false;act("/api/board",{id:${JSON.stringify(b.id)}})'`}>
    <b>${esc(b.name)}</b><div class="mute small">${esc(b.part)}</div>
    <div style="margin-top:6px"><span class="tag ${b.backend_ready?"ok":"warn"}">${b.backend_ready?"ready":"add-on missing"}</span></div></div>`).join("")+"</div>";
  put("boards",h);}
function verdict(j){
  const r=j.report||{},t=r.timing,bits=[];
  if(t){bits.push(`<span class="tag ${t.met?"ok":"bad"}">timing ${t.met?"met":"NOT met"}</span>`);
    const c=(t.clocks||[])[0];if(c)bits.push(`<span class="small mute">${esc(c.fmax_mhz)} MHz max</span>`);
    else if(t.worst_slack_ns!=null)bits.push(`<span class="small mute">worst slack ${esc(t.worst_slack_ns)} ns</span>`);}
  if(r.bitstream)bits.push(`<span class="tag ok">bitstream ready</span>`);
  return bits.join(" ");}
function details(j){
  const r=j.report||{},t=r.timing||{},u=r.utilization||{};let h="";
  let keys=["occupied Slices","Total 4 input LUTs","RAMB16BWEs","bonded IOBs","Logic","Register","I/O Port","LUT4","DFF","IOB"].filter(k=>u[k]);
  if(!keys.length)keys=Object.keys(u).slice(0,5);
  if(keys.length){h+="<table>";for(const k of keys.slice(0,5))h+=`<tr><td>${esc(k)}</td><td>${u[k].used} / ${u[k].available}</td>
    <td style="width:38%"><div class="bar"><i style="width:${u[k].percent}%"></i></div></td><td>${u[k].percent}%</td></tr>`;h+="</table>";}
  for(const c of (t.clocks||[]))h+=`<div class="small">Clock ${esc(c.clock)}: ${esc(c.fmax_mhz)} MHz possible, ${esc(c.target_mhz)} MHz asked</div>`;
  const f=(t.failing||[]).filter(x=>x.constraint);
  if(f.length){h+="<table style='margin-top:6px'><tr><th>Failing constraint</th><th>Check</th><th>Slack</th></tr>";
    for(const x of f)h+=`<tr><td>${esc(x.constraint)}</td><td>${esc(x.check)}</td><td>${esc(x.slack_ns)} ns</td></tr>`;h+="</table>";}
  if(r.bitstream)h+=`<div class="small mute" style="margin-top:6px">${esc(r.bitstream.path)}</div>`;
  h+=`<div class="small mute">Job ${esc(j.job_id)} · add-on ${esc(j.backend)} · started by ${who(j.by)}</div>`;
  return h;}
function jobCard(j,busy){
  const done=new Map((j.steps||[]).map(s=>[s.step,s]));
  const cls={done:"ok",failed:"bad",running:"run",unknown:"warn"}[j.state]||"";
  let h=`<div class="card"><div class="row"><div class="grow"><b>${esc(j.project)}</b> <span class="mute small">${esc(j.part)}</span></div>
    <span class="tag ${cls}">${esc(j.state)}</span></div><div class="steps">`;
  for(const s of (j.step_order||ORDER)){const st=done.get(s);let c="",t="";
    if(st){c=st.exit_code===0?"done":"fail";t=dur(st.seconds);}
    else if(j.current_step===s){c="run";t=dur(j.current_step_seconds);}
    h+=`<div class="step ${c}">${esc(s)}<div class="small">${t||"&nbsp;"}</div></div>`;}
  h+=`</div><div class="row">${verdict(j)}<span class="grow"></span>`;
  if(j.load_target)h+=btn("Load to board (temporary)","/api/jobs/"+j.job_id+"/load",busy,"primary");
  if(j.can_rebuild&&j.state!=="running")h+=btn("Build again","/api/jobs/"+j.job_id+"/rebuild",busy);
  h+="</div>";
  if(j.message)h+=`<div class="small mute" style="margin-top:6px">${esc(j.message)}</div>`;
  const tail=j.error_tail||(j.state==="running"?j.log_tail:null);
  if(tail&&tail.length)h+=`<pre>${esc(tail.join("\n"))}</pre>`;
  const id="d-"+j.job_id;
  h+=`<details ${open[id]?"open":""} ontoggle="tog('${id}',this.open)"><summary>Details</summary>${details(j)}</details>`;
  return h+"</div>";}
function jobsView(d){
  const busy=d.busy.length>0;
  if(!d.jobs.length)return put("jobs",'<div class="card mute">No builds yet. Ask the agent to build your design.</div>');
  let h=jobCard(d.jobs[0],busy);
  if(d.jobs.length>1)h+=`<details ${open.older?"open":""} ontoggle="tog('older',this.open)"><summary>${d.jobs.length-1} earlier build${d.jobs.length>2?"s":""}</summary>
    <div style="margin-top:8px">${d.jobs.slice(1).map(j=>jobCard(j,busy)).join("")}</div></details>`;
  put("jobs",h);}
function backendsView(d){
  const busy=d.busy.length>0;
  put("backends",`<div class="card">`+d.backends.map((b,i)=>{
    const s=!b.implemented?["planned","warn"]:(b.installed&&b.usable===false)?["not usable","bad"]:b.ready&&b.buttons.includes("stop")?["running","run"]:b.installed?["installed","ok"]:["not installed","warn"];
    const label={start:"Start",stop:"Stop",install:"Install",uninstall:"Uninstall"};
    const ask={uninstall:"Delete the downloaded tools of this add-on? Your projects and builds are kept. You can install it again later."};
    return `<div class="row" style="${i?"border-top:1px solid var(--line);margin-top:10px;padding-top:10px":""}">
      <div class="grow"><b>${esc(b.title)}</b><div class="small mute">${esc(b.detail)} ${esc(b.install_hint||"")}</div></div>
      <span class="tag ${s[1]}">${s[0]}</span>${b.buttons.map(x=>btn(label[x],"/api/backends/"+b.name+"/"+x,busy,x==="uninstall"?"danger":"",null,ask[x])).join("")}</div>`;}).join("")+"</div>");}
async function tick(){
  try{const d=await (await fetch("/api/state")).json();
    statusView(d);approvals(d);boardsView(d);jobsView(d);backendsView(d);
    if(errAt&&Date.now()-errAt>12000)fail("");
  }catch(e){put("statusbox",'<div id="status" class="busy"><span class="dot"></span><div>The Brigse server is not reachable.</div></div>');}}
tick();setInterval(tick,3000);
</script></body></html>
"""
