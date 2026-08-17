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

SESSIONS = DATA_DIR / "sessions"
SESSIONS.mkdir(parents=True, exist_ok=True)
MEDIA_SEQ = re.compile(r"^#EXT-X-MEDIA-SEQUENCE:(\d+)", re.M)
URI_ATTR = re.compile(r'URI="([^"]+)"')
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
    # De-duplicate without scrambling the playlist's ordering.
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
    last_client_access: float = field(default_factory=time.time)

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
            "client_age": round(time.time() - self.last_client_access, 2),
        }


class SessionManager:
    def __init__(self, config: ConfigStore):
        self.config = config
        self.lock = threading.RLock()
        self.runtimes: dict[str, Runtime] = {}
        threading.Thread(target=self._reaper, daemon=True, name="hls-relay-reaper").start()

    def _prepare(self, sid: str, profile: str, stream: dict, cmd: list[str]) -> Runtime:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        session_id = f"{sid}-{profile}-{stamp}"
        # Avoid clobbering if two viewers connect during the same second.
        base_id = session_id
        suffix = 2
        while (SESSIONS / session_id).exists():
            session_id = f"{base_id}-{suffix}"
            suffix += 1
        root = SESSIONS / session_id
        (root / "output").mkdir(parents=True, exist_ok=True)
        (root / "manifests").mkdir(exist_ok=True)
        (root / "command.txt").write_text(shell_join(cmd) + "\n", encoding="utf-8")
        metadata = dict(stream)
        metadata["configured_play_profile"] = stream.get("play_profile")
        metadata["session_profile"] = profile
        metadata["session_id"] = session_id
        metadata["session_started_utc"] = utc()
        (root / "stream.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        rt = Runtime(session_id, sid, profile, root)
        self.runtimes[session_id] = rt
        event(root / "events.jsonl", "session_start", profile=profile, command=shell_join(cmd))
        return rt

    def start(self, sid: str, profile: str, *, managed_relay: bool = False) -> Runtime:
        stream = self.config.stream(sid)
        if not stream:
            raise KeyError(sid)
        # HLS output paths are session-specific, so allocate the session root before
        # building the FFmpeg command.
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
        cmd = build(profile, stream, self.config.settings(), root / "output")
        (root / "command.txt").write_text(shell_join(cmd) + "\n", encoding="utf-8")
        metadata = dict(stream)
        metadata["configured_play_profile"] = stream.get("play_profile")
        metadata["session_profile"] = profile
        metadata["session_id"] = session_id
        metadata["session_started_utc"] = utc()
        (root / "stream.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        log = (root / "ffmpeg.log").open("wb", buffering=0)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        rt = Runtime(session_id, sid, profile, root, proc, managed_relay=managed_relay)
        self.runtimes[session_id] = rt
        event(root / "events.jsonl", "session_start", profile=profile, command=shell_join(cmd))
        threading.Thread(target=self._progress, args=(rt,), daemon=True).start()
        threading.Thread(target=self._monitor, args=(rt, stream), daemon=True).start()
        return rt

    def start_ts_relay(self, sid: str, *, permissive: bool = False) -> Runtime:
        stream = self.config.stream(sid)
        if not stream:
            raise KeyError(sid)
        profile = "continuous-ts-permissive" if permissive else "continuous-ts"
        cmd = build_ts_relay(stream, self.config.settings(), permissive=permissive)
        rt = self._prepare(sid, profile, stream, cmd)
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
            # FFmpeg documents out_time_us in microseconds. Some builds also expose
            # out_time_ms with legacy naming; prefer out_time_us for consistent units.
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

    def _monitor(self, rt: Runtime, stream: dict) -> None:
        settings = self.config.settings()
        poll = float(settings.get("manifest_poll_seconds", 1))
        freeze = float(settings.get("freeze_seconds", 8))
        last: dict[str, str] = {}
        counts: dict[str, int] = {}
        last_out: str | None = None
        n_out = 0
        headers = {"User-Agent": stream.get("user_agent", "")}
        with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=True, headers=headers) as client:
            while not rt.monitor_stop.wait(poll):
                if rt.proc and rt.proc.poll() is not None:
                    break
                if time.time() - rt.last_progress > freeze and rt.out_time_ms > 0 and not rt.freeze_logged:
                    event(
                        rt.root / "events.jsonl",
                        "progress_freeze",
                        seconds=round(time.time() - rt.last_progress, 2),
                        frame=rt.frame,
                        out_time_ms=rt.out_time_ms,
                    )
                    rt.freeze_logged = True

                # Capture the entry manifest and walk master -> child playlists up to two
                # levels deep. This is where Samsung SSAI discontinuities and extensionless
                # media URLs actually appear.
                queue: list[tuple[str, int, str]] = [(stream["input_url"], 0, "input")]
                seen: set[str] = set()
                child_count = 0
                while queue and child_count < 12:
                    url, depth, kind = queue.pop(0)
                    if url in seen:
                        continue
                    seen.add(url)
                    try:
                        r = client.get(url)
                        txt = r.text
                        final_url = str(r.url)
                        self._snapshot_manifest(
                            rt,
                            kind=kind,
                            url=final_url,
                            text=txt,
                            response=r,
                            last=last,
                            counts=counts,
                        )
                        if depth < 2:
                            for child_url in child_playlist_urls(txt, final_url):
                                queue.append((child_url, depth + 1, "variant"))
                                child_count += 1
                    except Exception as exc:
                        event(rt.root / "events.jsonl", f"{kind}_manifest_error", manifest_url=url, error=str(exc))

                op = rt.root / "output" / "index.m3u8"
                if op.exists():
                    try:
                        txt = op.read_text(errors="replace")
                        if txt != last_out:
                            n_out += 1
                            filename = f"output-{n_out:05d}.m3u8"
                            (rt.root / "manifests" / filename).write_text(txt, encoding="utf-8")
                            event(
                                rt.root / "events.jsonl",
                                "output_manifest_change",
                                snapshot=filename,
                                **parse_manifest(txt),
                            )
                            last_out = txt
                    except Exception as exc:
                        event(rt.root / "events.jsonl", "output_manifest_error", error=str(exc))

    def stop(self, session_id: str, *, reason: str = "manual") -> None:
        rt = self.runtimes.get(session_id)
        if not rt:
            return
        rt.monitor_stop.set()
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
            candidates = [
                r for r in self.runtimes.values()
                if r.stream_id == sid and r.profile == profile and r.proc and r.proc.poll() is None and r.managed_relay
            ]
            rt = max(candidates, key=lambda x: x.started) if candidates else self.start(sid, profile, managed_relay=True)
            rt.last_client_access = time.time()
            return rt

    def touch_client(self, sid: str, profile: str) -> None:
        with self.lock:
            candidates = [
                r for r in self.runtimes.values()
                if r.stream_id == sid and r.profile == profile and r.proc and r.proc.poll() is None and r.managed_relay
            ]
            if candidates:
                max(candidates, key=lambda x: x.started).last_client_access = time.time()

    def _reaper(self) -> None:
        while True:
            time.sleep(5)
            timeout = max(10.0, float(self.config.settings().get("hls_idle_timeout_seconds", 30)))
            now = time.time()
            for rt in list(self.runtimes.values()):
                if not rt.managed_relay or not rt.proc or rt.proc.poll() is not None:
                    continue
                if now - rt.last_client_access > timeout:
                    self.stop(rt.session_id, reason="hls_idle_timeout")

    def all(self) -> list[dict[str, Any]]:
        return sorted(
            [r.status() for r in self.runtimes.values()],
            key=lambda x: x["session_id"],
            reverse=True,
        )

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
