from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import __version__
from .config import ConfigStore, DEFAULT_UA
from .ffmpeg import HLS_PROFILES, shell_join
from .guide import GuideService, build_m3u, is_exported
from .session import SESSIONS, SessionManager
from .source import SourceService

config = ConfigStore()
sessions = SessionManager(config)
guide = GuideService(config)
sources = SourceService(config, guide)
app = FastAPI(title="Samsung TV Plus Stream Lab", version=__version__)
BASE = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


class StreamIn(BaseModel):
    id: str
    name: str = ""
    input_url: str
    user_agent: str = ""
    play_profile: str = "normalize-hls-permissive"
    enabled: bool = True
    selected: bool | None = None
    tvg_id: str = ""
    tvg_logo: str = ""
    group_title: str = "Stream Lab"
    channel_number: str = ""
    xmltv_url: str = ""
    xmltv_channel_id: str = ""


class SourceIn(BaseModel):
    id: str
    name: str = ""
    m3u_url: str
    xmltv_url: str = ""
    user_agent: str = DEFAULT_UA
    default_profile: str = "normalize-hls-sync-permissive"
    refresh_hours: float = Field(default=6, ge=1, le=168)
    enabled: bool = True


class StartIn(BaseModel):
    profile: str = "copy-null"


class SelectionIn(BaseModel):
    ids: list[str]
    selected: bool


class ChannelPatch(BaseModel):
    selected: bool | None = None
    play_profile: str | None = None
    channel_number: str | None = None
    group_title: str | None = None
    tvg_logo: str | None = None
    tvg_id: str | None = None
    xmltv_channel_id: str | None = None


class TuningSettingsIn(BaseModel):
    dts_delta_threshold: float = Field(default=60.0, ge=0.1, le=3600)
    av_sync_probe_seconds: int = Field(default=30, ge=5, le=300)
    av_sync_warn_seconds: float = Field(default=1.0, ge=0.05, le=60)
    audio_sync_bitrate_kbps: int = Field(default=160, ge=64, le=320)
    hls_idle_timeout_seconds: int = Field(default=30, ge=10, le=600)
    variant_pin_enabled: bool = True
    variant_quality: str = "auto"
    auto_recovery_enabled: bool = True
    av_sync_recovery_samples: int = Field(default=2, ge=1, le=10)
    recovery_stall_seconds: int = Field(default=20, ge=10, le=300)
    ssai_capture_enabled: bool = True


@app.on_event("startup")
def startup() -> None:
    sources.start_scheduler()


@app.on_event("shutdown")
def shutdown() -> None:
    sources.stop_scheduler()


