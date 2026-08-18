from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .config import DATA_DIR, ConfigStore
from .ffmpeg import HLS_PROFILES, build, build_ts_relay, shell_join
from .hls import media_segments_and_boundaries, resolve_variant

SESSIONS = DATA_DIR / "sessions"
SESSIONS.mkdir(parents=True, exist_ok=True)
MEDIA_SEQ = re.compile(r"^#EXT-X-MEDIA-SEQUENCE:(\d+)", re.M)
URI_ATTR = re.compile(r'URI="([^"]+)"')
TOPOLOGY_LINE = re.compile(r"New (audio|video) stream with index (\d+)", re.I)
PROGRESS_KEYS = {
    "frame", "fps", "stream_0_0_q", "bitrate", "total_size", "out_time_us",
    "out_time_ms", "out_time", "dup_frames", "drop_frames", "speed", "progress",
}


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def event(path: Path, kind: str, **details: Any) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": utc(), "event": kind, **details}, sort_keys=True) + "\n")


def _manifest_uri_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def child_playlist_urls(text: str, base_url: str) -> list[str]:
    """Return child playlist URIs from a master playlist, preserving order."""
    found: list[str] = []
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            for nxt in lines[i + 1:]:
                nxt = nxt.strip()
                if not nxt:
                    continue
                if nxt.startswith("#"):
                    continue
                found.append(urljoin(base_url, nxt))
                break
        elif line.startswith("#EXT-X-MEDIA:") or line.startswith("#EXT-X-I-FRAME-STREAM-INF:"):
            m = URI_ATTR.search(line)
            if m:
                found.append(urljoin(base_url, m.group(1)))
    return list(dict.fromkeys(found))


def parse_manifest(text: str, base_url: str | None = None) -> dict[str, Any]:
    m = MEDIA_SEQ.search(text)
    is_master = "#EXT-X-STREAM-INF:" in text or "#EXT-X-I-FRAME-STREAM-INF:" in text
    uri_lines = _manifest_uri_lines(text)
    segment_urls: list[str] = [] if is_master else [urljoin(base_url or "", x) for x in uri_lines]
    extensionless: list[str] = []
    for uri in segment_urls:
        path = urlparse(uri).path
        if not PurePosixPath(path).suffix:
            extensionless.append(uri)
    return {
        "playlist_type": "master" if is_master else "media",
        "media_sequence": int(m.group(1)) if m else None,
        "discontinuities": text.count("#EXT-X-DISCONTINUITY"),
        "assets": text.count("#EXT-X-ASSET"),
        "variants": len(child_playlist_urls(text, base_url or "")) if is_master else 0,
        "segments": len(segment_urls),
        "extensionless_segments": len(extensionless),
        "extensionless_segment_urls": extensionless[-8:],
        "program_date_time": next(
            (x.split(":", 1)[1] for x in text.splitlines() if x.startswith("#EXT-X-PROGRAM-DATE-TIME:")),
            None,
        ),
    }


def _slug_for_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8", errors="replace")).hexdigest()[:10]


