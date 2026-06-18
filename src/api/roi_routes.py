"""
roi_routes.py — interactive ROI calibration editor, mounted at /roi.

Unlike the dev-only detection viewer (/dev), these routes are available in prod
too, so you can calibrate ROIs directly on the deployed Pi. They let you:

  * GET  /roi/rois                       — current ROIs + known panes + advisories
  * PUT  /roi/rois/{pane}                — save one pane's box (validated, persisted)
  * DELETE /roi/rois/{pane}              — clear a pane (reverts to full frame)
  * GET  /roi/snapshot/{device_id}       — latest ORIGINAL frame (editor canvas)
  * GET  /roi/rois/{pane}/preview?...    — live "heads inside this box" count
  * GET  /roi/                           — the browser editor (drag box + sliders)

Edits persist to data/camera_rois.json via roi_store and mutate settings in
place, so resolve_roi picks them up on the next processed group — no restart.

SECURITY: PUT/DELETE write config and are unauthenticated, matching the rest of
the service (the Pi is expected to sit on a trusted LAN). If that changes, gate
these behind the device token or bind the editor to localhost.
"""
import logging

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from ..core.config import settings
from ..services import roi_store
from ..services.roi_service import _pane_key, count_in_roi, roi_advisories

logger = logging.getLogger(__name__)

roi_router = APIRouter()


def _panes_payload(request: Request) -> list[dict]:
    """Union of live cameras (pane→device_id) and configured-but-unseen panes."""
    store = getattr(request.app.state, "snapshot_store", None)
    live: dict[str, str] = {}
    if store is not None:
        for device_id in store.devices():
            live[_pane_key(device_id)] = device_id
    panes = []
    for pane in sorted(set(live) | set(settings.camera_rois)):
        panes.append({
            "pane": pane,
            "device_id": live.get(pane),
            "has_frame": pane in live,
        })
    return panes


@roi_router.get("/rois")
async def get_rois(request: Request):
    return {
        "rois": settings.camera_rois,
        "panes": _panes_payload(request),
        "advisories": roi_advisories(settings.camera_rois),
    }


@roi_router.put("/rois/{pane}")
async def put_roi(pane: str, box: list[float] = Body(..., embed=True)):
    pane = _pane_key(pane)
    try:
        rois = roi_store.set_pane(pane, box)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    logger.info("ROI saved for pane %r: %s", pane, box)
    return {"ok": True, "rois": rois, "advisories": roi_advisories(rois)}


@roi_router.delete("/rois/{pane}")
async def delete_roi(pane: str):
    pane = _pane_key(pane)
    rois = roi_store.delete_pane(pane)
    logger.info("ROI cleared for pane %r", pane)
    return {"ok": True, "rois": rois, "advisories": roi_advisories(rois)}


