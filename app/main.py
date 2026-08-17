from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import __version__
from .config import ConfigStore
from .ffmpeg import HLS_PROFILES, shell_join
from .session import SESSIONS, SessionManager

config = ConfigStore()
sessions = SessionManager(config)
app = FastAPI(title="Samsung TV Plus Stream Lab", version=__version__)
BASE = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


class StreamIn(BaseModel):
    id: str
    name: str = ""
    input_url: str
    user_agent: str = ""
    play_profile: str = "normalize-hls"
    enabled: bool = True


class StartIn(BaseModel):
    profile: str = "copy-null"


@app.get("/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "version": __version__})


@app.get("/api/status")
def status():
    return {
        "version": __version__,
        "streams": config.streams(),
        "sessions": sessions.all(),
        "settings": config.settings(),
    }


@app.post("/api/streams")
def save_stream(body: StreamIn):
    try:
        return config.upsert(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/streams/{sid}")
def delete_stream(sid: str):
    config.delete(sid)
    return {"ok": True}


@app.post("/api/streams/{sid}/start")
def start(sid: str, body: StartIn):
    try:
        return sessions.start(sid, body.profile).status()
    except KeyError:
        raise HTTPException(404, "Unknown stream")
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/sessions/{session_id}/stop")
def stop(session_id: str):
    sessions.stop(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/bundle")
def bundle(session_id: str):
    try:
        p = sessions.bundle(session_id)
        return FileResponse(p, filename=p.name, media_type="application/zip")
    except FileNotFoundError:
        raise HTTPException(404, "Unknown session")


@app.get("/api/sessions/{session_id}/events")
def events(session_id: str):
    p = SESSIONS / session_id / "events.jsonl"
    if not p.exists():
        raise HTTPException(404, "No events")
    return Response(p.read_text(), media_type="application/x-ndjson")


@app.get("/api/streams/{sid}/probe")
def probe(sid: str):
    s = config.stream(sid)
    if not s:
        raise HTTPException(404, "Unknown stream")
    cmd = [
        str(config.settings().get("ffprobe_path", "ffprobe")),
        "-v", "error", "-show_streams", "-show_format", "-of", "json",
    ]
    if s.get("user_agent"):
        cmd += ["-user_agent", s["user_agent"]]
    cmd += [s["input_url"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return {"command": shell_join(cmd), "returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/stream/{sid}/index.m3u8")
async def hls_playlist(sid: str):
    s = config.stream(sid)
    if not s:
        raise HTTPException(404, "Unknown stream")
    profile = s.get("play_profile", "normalize-hls")
    if profile not in HLS_PROFILES:
        raise HTTPException(400, "play_profile must be an HLS profile")
    rt = sessions.latest_hls(sid, profile)
    p = rt.root / "output" / "index.m3u8"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if p.exists():
            return Response(
                p.read_text(errors="replace"),
                media_type="application/vnd.apple.mpegurl",
                headers={"Cache-Control": "no-store", "X-Stream-Lab-Session": rt.session_id},
            )
        if rt.proc and rt.proc.poll() is not None:
            raise HTTPException(502, "FFmpeg exited before producing HLS")
        await asyncio.sleep(0.2)
    raise HTTPException(503, "HLS startup timeout")


def _ts_response(sid: str, *, permissive: bool) -> StreamingResponse:
    try:
        rt = sessions.start_ts_relay(sid, permissive=permissive)
    except KeyError:
        raise HTTPException(404, "Unknown stream")
    except Exception as exc:
        raise HTTPException(500, str(exc))

    proc = rt.proc
    assert proc is not None and proc.stdout is not None

    def gen():
        try:
            while True:
                chunk = proc.stdout.read(188 * 64)
                if not chunk:
                    break
                yield chunk
        finally:
            sessions.stop(rt.session_id, reason="client_disconnect_or_eof")

    return StreamingResponse(
        gen(),
        media_type="video/mp2t",
        headers={
            "Cache-Control": "no-store",
            "X-Stream-Lab-Session": rt.session_id,
            "X-Stream-Lab-Profile": rt.profile,
        },
    )


@app.get("/stream/{sid}/stream.ts")
def ts_stream(sid: str):
    return _ts_response(sid, permissive=False)


@app.get("/stream/{sid}/stream-permissive.ts")
def ts_stream_permissive(sid: str):
    return _ts_response(sid, permissive=True)


@app.get("/stream/{sid}/{filename}")
def hls_segment(sid: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    s = config.stream(sid)
    if not s:
        raise HTTPException(404, "Unknown stream")
    profile = s.get("play_profile", "normalize-hls")
    rt = sessions.latest_hls(sid, profile)
    p = rt.root / "output" / filename
    if not p.exists():
        raise HTTPException(404, "Segment not found")
    return FileResponse(p, media_type="video/mp2t")