@dataclass
class Runtime:
    session_id: str
    stream_id: str
    profile: str
    root: Path
    proc: subprocess.Popen | None = None
    started: float = field(default_factory=time.time)
    stopped: float | None = None
    frame: int = 0
    out_time_ms: int = 0
    last_progress: float = field(default_factory=time.time)
    freeze_logged: bool = False
    monitor_stop: threading.Event = field(default_factory=threading.Event)
    managed_relay: bool = False
    published: bool = False
    last_client_access: float = field(default_factory=time.time)
    av_sync_offset_seconds: float | None = None
    av_sync_max_abs_seconds: float = 0.0
    av_sync_status: str = "unknown"
    av_sync_samples: int = 0
    av_sync_bad_samples: int = 0
    av_sync_last_probe: float = 0.0
    resolved_input_url: str = ""
    master_input_url: str = ""
    variant_quality: str = ""
    variant_width: int = 0
    variant_height: int = 0
    variant_bandwidth: int = 0
    variant_pinned: bool = False
    variant_count: int = 0
    recovery_generation: int = 0
    recovery_scheduled: bool = False
    recovery_reason: str = ""
    sequence_base: int = 0
    ssai_capture_count: int = 0
    seen_ssai_boundaries: set[str] = field(default_factory=set)
    topology_changes: int = 0
    log_scan_offset: int = 0

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stream_id": self.stream_id,
            "profile": self.profile,
            "running": bool(self.proc and self.proc.poll() is None),
            "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
            "frame": self.frame,
            "out_time_ms": self.out_time_ms,
            "progress_age": round(time.time() - self.last_progress, 2),
            "path": str(self.root),
            "managed_relay": self.managed_relay,
            "published": self.published,
            "client_age": round(time.time() - self.last_client_access, 2),
            "av_sync_offset_seconds": None if self.av_sync_offset_seconds is None else round(self.av_sync_offset_seconds, 3),
            "av_sync_max_abs_seconds": round(self.av_sync_max_abs_seconds, 3),
            "av_sync_status": self.av_sync_status,
            "av_sync_samples": self.av_sync_samples,
            "resolved_input_url": self.resolved_input_url,
            "variant_quality": self.variant_quality,
            "variant_width": self.variant_width,
            "variant_height": self.variant_height,
            "variant_bandwidth": self.variant_bandwidth,
            "variant_pinned": self.variant_pinned,
            "variant_count": self.variant_count,
            "recovery_generation": self.recovery_generation,
            "recovery_reason": self.recovery_reason,
            "ssai_capture_count": self.ssai_capture_count,
            "topology_changes": self.topology_changes,
        }


