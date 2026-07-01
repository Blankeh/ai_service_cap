"""
roi_routes.py — interactive ROI calibration editor, mounted at /roi.

Unlike the dev-only detection viewer (/dev), these routes are available in prod
too, so you can calibrate ROIs directly on the deployed Pi. They let you:

  * GET    /roi/rois                     — current ROIs + known panes + advisories
  * PUT    /roi/rois/{pane}              — save one pane's shape (validated, persisted)
  * DELETE /roi/rois/{pane}              — clear a pane (reverts to full frame)
  * GET    /roi/snapshot/{device_id}     — latest ORIGINAL frame (editor canvas)
  * POST   /roi/rois/{pane}/preview      — live "heads inside this shape" count
  * GET    /roi/                         — the browser editor (draw a polygon)

A shape is either a rectangle [x1,y1,x2,y2] or a freeform polygon [[x,y],...];
both are accepted everywhere. Edits persist to data/camera_rois.json via
roi_store and mutate settings in place, so resolve_roi picks them up on the next
processed group — no restart.

SECURITY: PUT/DELETE write config and are unauthenticated, matching the rest of
the service (the Pi is expected to sit on a trusted LAN). If that changes, gate
these behind the device token or bind the editor to localhost.
"""
import logging

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from ..core.config import settings
from ..services import roi_store
from ..services.roi_service import (
    _pane_key,
    count_in_roi,
    roi_advisories,
    validate_shape,
)

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
async def put_roi(pane: str, payload: dict = Body(...)):
    pane = _pane_key(pane)
    # Accept the new "shape" key; fall back to the legacy "box" key.
    shape = payload.get("shape", payload.get("box"))
    if shape is None:
        raise HTTPException(status_code=422, detail="missing 'shape' in body")
    try:
        rois = roi_store.set_pane(pane, shape)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    logger.info("ROI saved for pane %r: %s", pane, shape)
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