@roi_router.get("/snapshot/{device_id}")
async def snapshot(device_id: str, request: Request):
    store = getattr(request.app.state, "snapshot_store", None)
    img = store.snapshot(device_id) if store is not None else None
    if img is None:
        raise HTTPException(status_code=404, detail="no frame yet for this camera")
    # Frames change; don't let the browser serve a stale cached snapshot.
    return Response(content=img, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@roi_router.get("/rois/{pane}/preview")
async def preview(pane: str, request: Request,
                  x1: float, y1: float, x2: float, y2: float, device_id: str):
    """Count how many of the camera's latest detections fall inside a candidate box."""
    store = getattr(request.app.state, "snapshot_store", None)
    latest = store.latest(device_id) if store is not None else None
    if latest is None:
        return {"count": None}
    count = count_in_roi(latest["detections"], (x1, y1, x2, y2), latest["meta"])
    return {"count": count, "total": len(latest["detections"])}


@roi_router.get("/", response_class=HTMLResponse)
async def editor() -> str:
    return _EDITOR_HTML


_EDITOR_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>ROI calibration</title>
<style>
 body{background:#111;color:#eee;font-family:system-ui,sans-serif;margin:16px}
 h1{font-size:18px;margin:0 0 4px} .sub{color:#888;font-size:13px;margin:0 0 14px}
 .cards{display:flex;flex-wrap:wrap;gap:18px}
 .card{background:#1b1b1b;border:1px solid #333;border-radius:8px;padding:12px;width:480px}
 .pane{font-size:15px;color:#8f8;margin:0 0 8px}
 .pane small{color:#777}
 .stage{position:relative;width:456px;background:#000;border:1px solid #333;
        user-select:none;touch-action:none}
 .stage img{display:block;width:100%;height:auto}
 .noframe{display:flex;align-items:center;justify-content:center;height:300px;
          color:#888;font-size:13px;text-align:center;padding:0 20px}
 .box{position:absolute;border:2px solid #2af;background:rgba(34,170,255,.16);cursor:move}
 .h{position:absolute;width:12px;height:12px;background:#2af;border:1px solid #04263b}
 .h.nw{left:-7px;top:-7px;cursor:nwse-resize} .h.ne{right:-7px;top:-7px;cursor:nesw-resize}
 .h.sw{left:-7px;bottom:-7px;cursor:nesw-resize} .h.se{right:-7px;bottom:-7px;cursor:nwse-resize}
 .sliders{margin:10px 0;display:grid;grid-template-columns:auto 1fr auto;gap:6px 8px;align-items:center}
 .sliders label{color:#aaa;font-size:12px} .sliders input[type=range]{width:100%}
 .sliders input[type=number]{width:64px;background:#222;color:#eee;border:1px solid #444;border-radius:4px}
 .row{display:flex;gap:8px;align-items:center;margin-top:8px}
 button{background:#264;color:#dfd;border:1px solid #4a6;border-radius:5px;padding:6px 12px;cursor:pointer}
 button.clear{background:#422;color:#fdd;border-color:#a55}
 .count{color:#fd6;font-size:13px;margin-left:auto}
 .msg{font-size:12px;margin-top:6px;min-height:14px}
 .msg.ok{color:#6c6} .msg.err{color:#f77}
 #adv{color:#fc6;font-size:13px;margin:10px 0;min-height:16px}
</style></head><body>
<h1>ROI calibration</h1>
<p class="sub">Drag the box or use the sliders. The count shows how many detected heads fall inside.
Save writes <code>data/camera_rois.json</code> and applies immediately — no restart.</p>
<div id="adv"></div>
<div class="cards" id="cards"></div>
<script>
const API = location.pathname.replace(/\/$/, "");   // e.g. "/roi"
const clamp = v => Math.max(0, Math.min(1, v));
const r3 = v => Math.round(v*1000)/1000;

async function boot(){
  const r = await fetch(API+"/rois"); const j = await r.json();
  renderAdv(j.advisories);
  const cards = document.getElementById("cards"); cards.innerHTML = "";
  for(const p of j.panes){
    const box = (j.rois[p.pane]) || [0.25,0.25,0.75,0.75];
    cards.appendChild(makeCard(p, box));
  }
  if(j.panes.length===0) cards.innerHTML =
    '<p class="sub">No cameras seen and no ROIs configured yet. Once a camera uploads a frame it appears here.</p>';
}
function renderAdv(adv){
  const el = document.getElementById("adv");
  el.textContent = (adv && adv.length) ? "⚠ "+adv.join("  •  ") : "";
}

function makeCard(p, box){
  const card = document.createElement("div"); card.className="card";
  card.innerHTML = `
    <p class="pane">${p.pane} <small>${p.device_id || "(no live camera)"}</small></p>
    <div class="stage"></div>
    <div class="sliders"></div>
    <div class="row">
      <button class="save">Save</button>
      <button class="clear">Clear</button>
      <span class="count"></span>
    </div>
    <div class="msg"></div>`;
  const stage = card.querySelector(".stage");
  const state = {x1:box[0],y1:box[1],x2:box[2],y2:box[3]};

  // Canvas: snapshot image, or a placeholder when no frame yet.
  let rect;
  if(p.device_id){
    const img = document.createElement("img");
    const refresh = ()=> img.src = API+"/snapshot/"+encodeURIComponent(p.device_id)+"?t="+Date.now();
    img.onerror = ()=>{ stage.querySelectorAll("img").forEach(n=>n.remove()); ensurePlaceholder(stage); };
    refresh(); setInterval(refresh, 3000);
    stage.appendChild(img);
  } else { ensurePlaceholder(stage); }

  const boxEl = document.createElement("div"); boxEl.className="box";
  for(const c of ["nw","ne","sw","se"]){ const h=document.createElement("div"); h.className="h "+c; h.dataset.c=c; boxEl.appendChild(h); }
  stage.appendChild(boxEl);

  const sliders = card.querySelector(".sliders");
  const inputs = {};
  for(const k of ["x1","y1","x2","y2"]){
    const lab=document.createElement("label"); lab.textContent=k;
    const rng=document.createElement("input"); rng.type="range"; rng.min=0; rng.max=1; rng.step=0.001;
    const num=document.createElement("input"); num.type="number"; num.min=0; num.max=1; num.step=0.001;
    sliders.append(lab,rng,num);
    inputs[k]={rng,num};
    const set = v=>{ state[k]=clamp(parseFloat(v)||0); normalize(); sync(); preview(); };
    rng.addEventListener("input",e=>set(e.target.value));
    num.addEventListener("input",e=>set(e.target.value));
  }

  function normalize(){ // keep x1<x2, y1<y2
    if(state.x1>state.x2){[state.x1,state.x2]=[state.x2,state.x1];}
    if(state.y1>state.y2){[state.y1,state.y2]=[state.y2,state.y1];}
  }
  function sync(){
    boxEl.style.left  =(state.x1*100)+"%"; boxEl.style.top   =(state.y1*100)+"%";
    boxEl.style.width =((state.x2-state.x1)*100)+"%"; boxEl.style.height=((state.y2-state.y1)*100)+"%";
    for(const k of ["x1","y1","x2","y2"]){ inputs[k].rng.value=state[k]; inputs[k].num.value=r3(state[k]); }
  }

  // Drag-to-move and corner resize via pointer events.
  let drag=null;
  const toNorm = e=>{ const b=stage.getBoundingClientRect();
    return {x:clamp((e.clientX-b.left)/b.width), y:clamp((e.clientY-b.top)/b.height)}; };
  boxEl.addEventListener("pointerdown",e=>{
    if(e.target.classList.contains("h")) drag={mode:"resize",c:e.target.dataset.c};
    else { const n=toNorm(e); drag={mode:"move",ox:n.x-state.x1,oy:n.y-state.y1,w:state.x2-state.x1,h:state.y2-state.y1}; }
    boxEl.setPointerCapture(e.pointerId); e.preventDefault();
  });
  boxEl.addEventListener("pointermove",e=>{
    if(!drag) return; const n=toNorm(e);
    if(drag.mode==="move"){
      let nx1=clamp(n.x-drag.ox), ny1=clamp(n.y-drag.oy);
      nx1=Math.min(nx1,1-drag.w); ny1=Math.min(ny1,1-drag.h);
      state.x1=nx1; state.y1=ny1; state.x2=nx1+drag.w; state.y2=ny1+drag.h;
    } else {
      if(drag.c.includes("w")) state.x1=n.x; if(drag.c.includes("e")) state.x2=n.x;
      if(drag.c.includes("n")) state.y1=n.y; if(drag.c.includes("s")) state.y2=n.y;
      normalize();
    }
    sync(); preview();
  });
  boxEl.addEventListener("pointerup",e=>{ drag=null; });

  // Debounced live count preview.
  let t=null; const countEl=card.querySelector(".count");
  function preview(){
    if(!p.device_id){ countEl.textContent=""; return; }
    clearTimeout(t); t=setTimeout(async()=>{
      const q=`x1=${state.x1}&y1=${state.y1}&x2=${state.x2}&y2=${state.y2}&device_id=${encodeURIComponent(p.device_id)}`;
      try{ const r=await fetch(`${API}/rois/${encodeURIComponent(p.pane)}/preview?`+q); const j=await r.json();
        countEl.textContent = (j.count==null) ? "no frame yet" : `${j.count} / ${j.total} heads in box`;
      }catch(_){}
    },180);
  }

  const msg=card.querySelector(".msg");
  card.querySelector(".save").addEventListener("click",async()=>{
    const r=await fetch(`${API}/rois/${encodeURIComponent(p.pane)}`,{method:"PUT",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({box:[r3(state.x1),r3(state.y1),r3(state.x2),r3(state.y2)]})});
    const j=await r.json();
    if(r.ok){ msg.className="msg ok"; msg.textContent="Saved."; renderAdv(j.advisories); }
    else   { msg.className="msg err"; msg.textContent="Rejected: "+(j.detail||"invalid box"); }
  });
  card.querySelector(".clear").addEventListener("click",async()=>{
    const r=await fetch(`${API}/rois/${encodeURIComponent(p.pane)}`,{method:"DELETE"});
    const j=await r.json(); msg.className="msg ok"; msg.textContent="Cleared (full frame).";
    renderAdv(j.advisories);
  });

  sync(); preview();
  return card;
}
function ensurePlaceholder(stage){
  if(stage.querySelector(".noframe")) return;
  const ph=document.createElement("div"); ph.className="noframe";
  ph.textContent="Waiting for first frame from this camera — you can still set the box numerically.";
  stage.insertBefore(ph, stage.firstChild);
}
boot();
</script></body></html>"""