class SessionManager:
    def __init__(self, config: ConfigStore):
        self.config = config
        self.lock = threading.RLock()
        self.runtimes: dict[str, Runtime] = {}
        self.active_relays: dict[tuple[str, str], str] = {}
        self.sequence_bases: dict[str, int] = {}
        self.recovery_history: dict[str, list[float]] = {}
        threading.Thread(target=self._reaper, daemon=True, name="hls-relay-reaper").start()

    def _new_root(self, sid: str, profile: str) -> tuple[str, Path]:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        session_id = f"{sid}-{profile}-{stamp}"
        base_id = session_id
        suffix = 2
        while (SESSIONS / session_id).exists():
            session_id = f"{base_id}-{suffix}"
            suffix += 1
        root = SESSIONS / session_id
        (root / "output").mkdir(parents=True, exist_ok=True)
        (root / "manifests").mkdir(exist_ok=True)
        (root / "ssai-captures").mkdir(exist_ok=True)
        return session_id, root

    def _next_sequence_base(self, sid: str) -> int:
        with self.lock:
            candidate = int(time.time() * 1000)
            previous = self.sequence_bases.get(sid, 0)
            base = max(candidate, previous + 100000)
            self.sequence_bases[sid] = base
            return base

    def _resolve_input(self, stream: dict[str, Any], settings: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        resolved_stream = dict(stream)
        resolution: dict[str, Any]
        try:
            resolution = resolve_variant(
                str(stream["input_url"]),
                user_agent=str(stream.get("user_agent") or ""),
                quality=str(settings.get("variant_quality", "auto")),
                enabled=bool(settings.get("variant_pin_enabled", True)),
                timeout_seconds=10.0,
            )
            resolved_stream["resolved_input_url"] = str(resolution.get("resolved_url") or stream["input_url"])
        except Exception as exc:
            resolution = {
                "original_url": str(stream.get("input_url") or ""),
                "master_url": str(stream.get("input_url") or ""),
                "resolved_url": str(stream.get("input_url") or ""),
                "pinned": False,
                "quality": str(settings.get("variant_quality", "auto")),
                "variant": None,
                "variant_count": 0,
                "error": str(exc),
            }
            resolved_stream["resolved_input_url"] = str(stream["input_url"])
        return resolved_stream, resolution

    def _metadata(self, stream: dict[str, Any], profile: str, session_id: str, settings: dict[str, Any], resolution: dict[str, Any], generation: int) -> dict[str, Any]:
        metadata = dict(stream)
        metadata.update({
            "configured_play_profile": stream.get("play_profile"),
            "session_profile": profile,
            "session_id": session_id,
            "session_started_utc": utc(),
            "effective_dts_delta_threshold": settings.get("dts_delta_threshold", 60.0),
            "audio_sync_bitrate_kbps": settings.get("audio_sync_bitrate_kbps", 160),
            "av_sync_warn_seconds": settings.get("av_sync_warn_seconds", 1.0),
            "variant_resolution": resolution,
            "recovery_generation": generation,
            "auto_recovery_enabled": settings.get("auto_recovery_enabled", True),
            "ssai_capture_enabled": settings.get("ssai_capture_enabled", True),
        })
        return metadata

    def _apply_resolution(self, rt: Runtime, resolution: dict[str, Any]) -> None:
        variant = resolution.get("variant") or {}
        rt.resolved_input_url = str(resolution.get("resolved_url") or "")
        rt.master_input_url = str(resolution.get("master_url") or "")
        rt.variant_quality = str(resolution.get("quality") or "")
        rt.variant_pinned = bool(resolution.get("pinned"))
        rt.variant_count = int(resolution.get("variant_count") or 0)
        rt.variant_width = int(variant.get("width") or 0)
        rt.variant_height = int(variant.get("height") or 0)
        rt.variant_bandwidth = int(variant.get("bandwidth") or 0)

    def start(
        self,
        sid: str,
        profile: str,
        *,
        managed_relay: bool = False,
        recovery_generation: int = 0,
        publish_managed: bool = True,
        recovery_reason: str = "",
    ) -> Runtime:
        stream = self.config.stream(sid)
        if not stream:
            raise KeyError(sid)
        settings = self.config.settings()
        session_id, root = self._new_root(sid, profile)
        resolved_stream, resolution = self._resolve_input(stream, settings)
        cmd = build(profile, resolved_stream, settings, root / "output")
        (root / "command.txt").write_text(shell_join(cmd) + "\n", encoding="utf-8")
        (root / "stream.json").write_text(
            json.dumps(self._metadata(stream, profile, session_id, settings, resolution, recovery_generation), indent=2) + "\n",
            encoding="utf-8",
        )
        log = (root / "ffmpeg.log").open("wb", buffering=0)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        rt = Runtime(
            session_id,
            sid,
            profile,
            root,
            proc,
            managed_relay=managed_relay,
            published=bool(managed_relay and publish_managed),
            recovery_generation=recovery_generation,
            recovery_reason=recovery_reason,
            sequence_base=self._next_sequence_base(sid) if managed_relay else 0,
        )
        self._apply_resolution(rt, resolution)
        with self.lock:
            self.runtimes[session_id] = rt
            if rt.published:
                self.active_relays[(sid, profile)] = session_id
        event(
            root / "events.jsonl",
            "session_start",
            profile=profile,
            command=shell_join(cmd),
            dts_delta_threshold=settings.get("dts_delta_threshold", 60.0),
            managed_relay=managed_relay,
            recovery_generation=recovery_generation,
            variant_resolution=resolution,
        )
        if resolution.get("error"):
            event(root / "events.jsonl", "variant_resolution_error", error=resolution["error"])
        elif resolution.get("pinned"):
            event(root / "events.jsonl", "variant_pinned", **resolution)
        threading.Thread(target=self._progress, args=(rt,), daemon=True).start()
        threading.Thread(target=self._monitor, args=(rt, stream), daemon=True).start()
        return rt

    def _prepare_ts(self, sid: str, profile: str, stream: dict[str, Any], cmd: list[str], resolution: dict[str, Any]) -> Runtime:
        session_id, root = self._new_root(sid, profile)
        settings = self.config.settings()
        (root / "command.txt").write_text(shell_join(cmd) + "\n", encoding="utf-8")
        (root / "stream.json").write_text(
            json.dumps(self._metadata(stream, profile, session_id, settings, resolution, 0), indent=2) + "\n",
            encoding="utf-8",
        )
        rt = Runtime(session_id, sid, profile, root)
        self._apply_resolution(rt, resolution)
        self.runtimes[session_id] = rt
        event(root / "events.jsonl", "session_start", profile=profile, command=shell_join(cmd), variant_resolution=resolution)
        return rt

    def start_ts_relay(self, sid: str, *, permissive: bool = False) -> Runtime:
        stream = self.config.stream(sid)
        if not stream:
            raise KeyError(sid)
        profile = "continuous-ts-permissive" if permissive else "continuous-ts"
        settings = self.config.settings()
        resolved_stream, resolution = self._resolve_input(stream, settings)
        cmd = build_ts_relay(resolved_stream, settings, permissive=permissive)
        rt = self._prepare_ts(sid, profile, stream, cmd, resolution)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            start_new_session=True,
        )
        rt.proc = proc
        event(rt.root / "events.jsonl", "ts_client_attached", permissive=permissive)
        threading.Thread(target=self._ts_stderr_progress, args=(rt,), daemon=True).start()
        threading.Thread(target=self._monitor, args=(rt, stream), daemon=True).start()
        return rt

    def _record_progress(self, rt: Runtime, block: dict[str, str]) -> None:
        try:
            new_ms = int(block.get("out_time_us", "0")) // 1000
        except Exception:
            new_ms = rt.out_time_ms
        try:
            rt.frame = int(block.get("frame", rt.frame))
        except Exception:
            pass
        if new_ms > rt.out_time_ms:
            rt.out_time_ms = new_ms
            rt.last_progress = time.time()
            rt.freeze_logged = False
        with (rt.root / "progress.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": utc(), **block}) + "\n")

    def _progress(self, rt: Runtime) -> None:
        block: dict[str, str] = {}
        try:
            assert rt.proc is not None and rt.proc.stdout is not None
            for line in rt.proc.stdout:
                line = line.strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                block[k] = v
                if k == "progress":
                    self._record_progress(rt, block)
                    block = {}
        finally:
            rt.stopped = time.time()
            event(rt.root / "events.jsonl", "ffmpeg_exit", returncode=rt.proc.poll() if rt.proc else None)
            if rt.managed_relay and rt.published and not rt.monitor_stop.is_set():
                self._schedule_recovery(rt, "ffmpeg_exit")

    def _ts_stderr_progress(self, rt: Runtime) -> None:
        block: dict[str, str] = {}
        log_path = rt.root / "ffmpeg.log"
        try:
            assert rt.proc is not None and rt.proc.stderr is not None
            with log_path.open("ab", buffering=0) as log:
                while True:
                    raw = rt.proc.stderr.readline()
                    if not raw:
                        break
                    log.write(raw)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k not in PROGRESS_KEYS:
                        continue
                    block[k] = v
                    if k == "progress":
                        self._record_progress(rt, block)
                        block = {}
        finally:
            rt.stopped = time.time()
            event(rt.root / "events.jsonl", "ffmpeg_exit", returncode=rt.proc.poll() if rt.proc else None)

    def _snapshot_manifest(
        self,
        rt: Runtime,
        *,
        kind: str,
        url: str,
        text: str,
        response: httpx.Response,
        last: dict[str, str],
        counts: dict[str, int],
    ) -> bool:
        if last.get(url) == text:
            return False
        last[url] = text
        counts[url] = counts.get(url, 0) + 1
        short = _slug_for_url(url)
        filename = f"{kind}-{short}-{counts[url]:05d}.m3u8"
        (rt.root / "manifests" / filename).write_text(text, encoding="utf-8", errors="replace")
        meta = parse_manifest(text, url)
        event(
            rt.root / "events.jsonl",
            f"{kind}_manifest_change",
            manifest_url=url,
            final_url=str(response.url),
            http_status=response.status_code,
            content_type=response.headers.get("content-type"),
            snapshot=filename,
            **meta,
        )
        if meta["extensionless_segments"]:
            event(
                rt.root / "events.jsonl",
                "extensionless_segments_observed",
                manifest_url=url,
                count=meta["extensionless_segments"],
                urls=meta["extensionless_segment_urls"],
            )
        return True

    def _capture_ssai_window(
        self,
        rt: Runtime,
        *,
        manifest_url: str,
        manifest_text: str,
        segments: list[str],
        boundary: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        settings = self.config.settings()
        before = max(0, int(settings.get("ssai_capture_segments_before", 4)))
        after = max(1, int(settings.get("ssai_capture_segments_after", 8)))
        max_bytes = max(1, int(settings.get("ssai_capture_max_mb", 64))) * 1024 * 1024
        idx = int(boundary.get("segment_index") or 0)
        chosen = segments[max(0, idx - before): min(len(segments), idx + after + 1)]
        with self.lock:
            rt.ssai_capture_count += 1
            capture_no = rt.ssai_capture_count
        capture_dir = rt.root / "ssai-captures" / f"capture_{capture_no:03d}"
        (capture_dir / "segments").mkdir(parents=True, exist_ok=True)
        (capture_dir / "manifest.m3u8").write_text(manifest_text, encoding="utf-8", errors="replace")
        records: list[dict[str, Any]] = []
        total = 0
        with httpx.Client(timeout=httpx.Timeout(10, connect=4), follow_redirects=True, headers=headers) as client:
            for n, url in enumerate(chosen):
                if total >= max_bytes:
                    break
                record: dict[str, Any] = {"url": url, "relative_index": n - min(before, idx)}
                try:
                    response = client.get(url)
                    record.update({
                        "status": response.status_code,
                        "final_url": str(response.url),
                        "content_type": response.headers.get("content-type"),
                        "bytes": len(response.content),
                    })
                    if response.is_success and total + len(response.content) <= max_bytes:
                        filename = f"segment_{n:03d}.ts"
                        (capture_dir / "segments" / filename).write_bytes(response.content)
                        record["file"] = f"segments/{filename}"
                        total += len(response.content)
                except Exception as exc:
                    record["error"] = str(exc)
                records.append(record)
        metadata = {
            "captured_at_utc": utc(),
            "manifest_url": manifest_url,
            "boundary": boundary,
            "requested_before": before,
            "requested_after": after,
            "segments_captured": sum(1 for x in records if x.get("file")),
            "bytes_captured": total,
            "segments": records,
        }
        (capture_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        event(
            rt.root / "events.jsonl",
            "ssai_capture_complete",
            capture=f"capture_{capture_no:03d}",
            segments=metadata["segments_captured"],
            bytes=total,
            boundary=boundary.get("key"),
        )

    def _handle_selected_manifest(self, rt: Runtime, url: str, text: str, headers: dict[str, str]) -> None:
        segments, boundaries = media_segments_and_boundaries(text, url)
        for boundary in boundaries:
            key = str(boundary.get("key") or "")
            if not key or key in rt.seen_ssai_boundaries:
                continue
            rt.seen_ssai_boundaries.add(key)
            event(
                rt.root / "events.jsonl",
                "ssai_boundary_detected",
                manifest_url=url,
                boundary_segment=key,
                asset=boundary.get("asset", ""),
            )
            if bool(self.config.settings().get("ssai_capture_enabled", True)):
                threading.Thread(
                    target=self._capture_ssai_window,
                    kwargs={
                        "rt": rt,
                        "manifest_url": url,
                        "manifest_text": text,
                        "segments": list(segments),
                        "boundary": dict(boundary),
                        "headers": dict(headers),
                    },
                    daemon=True,
                    name=f"ssai-capture-{rt.session_id}",
                ).start()

    def _scan_ffmpeg_log(self, rt: Runtime) -> None:
        path = rt.root / "ffmpeg.log"
        if not path.exists():
            return
        try:
            size = path.stat().st_size
            if size < rt.log_scan_offset:
                rt.log_scan_offset = 0
            if size == rt.log_scan_offset:
                return
            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(rt.log_scan_offset)
                chunk = f.read()
                rt.log_scan_offset = f.tell()
            for line in chunk.splitlines():
                match = TOPOLOGY_LINE.search(line)
                if match:
                    rt.topology_changes += 1
                    event(
                        rt.root / "events.jsonl",
                        "input_stream_topology_change",
                        media_type=match.group(1).lower(),
                        stream_index=int(match.group(2)),
                        line=line[-1000:],
                    )
        except Exception:
            return

    def _sample_av_sync(self, rt: Runtime) -> None:
        settings = self.config.settings()
        interval = max(5.0, float(settings.get("av_sync_probe_seconds", 30)))
        now = time.time()
        if now - rt.av_sync_last_probe < interval:
            return
        rt.av_sync_last_probe = now
        output_dir = rt.root / "output"
        segments = sorted(output_dir.glob("segment_*.ts"))
        if not segments:
            return
        segment = segments[-1]
        cmd = [
            str(settings.get("ffprobe_path", "ffprobe")),
            "-v", "error",
            "-show_entries", "stream=codec_type,start_time",
            "-of", "json",
            str(segment),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
            if result.returncode != 0:
                return
            payload = json.loads(result.stdout or "{}")
            starts: dict[str, float] = {}
            for item in payload.get("streams", []):
                kind = str(item.get("codec_type") or "")
                if kind not in {"video", "audio"} or kind in starts:
                    continue
                try:
                    starts[kind] = float(item.get("start_time"))
                except (TypeError, ValueError):
                    pass
            if "video" not in starts or "audio" not in starts:
                return
            offset = starts["audio"] - starts["video"]
            warning = max(0.05, float(settings.get("av_sync_warn_seconds", 1.0)))
            previous = rt.av_sync_status
            rt.av_sync_offset_seconds = offset
            rt.av_sync_max_abs_seconds = max(rt.av_sync_max_abs_seconds, abs(offset))
            rt.av_sync_samples += 1
            rt.av_sync_status = "desynced" if abs(offset) >= warning else "healthy"
            if rt.av_sync_status == "desynced":
                rt.av_sync_bad_samples += 1
            else:
                rt.av_sync_bad_samples = 0
            event(
                rt.root / "events.jsonl",
                "av_sync_sample",
                segment=segment.name,
                offset_seconds=round(offset, 6),
                max_abs_seconds=round(rt.av_sync_max_abs_seconds, 6),
                status=rt.av_sync_status,
                bad_samples=rt.av_sync_bad_samples,
                warning_threshold_seconds=warning,
            )
            if rt.av_sync_status == "desynced" and previous != "desynced":
                event(
                    rt.root / "events.jsonl",
                    "av_sync_warning",
                    segment=segment.name,
                    offset_seconds=round(offset, 6),
                    warning_threshold_seconds=warning,
                )
            needed = max(1, int(settings.get("av_sync_recovery_samples", 2)))
            if (
                rt.managed_relay
                and rt.published
                and bool(settings.get("auto_recovery_enabled", True))
                and rt.av_sync_bad_samples >= needed
            ):
                self._schedule_recovery(rt, f"av_desync:{offset:.3f}s")
        except Exception as exc:
            event(rt.root / "events.jsonl", "av_sync_probe_error", segment=segment.name, error=str(exc))

    def _recovery_allowed(self, rt: Runtime) -> bool:
        settings = self.config.settings()
        if not bool(settings.get("auto_recovery_enabled", True)):
            return False
        now = time.time()
        history = [x for x in self.recovery_history.get(rt.stream_id, []) if now - x < 300]
        self.recovery_history[rt.stream_id] = history
        limit = max(1, int(settings.get("recovery_max_5min", 3)))
        if len(history) >= limit:
            event(rt.root / "events.jsonl", "auto_recovery_suppressed", reason="rate_limit", attempts_5min=len(history), limit=limit)
            return False
        return True

    def _schedule_recovery(self, rt: Runtime, reason: str) -> None:
        with self.lock:
            active = self.active_relays.get((rt.stream_id, rt.profile))
            if rt.recovery_scheduled or not rt.managed_relay or not rt.published or active != rt.session_id:
                return
            if not self._recovery_allowed(rt):
                return
            rt.recovery_scheduled = True
            rt.recovery_reason = reason
        event(rt.root / "events.jsonl", "auto_recovery_requested", reason=reason, generation=rt.recovery_generation)
        threading.Thread(target=self._recover, args=(rt, reason), daemon=True, name=f"recover-{rt.stream_id}").start()

    def _recover(self, old: Runtime, reason: str) -> None:
        new: Runtime | None = None
        try:
            new = self.start(
                old.stream_id,
                old.profile,
                managed_relay=True,
                recovery_generation=old.recovery_generation + 1,
                publish_managed=False,
                recovery_reason=reason,
            )
            timeout = max(5.0, float(self.config.settings().get("recovery_startup_timeout_seconds", 20)))
            deadline = time.time() + timeout
            ready = False
            while time.time() < deadline:
                if new.proc and new.proc.poll() is not None:
                    break
                playlist = new.root / "output" / "index.m3u8"
                if playlist.exists() and len(list(new.root.joinpath("output").glob("segment_*.ts"))) >= 2:
                    ready = True
                    break
                time.sleep(0.25)
            if not ready:
                event(old.root / "events.jsonl", "auto_recovery_failed", reason=reason, replacement=new.session_id)
                self.stop(new.session_id, reason="recovery_startup_failed")
                old.recovery_scheduled = False
                return
            with self.lock:
                if self.active_relays.get((old.stream_id, old.profile)) != old.session_id:
                    self.stop(new.session_id, reason="recovery_superseded")
                    old.recovery_scheduled = False
                    return
                new.published = True
                new.last_client_access = time.time()
                self.active_relays[(old.stream_id, old.profile)] = new.session_id
                old.published = False
                self.recovery_history.setdefault(old.stream_id, []).append(time.time())
            event(
                old.root / "events.jsonl",
                "auto_recovery_switch",
                reason=reason,
                replacement=new.session_id,
                replacement_generation=new.recovery_generation,
            )
            event(
                new.root / "events.jsonl",
                "recovery_promoted",
                reason=reason,
                previous=old.session_id,
                generation=new.recovery_generation,
            )
            self.stop(old.session_id, reason=f"auto_recovery:{reason}")
        except Exception as exc:
            event(old.root / "events.jsonl", "auto_recovery_error", reason=reason, error=str(exc))
            old.recovery_scheduled = False
            if new:
                self.stop(new.session_id, reason="recovery_error")

    def _monitor(self, rt: Runtime, stream: dict[str, Any]) -> None:
        settings = self.config.settings()
        poll = float(settings.get("manifest_poll_seconds", 1))
        freeze = float(settings.get("freeze_seconds", 8))
        last: dict[str, str] = {}
        counts: dict[str, int] = {}
        last_out: str | None = None
        n_out = 0
        headers = {"User-Agent": str(stream.get("user_agent") or "")}
        tick = 0
        selected_url = rt.resolved_input_url or str(stream["input_url"])
        original_url = str(stream["input_url"])
        with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=True, headers=headers) as client:
            while not rt.monitor_stop.wait(poll):
                tick += 1
                if rt.proc and rt.proc.poll() is not None:
                    break
                progress_age = time.time() - rt.last_progress
                if progress_age > freeze and rt.out_time_ms > 0 and not rt.freeze_logged:
                    event(
                        rt.root / "events.jsonl",
                        "progress_freeze",
                        seconds=round(progress_age, 2),
                        frame=rt.frame,
                        out_time_ms=rt.out_time_ms,
                    )
                    rt.freeze_logged = True
                stall_limit = max(10.0, float(settings.get("recovery_stall_seconds", 20)))
                if (
                    progress_age >= stall_limit
                    and rt.out_time_ms > 0
                    and rt.managed_relay
                    and rt.published
                    and bool(settings.get("auto_recovery_enabled", True))
                ):
                    self._schedule_recovery(rt, f"progress_stall:{progress_age:.1f}s")

                # v0.2.3 intentionally monitors the entry master plus only the pinned
                # media rendition. This is cheaper and makes SSAI diagnostics match the
                # exact rendition FFmpeg is consuming.
                urls: list[tuple[str, str]] = [(selected_url, "selected")]
                if original_url != selected_url and (tick == 1 or tick % 10 == 0):
                    urls.append((original_url, "master"))
                seen: set[str] = set()
                for url, kind in urls:
                    if url in seen:
                        continue
                    seen.add(url)
                    try:
                        response = client.get(url)
                        response.raise_for_status()
                        text = response.text
                        final_url = str(response.url)
                        changed = self._snapshot_manifest(
                            rt,
                            kind=kind,
                            url=final_url,
                            text=text,
                            response=response,
                            last=last,
                            counts=counts,
                        )
                        if kind == "selected" and changed and "#EXT-X-STREAM-INF" not in text:
                            self._handle_selected_manifest(rt, final_url, text, headers)
                    except Exception as exc:
                        event(rt.root / "events.jsonl", f"{kind}_manifest_error", manifest_url=url, error=str(exc))

                op = rt.root / "output" / "index.m3u8"
                if op.exists():
                    try:
                        text = op.read_text(errors="replace")
                        if text != last_out:
                            n_out += 1
                            filename = f"output-{n_out:05d}.m3u8"
                            (rt.root / "manifests" / filename).write_text(text, encoding="utf-8")
                            event(rt.root / "events.jsonl", "output_manifest_change", snapshot=filename, **parse_manifest(text))
                            last_out = text
                    except Exception as exc:
                        event(rt.root / "events.jsonl", "output_manifest_error", error=str(exc))
                self._scan_ffmpeg_log(rt)
                if rt.profile in HLS_PROFILES:
                    self._sample_av_sync(rt)

    def stop(self, session_id: str, *, reason: str = "manual") -> None:
        rt = self.runtimes.get(session_id)
        if not rt:
            return
        rt.monitor_stop.set()
        with self.lock:
            key = (rt.stream_id, rt.profile)
            if self.active_relays.get(key) == session_id:
                self.active_relays.pop(key, None)
            rt.published = False
        p = rt.proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(4)
            except Exception:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
        event(rt.root / "events.jsonl", "session_stop", reason=reason)

    def latest_hls(self, sid: str, profile: str) -> Runtime:
        with self.lock:
            key = (sid, profile)
            rt = self.runtimes.get(self.active_relays.get(key, ""))
            if not rt or not rt.proc or rt.proc.poll() is not None:
                rt = self.start(sid, profile, managed_relay=True, publish_managed=True)
            rt.last_client_access = time.time()
            return rt

    def touch_client(self, sid: str, profile: str) -> None:
        with self.lock:
            rt = self.runtimes.get(self.active_relays.get((sid, profile), ""))
            if rt:
                rt.last_client_access = time.time()

    def render_playlist(self, rt: Runtime) -> str:
        path = rt.root / "output" / "index.m3u8"
        text = path.read_text(errors="replace")
        match = MEDIA_SEQ.search(text)
        local_seq = int(match.group(1)) if match else 0
        if match:
            text = MEDIA_SEQ.sub(f"#EXT-X-MEDIA-SEQUENCE:{rt.sequence_base + local_seq}", text, count=1)
        else:
            lines = text.splitlines()
            insert_at = 1 if lines and lines[0].startswith("#EXTM3U") else 0
            lines.insert(insert_at, f"#EXT-X-MEDIA-SEQUENCE:{rt.sequence_base}")
            text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        text = re.sub(r"^#EXT-X-DISCONTINUITY-SEQUENCE:\d+\n?", "", text, flags=re.M)
        lines = text.splitlines()
        media_index = next((i for i, line in enumerate(lines) if line.startswith("#EXT-X-MEDIA-SEQUENCE:")), 0)
        lines.insert(media_index + 1, f"#EXT-X-DISCONTINUITY-SEQUENCE:{rt.recovery_generation}")
        if rt.recovery_generation > 0 and local_seq == 0:
            first_uri = next((i for i, line in enumerate(lines) if line and not line.startswith("#")), None)
            if first_uri is not None:
                lines.insert(first_uri, "#EXT-X-DISCONTINUITY")
        rewritten: list[str] = []
        for line in lines:
            if line and not line.startswith("#"):
                filename = Path(line).name
                rewritten.append(f"segments/{rt.session_id}/{filename}")
            else:
                rewritten.append(line)
        return "\n".join(rewritten) + "\n"

    def _reaper(self) -> None:
        while True:
            time.sleep(5)
            timeout = max(10.0, float(self.config.settings().get("hls_idle_timeout_seconds", 30)))
            now = time.time()
            for rt in list(self.runtimes.values()):
                if not rt.managed_relay or not rt.proc or rt.proc.poll() is not None or not rt.published:
                    continue
                if now - rt.last_client_access > timeout:
                    self.stop(rt.session_id, reason="hls_idle_timeout")

    def all(self) -> list[dict[str, Any]]:
        return sorted([r.status() for r in self.runtimes.values()], key=lambda x: x["session_id"], reverse=True)

    def bundle(self, session_id: str) -> Path:
        rt = self.runtimes.get(session_id)
        root = rt.root if rt else SESSIONS / session_id
        if not root.exists():
            raise FileNotFoundError(session_id)
        dest = SESSIONS / f"{session_id}.zip"
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for p in root.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(root))
        return dest
