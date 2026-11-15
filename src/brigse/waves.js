// Wave viewer of the Brigse dashboard. Inserted into the page by dashboard.py; relies on put(), esc(), open and tog().
const W = {};                                  // one view per simulation: data, visible range, cursor, radix
const UNITS = [["s", 1], ["ms", 1e-3], ["us", 1e-6], ["ns", 1e-9], ["ps", 1e-12], ["fs", 1e-15]];
function tsec(ts) {
  const m = /^(\d+)\s*([a-z]+)$/.exec(ts || "1ns"), u = UNITS.find(x => m && x[0] === m[2]);
  return (m ? +m[1] : 1) * (u ? u[1] : 1e-9);
}
function fmtT(v, ts) {
  if (!v) return "0";
  const s = v * tsec(ts);
  for (const [n, f] of UNITS) if (Math.abs(s) >= f || n === "fs") { const x = s / f; return (Math.abs(x) >= 100 ? Math.round(x) : +x.toFixed(2)) + " " + n; }
}
function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), n = x / p; return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * p; }
function valueAt(ch, t) {                      // last change at or before t, by binary search
  let lo = 0, hi = ch.length - 1, r = null;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (ch[m][0] <= t) { r = ch[m][1]; lo = m + 1; } else hi = m - 1; }
  return r;
}
function fmtVal(v, w, radix) {
  if (v == null) return "";
  if (w === 1) return v;
  if (/[xz]/.test(v)) return /^x+$/.test(v) ? "x" : "b" + v;
  if (radix === "bin") return v;
  const n = BigInt("0b" + v);
  return radix === "dec" ? n.toString(10) : n.toString(16);
}
function clampView(st) {
  const end = Math.max(1, st.data.end);
  let span = Math.min(Math.max(2, st.v1 - st.v0), end);
  if (st.v0 < 0) st.v0 = 0;
  st.v1 = st.v0 + span;
  if (st.v1 > end) { st.v1 = end; st.v0 = Math.max(0, end - span); }
}
async function loadWaves(id) {
  if (!W[id]) {
    const data = await (await fetch(`/api/sims/${id}/waves.json`)).json();
    W[id] = { data, v0: 0, v1: Math.max(1, data.end), cursor: null, radix: "hex", drag: null };
  }
  const cv = document.getElementById("cv-" + id);
  if (cv && !cv._bound) { cv._bound = true; bindWaves(cv, id); }
  drawWaves(id);
}
function drawWaves(id) {
  const st = W[id], cv = document.getElementById("cv-" + id);
  if (!st || !cv) return;
  const css = getComputedStyle(document.body), c = n => css.getPropertyValue(n).trim();
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth || 600, rowH = 28, nameW = Math.min(210, Math.round(w * 0.36));
  const top = 26, sigs = st.data.signals, h = top + sigs.length * rowH + 6, plotW = w - nameW - 8, span = Math.max(1, st.v1 - st.v0);
  const px = t => nameW + (t - st.v0) / span * plotW;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.height = h + "px";
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.font = "12px -apple-system,Segoe UI,sans-serif"; g.textBaseline = "middle";
  const step = niceStep(span / Math.max(2, plotW / 120));
  g.lineWidth = 1;
  for (let t = Math.ceil(st.v0 / step) * step; t <= st.v1; t += step) {
    const x = px(t); g.globalAlpha = 0.3; g.strokeStyle = c("--line"); g.beginPath(); g.moveTo(x, top - 4); g.lineTo(x, h); g.stroke();
    g.globalAlpha = 1; g.fillStyle = c("--mute"); g.textAlign = "left"; g.fillText(fmtT(t, st.data.timescale), x + 3, top / 2);
  }
  sigs.forEach((s, i) => {
    const y = top + i * rowH, mid = y + rowH / 2, hi = y + 6, lo = y + rowH - 7, ch = s.changes;
    const short = s.name.includes(".") ? s.name.split(".").slice(1).join(".") : s.name;
    g.save(); g.beginPath(); g.rect(0, y, nameW - 4, rowH); g.clip();
    g.fillStyle = c("--ink"); g.textAlign = "left"; g.fillText(short + (s.width > 1 ? `[${s.width - 1}:0]` : ""), 6, mid);
    g.restore();
    if (st.cursor != null) {
      const v = fmtVal(valueAt(ch, st.cursor), s.width, st.radix);
      g.fillStyle = c("--accent"); g.textAlign = "right"; g.fillText(v, nameW - 8, mid);
    }
    g.save(); g.beginPath(); g.rect(nameW, y, plotW + 4, rowH); g.clip();
    for (let k = 0; k < ch.length; k++) {
      const t = ch[k][0], v = ch[k][1], tn = k + 1 < ch.length ? ch[k + 1][0] : st.data.end;
      if (tn < st.v0 || t > st.v1) continue;
      const xa = px(t), xb = Math.max(px(tn), xa + 1), bad = /x/.test(v), color = bad ? c("--bad") : /z/.test(v) ? c("--mute") : c("--accent");
      g.strokeStyle = color; g.lineWidth = 1.5;
      if (s.width === 1) {
        const yv = v === "1" ? hi : v === "0" ? lo : mid;
        if (k > 0) { const pv = ch[k - 1][1], yp = pv === "1" ? hi : pv === "0" ? lo : mid; g.beginPath(); g.moveTo(xa, yp); g.lineTo(xa, yv); g.stroke(); }
        g.beginPath(); g.moveTo(xa, yv); g.lineTo(xb, yv); g.stroke();
      } else {
        g.beginPath(); g.moveTo(xa + 2, mid); g.lineTo(xa + 5, hi); g.lineTo(xb - 5, hi); g.lineTo(xb - 2, mid); g.lineTo(xb - 5, lo); g.lineTo(xa + 5, lo); g.closePath(); g.stroke();
        const label = fmtVal(v, s.width, st.radix);
        if (xb - xa > 12 + label.length * 6.5) { g.fillStyle = bad ? c("--bad") : c("--ink"); g.textAlign = "center"; g.fillText(label, (xa + xb) / 2, mid); }
      }
    }
    g.restore();
  });
  if (st.cursor != null && st.cursor >= st.v0 && st.cursor <= st.v1) {
    g.strokeStyle = c("--warn"); g.lineWidth = 1; g.beginPath(); g.moveTo(px(st.cursor), top - 4); g.lineTo(px(st.cursor), h); g.stroke();
  }
  const label = document.getElementById("cur-" + id);
  if (label) label.textContent = st.cursor != null ? "t = " + fmtT(Math.round(st.cursor), st.data.timescale) : "";
}
function bindWaves(cv, id) {
  const at = e => { const st = W[id], r = cv.getBoundingClientRect(), nameW = Math.min(210, Math.round((cv.clientWidth || 600) * 0.36)); return { x: e.clientX - r.left, nameW, t: st.v0 + (e.clientX - r.left - nameW) / (r.width - nameW - 8) * (st.v1 - st.v0) }; };
  cv.addEventListener("mousemove", e => {
    const st = W[id], p = at(e);
    if (st.drag) { const dt = (p.x - st.drag.x) / (cv.clientWidth - p.nameW - 8) * (st.drag.v1 - st.drag.v0); st.v0 = st.drag.v0 - dt; st.v1 = st.drag.v1 - dt; clampView(st); }
    st.cursor = p.x > p.nameW ? p.t : null; drawWaves(id);
  });
  cv.addEventListener("mouseleave", () => { const st = W[id]; st.cursor = null; st.drag = null; drawWaves(id); });
  cv.addEventListener("mousedown", e => { const st = W[id], p = at(e); st.drag = { x: p.x, v0: st.v0, v1: st.v1 }; });
  window.addEventListener("mouseup", () => { if (W[id]) W[id].drag = null; });
  cv.addEventListener("wheel", e => {
    e.preventDefault();
    const st = W[id], p = at(e), f = e.deltaY > 0 ? 1.25 : 0.8, tc = Math.min(Math.max(p.t, st.v0), st.v1);
    st.v0 = tc - (tc - st.v0) * f; st.v1 = tc + (st.v1 - tc) * f; clampView(st); drawWaves(id);
  }, { passive: false });
}
function zoomWaves(id, f) { const st = W[id]; if (!st) return; const m = (st.v0 + st.v1) / 2, half = (st.v1 - st.v0) / 2 * f; st.v0 = m - half; st.v1 = m + half; clampView(st); drawWaves(id); }
function fitWaves(id) { const st = W[id]; if (!st) return; st.v0 = 0; st.v1 = Math.max(1, st.data.end); drawWaves(id); }
function radixWaves(id, r) { if (W[id]) { W[id].radix = r; drawWaves(id); } }
function simsView(d) {
  const sims = d.sims || [];
  if (!sims.length) return put("sims", '<div class="card mute">No simulations yet. Ask the agent to simulate your testbench.</div>');
  put("sims", sims.map(s => {
    let h = `<div class="card"><div class="row"><div class="grow"><b>${esc(s.top || "simulation")}</b> <span class="mute small">${esc((s.sources || []).join(", "))}</span>
      <div class="mute small">${esc(when(s.started))}</div></div>
      <span class="tag ${s.passed ? "ok" : "bad"}">${s.passed ? "passed" : s.compiled ? "failed" : "did not compile"}</span></div>`;
    if (s.has_waves) h += `<div class="row small" style="margin:8px 0;gap:8px">
        <button onclick="zoomWaves('${s.id}',0.5)">Zoom in</button><button onclick="zoomWaves('${s.id}',2)">Zoom out</button><button onclick="fitWaves('${s.id}')">Fit</button>
        <select onchange="radixWaves('${s.id}',this.value)"><option>hex</option><option>bin</option><option>dec</option></select>
        <span class="mute" id="cur-${s.id}"></span><span class="grow"></span><span class="mute">${s.signals} signals · scroll to zoom, drag to move</span></div>
      <canvas id="cv-${s.id}" class="wave"></canvas>`;
    else h += `<div class="small mute" style="margin-top:6px">No waveform was recorded${s.note ? ": " + esc(s.note) : ""}.</div>`;
    if (s.output) h += `<details ${open["o-" + s.id] ? "open" : ""} ontoggle="tog('o-${s.id}',this.open)"><summary>Simulator output</summary><pre>${esc(s.output)}</pre></details>`;
    return h + "</div>";
  }).join(""));
  sims.filter(s => s.has_waves).forEach(s => loadWaves(s.id));
}
window.addEventListener("resize", () => Object.keys(W).forEach(drawWaves));
