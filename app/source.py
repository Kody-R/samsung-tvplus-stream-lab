from __future__ import annotations

import gzip
import hashlib
import io
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx

from .config import DEFAULT_UA

ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_key(item: dict[str, Any]) -> str:
    tvg = str(item.get("tvg_id") or "").strip()
    if tvg:
        return f"tvg:{tvg}"
    name = str(item.get("name") or "").strip().casefold()
    group = str(item.get("group_title") or "").strip().casefold()
    return f"name:{group}|{name}"


def _internal_id(source_id: str, source_key: str) -> str:
    digest = hashlib.sha1(source_key.encode("utf-8", errors="replace")).hexdigest()[:12]
    safe_source = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_id).strip("-") or "source"
    return f"{safe_source}-{digest}"


def parse_m3u(text: str, base_url: str = "") -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    channels: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    extgrp = ""
    for line in lines:
        if not line:
            continue
        if line.startswith("#EXTGRP:"):
            extgrp = line.split(":", 1)[1].strip()
            if pending is not None and not pending.get("group_title"):
                pending["group_title"] = extgrp
            continue
        if line.startswith("#EXTINF:"):
            attrs = {k.lower(): v for k, v in ATTR_RE.findall(line)}
            name = line.split(",", 1)[1].strip() if "," in line else attrs.get("tvg-name", "")
            pending = {
                "name": name or attrs.get("tvg-name", "Unnamed Channel"),
                "tvg_id": attrs.get("tvg-id", ""),
                "tvg_logo": attrs.get("tvg-logo", ""),
                "group_title": attrs.get("group-title", "") or extgrp,
                "channel_number": attrs.get("tvg-chno", "") or attrs.get("channel-number", "") or attrs.get("ch-number", ""),
            }
            extgrp = ""
            continue
        if line.startswith("#"):
            continue
        if pending is not None:
            pending["input_url"] = urljoin(base_url, line) if base_url else line
            pending["source_key"] = _stable_key(pending)
            channels.append(pending)
            pending = None
    return channels


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def xmltv_channel_index(payload: bytes) -> tuple[set[str], dict[str, list[str]]]:
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    ids: set[str] = set()
    names: dict[str, list[str]] = {}
    try:
        for _event, elem in ET.iterparse(io.BytesIO(payload), events=("end",)):
            if elem.tag == "channel":
                cid = str(elem.attrib.get("id") or "").strip()
                if cid:
                    ids.add(cid)
                    for display in elem.findall("display-name"):
                        norm = _norm_name(str(display.text or ""))
                        if norm:
                            names.setdefault(norm, []).append(cid)
                elem.clear()
            elif elem.tag == "programme" and ids:
                # XMLTV normally declares all channels before programme records.
                break
    except ET.ParseError:
        pass
    return ids, names


def xmltv_channel_ids(payload: bytes) -> set[str]:
    return xmltv_channel_index(payload)[0]