@app.get("/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "version": __version__})


def _summary(streams: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [s for s in streams if bool(s.get("selected", s.get("enabled", True)))]
    exported = [s for s in streams if is_exported(s)]
    return {
        "channels": len(streams),
        "selected": len(selected),
        "exported": len(exported),
        "missing": sum(1 for s in streams if s.get("missing")),
        "epg_matched": sum(1 for s in exported if s.get("epg_matched")),
        "groups": len({str(s.get("group_title") or "") for s in streams}),
    }


@app.get("/api/status")
def status():
    streams = config.streams()
    return {
        "version": __version__,
        "sources": config.sources(),
        "sessions": sessions.all(),
        "settings": config.settings(),
        "summary": _summary(streams),
    }


@app.post("/api/settings/tuning")
def save_tuning_settings(body: TuningSettingsIn):
    try:
        return config.update_settings(**body.model_dump())
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/channels")
def channels(
    q: str = "",
    source_id: str = "",
    group: str = "",
    selected: str = "",
    epg: str = "",
    missing: str = "",
):
    items = config.streams()
    needle = q.strip().casefold()
    if needle:
        items = [x for x in items if needle in " ".join([
            str(x.get("name") or ""), str(x.get("tvg_id") or ""), str(x.get("channel_number") or ""),
            str(x.get("group_title") or ""), str(x.get("source_id") or "")
        ]).casefold()]
    if source_id:
        items = [x for x in items if str(x.get("source_id") or "") == source_id]
    if group:
        items = [x for x in items if str(x.get("group_title") or "") == group]
    if selected in {"true", "false"}:
        want = selected == "true"
        items = [x for x in items if bool(x.get("selected", x.get("enabled", True))) == want]
    if epg in {"true", "false"}:
        want = epg == "true"
        items = [x for x in items if bool(x.get("epg_matched")) == want]
    if missing in {"true", "false"}:
        want = missing == "true"
        items = [x for x in items if bool(x.get("missing")) == want]
    items.sort(key=lambda x: (str(x.get("group_title") or "").casefold(), str(x.get("channel_number") or "").zfill(8), str(x.get("name") or "").casefold()))
    return {
        "items": items,
        "count": len(items),
        "groups": sorted({str(x.get("group_title") or "") for x in config.streams() if str(x.get("group_title") or "")}),
    }


@app.post("/api/sources")
def save_source(body: SourceIn):
    try:
        return config.upsert_source(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/sources/{source_id}/refresh")
def refresh_source(source_id: str):
    try:
        stats = sources.refresh(source_id)
        return {"ok": True, **stats}
    except KeyError:
        raise HTTPException(404, "Unknown source")
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str, delete_channels: bool = True):
    config.delete_source(source_id, delete_channels=delete_channels)
    guide.invalidate()
    return {"ok": True}


@app.post("/api/channels/select")
def select_channels(body: SelectionIn):
    count = config.set_selected(body.ids, body.selected)
    guide.invalidate()
    return {"ok": True, "updated": count}


@app.patch("/api/channels/{sid}")
def patch_channel(sid: str, body: ChannelPatch):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "play_profile" in fields and fields["play_profile"] not in HLS_PROFILES:
        raise HTTPException(400, "Unknown HLS profile")
    saved = config.update_stream_fields(sid, **fields)
    if not saved:
        raise HTTPException(404, "Unknown channel")
    guide.invalidate()
    return saved


@app.get("/playlist.m3u")
def jellyfin_playlist(request: Request):
    payload = build_m3u(config.streams(), str(request.base_url).rstrip("/"))
    return Response(payload, media_type="audio/x-mpegurl", headers={"Cache-Control": "no-cache", "Content-Disposition": 'inline; filename="stream-lab.m3u"'})


@app.get("/guide.xml")
def jellyfin_guide():
    payload = guide.xml()
    return Response(payload, media_type="application/xml", headers={"Cache-Control": "no-cache", "Content-Disposition": 'inline; filename="stream-lab.xml"'})


@app.get("/api/guide/status")
def guide_status():
    return guide.status()


@app.post("/api/guide/refresh")
def guide_refresh():
    guide.invalidate()
    payload = guide.xml(force=True)
    return {"ok": True, "bytes": len(payload), **guide.status()}


# Manual-stream API retained for one-off diagnostics and backwards compatibility.
@app.post("/api/streams")
def save_stream(body: StreamIn):
    try:
        saved = config.upsert(body.model_dump())
        guide.invalidate()
        return saved
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/streams/{sid}")
def delete_stream(sid: str):
    config.delete(sid)
    guide.invalidate()
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
    return Response(p.read_text(encoding="utf-8"), media_type="application/x-ndjson")


@app.get("/api/streams/{sid}/probe")
def probe(sid: str):
    s = config.stream(sid)
    if not s:
        raise HTTPException(404, "Unknown stream")
    cmd = [str(config.settings().get("ffprobe_path", "ffprobe")), "-v", "error", "-show_streams", "-show_format", "-of", "json"]
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
    if s.get("missing"):
        raise HTTPException(410, "Channel is currently missing from its upstream source")
    profile = s.get("play_profile", "normalize-hls-permissive")
    if profile not in HLS_PROFILES:
        raise HTTPException(400, "play_profile must be an HLS profile")
    rt = sessions.latest_hls(sid, profile)
    p = rt.root / "output" / "index.m3u8"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if p.exists():
            return Response(sessions.render_playlist(rt), media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store", "X-Stream-Lab-Session": rt.session_id, "X-Stream-Lab-Generation": str(rt.recovery_generation)})
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

    return StreamingResponse(gen(), media_type="video/mp2t", headers={"Cache-Control": "no-store", "X-Stream-Lab-Session": rt.session_id, "X-Stream-Lab-Profile": rt.profile})


@app.get("/stream/{sid}/stream.ts")
def ts_stream(sid: str):
    return _ts_response(sid, permissive=False)


@app.get("/stream/{sid}/stream-permissive.ts")
def ts_stream_permissive(sid: str):
    return _ts_response(sid, permissive=True)


@app.get("/stream/{sid}/segments/{session_id}/{filename}")
def hls_generation_segment(sid: str, session_id: str, filename: str):
    if "/" in filename or ".." in filename or "/" in session_id or ".." in session_id:
        raise HTTPException(400, "Invalid segment path")
    rt = sessions.runtimes.get(session_id)
    root = rt.root if rt else SESSIONS / session_id
    if rt and str(rt.stream_id) != str(sid):
        raise HTTPException(404, "Segment not found")
    if not rt and not session_id.startswith(f"{sid}-"):
        raise HTTPException(404, "Segment not found")
    p = root / "output" / filename
    if not p.exists():
        raise HTTPException(404, "Segment not found")
    if rt:
        rt.last_client_access = time.time()
    return FileResponse(p, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@app.get("/stream/{sid}/{filename}")
def hls_segment(sid: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    s = config.stream(sid)
    if not s:
        raise HTTPException(404, "Unknown stream")
    profile = s.get("play_profile", "normalize-hls-permissive")
    rt = sessions.latest_hls(sid, profile)
    rt.last_client_access = time.time()
    p = rt.root / "output" / filename
    if not p.exists():
        raise HTTPException(404, "Segment not found")
    return FileResponse(p, media_type="video/mp2t")
