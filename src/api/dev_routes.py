"""
dev_routes.py — DEV-ONLY endpoints, mounted under /dev.

Only included by main.py when APP_ENV=dev. Serves a live grid of the latest
annotated detection frames, one MJPEG stream per camera.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

dev_router = APIRouter()


_INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>AI Service — DEV detection viewer</title>
<style>
 body{background:#111;color:#eee;font-family:system-ui,sans-serif;margin:16px}
 h1{font-size:18px;margin:0 0 12px}
 figure{display:inline-block;margin:8px;text-align:center}
 img{border:1px solid #333;max-width:46vw;background:#000}
 figcaption{color:#8f8;font-size:13px;margin-top:4px}
</style></head><body>
<h1>DEV detection viewer <small id="s" style="color:#888">— waiting for frames…</small></h1>
<div id="grid"></div>
<script>
const seen=new Set();
async function tick(){
  try{
    const r=await fetch('devices');const j=await r.json();
    for(const d of j.devices){
      if(seen.has(d))continue;seen.add(d);
      document.getElementById('s').textContent='';
      const fig=document.createElement('figure');
      const im=document.createElement('img');im.src='stream/'+encodeURIComponent(d);
      const cap=document.createElement('figcaption');cap.textContent=d;
      fig.appendChild(im);fig.appendChild(cap);
      document.getElementById('grid').appendChild(fig);
    }
  }catch(e){}
}
setInterval(tick,2000);tick();
</script></body></html>"""


@dev_router.get("/", response_class=HTMLResponse)
async def dev_index() -> str:
    return _INDEX_HTML


@dev_router.get("/devices")
async def dev_devices(request: Request):
    viewer = request.app.state.dev_viewer
    return {"devices": viewer.devices()}


@dev_router.get("/stream/{device_id}")
async def dev_stream(device_id: str, request: Request):
    viewer = request.app.state.dev_viewer
    return StreamingResponse(
        viewer.stream(device_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