class SourceService:
    def __init__(self, config, guide=None):
        self.config = config
        self.guide = guide
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._locks: dict[str, threading.Lock] = {}

    def start_scheduler(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="source-refresh")
        self._thread.start()

    def stop_scheduler(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(float(self.config.settings().get("source_refresh_poll_seconds", 60))):
            now = time.time()
            for source in self.config.sources():
                if not source.get("enabled", True):
                    continue
                refresh_seconds = max(3600.0, float(source.get("refresh_hours") or 6) * 3600.0)
                last = float(source.get("last_refresh_epoch") or 0)
                if now - last >= refresh_seconds:
                    try:
                        self.refresh(source["id"])
                    except Exception:
                        pass

    def _fetch(self, url: str, user_agent: str, timeout: float = 45.0) -> bytes:
        headers = {"User-Agent": user_agent or DEFAULT_UA}
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)), follow_redirects=True, headers=headers) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content

    def refresh(self, source_id: str) -> dict[str, Any]:
        source = self.config.source(source_id)
        if not source:
            raise KeyError(source_id)
        lock = self._locks.setdefault(str(source_id), threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError("Source refresh already running")
        try:
            ua = str(source.get("user_agent") or DEFAULT_UA)
            m3u_bytes = self._fetch(str(source["m3u_url"]), ua)
            text = m3u_bytes.decode("utf-8-sig", errors="replace")
            parsed = parse_m3u(text, str(source["m3u_url"]))

            epg_ids: set[str] = set()
            epg_names: dict[str, list[str]] = {}
            xmltv_error = ""
            xmltv_url = str(source.get("xmltv_url") or "").strip()
            if xmltv_url:
                try:
                    epg_ids, epg_names = xmltv_channel_index(self._fetch(xmltv_url, ua, 60.0))
                except Exception as exc:
                    xmltv_error = str(exc)

            existing = {
                str(x.get("source_key")): x
                for x in self.config.streams()
                if str(x.get("source_id") or "") == str(source_id)
            }
            now_utc = utc()
            default_profile = str(source.get("default_profile") or "normalize-hls-permissive")
            imported: list[dict[str, Any]] = []
            seen_keys: set[str] = set()
            new_count = 0
            changed_urls = 0
            epg_matches = 0
            for ch in parsed:
                key = str(ch["source_key"])
                seen_keys.add(key)
                old = existing.get(key)
                if old is None:
                    new_count += 1
                elif str(old.get("input_url") or "") != str(ch.get("input_url") or ""):
                    changed_urls += 1
                tvg_id = str(ch.get("tvg_id") or "").strip()
                matched_xmltv_id = tvg_id if tvg_id and tvg_id in epg_ids else ""
                if not matched_xmltv_id:
                    candidates = epg_names.get(_norm_name(str(ch.get("name") or "")), [])
                    if len(candidates) == 1:
                        matched_xmltv_id = candidates[0]
                epg_match = bool(matched_xmltv_id)
                if epg_match:
                    epg_matches += 1
                imported.append({
                    "id": str(old.get("id")) if old else _internal_id(str(source_id), key),
                    "name": str(ch.get("name") or tvg_id or "Unnamed Channel"),
                    "input_url": str(ch.get("input_url") or ""),
                    "user_agent": ua,
                    "play_profile": str(old.get("play_profile")) if old else default_profile,
                    "selected": bool(old.get("selected", False)) if old else False,
                    "enabled": bool(old.get("selected", False)) if old else False,
                    "tvg_id": tvg_id or (str(old.get("tvg_id")) if old else ""),
                    "tvg_logo": str(ch.get("tvg_logo") or ""),
                    "group_title": str(ch.get("group_title") or source.get("name") or "Imported"),
                    "channel_number": str(ch.get("channel_number") or ""),
                    "xmltv_url": xmltv_url,
                    "xmltv_channel_id": matched_xmltv_id or tvg_id,
                    "source_id": str(source_id),
                    "source_key": key,
                    "missing": False,
                    "upstream_seen": True,
                    "epg_matched": epg_match,
                    "imported": True,
                    "last_seen_utc": now_utc,
                })

            removed_count = sum(1 for key in existing if key not in seen_keys)
            self.config.replace_source_streams(str(source_id), imported, seen_keys)
            stats = {
                "channels": len(parsed),
                "new": new_count,
                "removed": removed_count,
                "url_changes": changed_urls,
                "epg_channels": len(epg_ids),
                "epg_matches": epg_matches,
                "xmltv_error": xmltv_error,
            }
            self.config.update_source_runtime(
                str(source_id),
                last_refresh_utc=now_utc,
                last_refresh_epoch=time.time(),
                last_error="",
                stats=stats,
            )
            if self.guide:
                self.guide.invalidate()
            return stats
        except Exception as exc:
            self.config.update_source_runtime(str(source_id), last_error=str(exc), last_refresh_epoch=time.time())
            raise
        finally:
            lock.release()