@roi_router.post("/rois/{pane}/preview")
async def preview(pane: str, request: Request, payload: dict = Body(...)):
    """Count how many of the camera's latest detections fall inside a candidate shape."""
    device_id = payload.get("device_id")
    shape = payload.get("shape")
    if not device_id or shape is None:
        return {"count": None}
    # While the user is mid-draw the shape may be incomplete — don't 500, just skip.
    if validate_shape(shape) is not None:
        return {"count": None}
    store = getattr(request.app.state, "snapshot_store", None)
    latest = store.latest(device_id) if store is not None else None
    if latest is None:
        return {"count": None}
    count = count_in_roi(latest["detections"], shape, latest["meta"])
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
 .ov{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
 .ov polygon{fill:rgba(34,170,255,.16);stroke:#2af;stroke-width:2;
             vector-effect:non-scaling-stroke;cursor:move}
 .h{position:absolute;width:13px;height:13px;border-radius:50%;background:#2af;
    border:1px solid #04263b;transform:translate(-50%,-50%);cursor:grab;touch-action:none}
 .h:active{cursor:grabbing}
 .hint{color:#789;font-size:11px;margin:6px 0 2px}
 .row{display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap}
 button{background:#264;color:#dfd;border:1px solid #4a6;border-radius:5px;padding:6px 12px;cursor:pointer}
 button.minor{background:#23303a;color:#bdd;border-color:#3a5a6a}
 button.clear{background:#422;color:#fdd;border-color:#a55}
 .count{color:#fd6;font-size:13px;margin-left:auto}
 .msg{font-size:12px;margin-top:6px;min-height:14px}
 .msg.ok{color:#6c6} .msg.err{color:#f77}
 #adv{color:#fc6;font-size:13px;margin:10px 0;min-height:16px}
</style></head><body>
<h1>ROI calibration</h1>
<p class="sub">Drag the dots to reshape, drag inside to move the whole region, click
outside to add a point, double-click a dot to remove it. The count shows how many
detected heads fall inside. Save writes <code>data/camera_rois.json</code> and
applies immediately — no restart.</p>
<div id="adv"></div>
<div class="cards" id="cards"></div>
<script>
const API = location.pathname.replace(/\/$/, "");   // e.g. "/roi"
const clamp = v => Math.max(0, Math.min(1, v));
const r3 = v => Math.round(v*1000)/1000;
const SVGNS = "http://www.w3.org/2000/svg";

// A shape from the server is either a flat [x1,y1,x2,y2] rectangle or a list of
// [x,y] vertices. Normalize both into an array of {x,y} polygon points.
function toPoints(shape){
  if(!Array.isArray(shape) || !shape.length) return defaultQuad();
  if(Array.isArray(shape[0])) return shape.map(p=>({x:clamp(+p[0]),y:clamp(+p[1])}));
  const [x1,y1,x2,y2]=shape;                          // legacy rectangle
  return [{x:x1,y:y1},{x:x2,y:y1},{x:x2,y:y2},{x:x1,y:y2}];
}
function defaultQuad(){ return [{x:.25,y:.25},{x:.75,y:.25},{x:.75,y:.75},{x:.25,y:.75}]; }

async function boot(){
  const r = await fetch(API+"/rois"); const j = await r.json();
  renderAdv(j.advisories);
  const cards = document.getElementById("cards"); cards.innerHTML = "";
  for(const p of j.panes) cards.appendChild(makeCard(p, j.rois[p.pane]));
  if(j.panes.length===0) cards.innerHTML =
    '<p class="sub">No cameras seen and no ROIs configured yet. Once a camera uploads a frame it appears here.</p>';
}
function renderAdv(adv){
  const el = document.getElementById("adv");
  el.textContent = (adv && adv.length) ? "⚠ "+adv.join("  •  ") : "";
}

function makeCard(p, shape){
  const card = document.createElement("div"); card.className="card";
  card.innerHTML = `
    <p class="pane">${p.pane} <small>${p.device_id || "(no live camera)"}</small></p>
    <div class="stage"></div>
    <div class="hint">drag dot = move corner · drag inside = move region · click outside = add point · dbl-click dot = delete</div>
    <div class="row">
      <button class="save">Save</button>
      <button class="minor undo">Undo point</button>
      <button class="minor reset">Reset</button>
      <button class="clear">Clear</button>
      <span class="count"></span>
    </div>
    <div class="msg"></div>`;
  const stage = card.querySelector(".stage");
  let pts = toPoints(shape);

  // Canvas: snapshot image (polled ~1s, matching the new capture cadence), or a
  // placeholder when no frame yet.
  if(p.device_id){
    const img = document.createElement("img");
    const refresh = ()=> img.src = API+"/snapshot/"+encodeURIComponent(p.device_id)+"?t="+Date.now();
    img.onerror = ()=>{ stage.querySelectorAll("img").forEach(n=>n.remove()); ensurePlaceholder(stage); };
    refresh(); setInterval(refresh, 1000);
    stage.appendChild(img);
  } else { ensurePlaceholder(stage); }

  // SVG overlay (0..100 user units via the polygon points) + HTML dot handles.
  const svg = document.createElementNS(SVGNS,"svg");
  svg.setAttribute("class","ov"); svg.setAttribute("viewBox","0 0 100 100");
  svg.setAttribute("preserveAspectRatio","none");
  const poly = document.createElementNS(SVGNS,"polygon"); svg.appendChild(poly);
  stage.appendChild(svg);
  const handles = [];

  const toNorm = e=>{ const b=stage.getBoundingClientRect();
    return {x:clamp((e.clientX-b.left)/b.width), y:clamp((e.clientY-b.top)/b.height)}; };

  function render(){
    poly.setAttribute("points", pts.map(p=>`${p.x*100},${p.y*100}`).join(" "));
    while(handles.length>pts.length){ handles.pop().remove(); }
    while(handles.length<pts.length){
      const h=document.createElement("div"); h.className="h"; stage.appendChild(h);
      bindHandle(h); handles.push(h);
    }
    // Position dots and re-stamp dataset.i — indices shift after add/delete.
    pts.forEach((pt,i)=>{ handles[i].style.left=(pt.x*100)+"%";
      handles[i].style.top=(pt.y*100)+"%"; handles[i].dataset.i=i; });
  }

  // Vertex drag + double-click delete. The live index is read from dataset.i
  // (kept current by render) so splices elsewhere don't desync this handle.
  function bindHandle(h){
    h.addEventListener("pointerdown", e=>{
      const i = +h.dataset.i;
      h.setPointerCapture(e.pointerId);
      const move = ev=>{ const n=toNorm(ev); pts[i]={x:n.x,y:n.y}; render(); };
      const up = ()=>{ h.removeEventListener("pointermove",move);
                       h.removeEventListener("pointerup",up); preview(); };
      h.addEventListener("pointermove",move); h.addEventListener("pointerup",up);
      e.stopPropagation(); e.preventDefault();
    });
    h.addEventListener("dblclick", e=>{
      const i = +h.dataset.i;
      if(pts.length>3){ pts.splice(i,1); render(); preview(); }
      e.stopPropagation();
    });
  }

  // Drag the whole polygon.
  poly.addEventListener("pointerdown", e=>{
    const start=toNorm(e); const orig=pts.map(p=>({...p}));
    poly.setPointerCapture(e.pointerId);
    const move = ev=>{ const n=toNorm(ev); const dx=n.x-start.x, dy=n.y-start.y;
      pts = orig.map(p=>({x:clamp(p.x+dx), y:clamp(p.y+dy)})); render(); };
    const up = ()=>{ poly.removeEventListener("pointermove",move);
                     poly.removeEventListener("pointerup",up); preview(); };
    poly.addEventListener("pointermove",move); poly.addEventListener("pointerup",up);
    e.preventDefault();
  });

  // Click on empty canvas → insert a point on the nearest edge.
  stage.addEventListener("pointerdown", e=>{
    if(e.target!==stage && e.target.tagName!=="IMG" && e.target!==svg) return;
    const n=toNorm(e); insertOnNearestEdge(n); render(); preview();
  });
  function insertOnNearestEdge(n){
    let best=0, bestD=Infinity;
    for(let i=0;i<pts.length;i++){
      const a=pts[i], b=pts[(i+1)%pts.length];
      const d=segDist(n,a,b); if(d<bestD){bestD=d; best=i;}
    }
    pts.splice(best+1,0,{x:n.x,y:n.y});
  }
  function segDist(p,a,b){
    const dx=b.x-a.x, dy=b.y-a.y; const l2=dx*dx+dy*dy;
    let t = l2 ? ((p.x-a.x)*dx+(p.y-a.y)*dy)/l2 : 0; t=Math.max(0,Math.min(1,t));
    const cx=a.x+t*dx, cy=a.y+t*dy; return Math.hypot(p.x-cx,p.y-cy);
  }

  // Debounced live count preview.
  let t=null; const countEl=card.querySelector(".count");
  function preview(){
    if(!p.device_id){ countEl.textContent=""; return; }
    clearTimeout(t); t=setTimeout(async()=>{
      try{
        const r=await fetch(`${API}/rois/${encodeURIComponent(p.pane)}/preview`,{method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({device_id:p.device_id, shape:pts.map(p=>[p.x,p.y])})});
        const j=await r.json();
        countEl.textContent = (j.count==null) ? "no frame yet" : `${j.count} / ${j.total} heads in shape`;
      }catch(_){}
    },180);
  }

  const msg=card.querySelector(".msg");
  card.querySelector(".save").addEventListener("click",async()=>{
    const r=await fetch(`${API}/rois/${encodeURIComponent(p.pane)}`,{method:"PUT",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({shape:pts.map(p=>[r3(p.x),r3(p.y)])})});
    const j=await r.json();
    if(r.ok){ msg.className="msg ok"; msg.textContent="Saved."; renderAdv(j.advisories); }
    else   { msg.className="msg err"; msg.textContent="Rejected: "+(j.detail||"invalid shape"); }
  });
  card.querySelector(".undo").addEventListener("click",()=>{
    if(pts.length>3){ pts.pop(); render(); preview(); }
  });
  card.querySelector(".reset").addEventListener("click",()=>{
    pts=defaultQuad(); render(); preview();
  });
  card.querySelector(".clear").addEventListener("click",async()=>{
    const r=await fetch(`${API}/rois/${encodeURIComponent(p.pane)}`,{method:"DELETE"});
    const j=await r.json(); msg.className="msg ok"; msg.textContent="Cleared (full frame).";
    renderAdv(j.advisories);
  });

  render(); preview();
  return card;
}
function ensurePlaceholder(stage){
  if(stage.querySelector(".noframe")) return;
  const ph=document.createElement("div"); ph.className="noframe";
  ph.textContent="Waiting for first frame from this camera — you can still set the region.";
  stage.insertBefore(ph, stage.firstChild);
}
boot();
</script></body></html>"""
