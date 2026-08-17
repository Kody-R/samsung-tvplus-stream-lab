from __future__ import annotations

import copy
import gzip
import hashlib
import json
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _m3u_value(value: Any) -> str:
    return str(value or "").replace('"', "'").replace("\r", " ").replace("\n", " ").strip()


def output_tvg_id(stream: dict[str, Any]) -> str:
    return str(stream.get("tvg_id") or stream.get("id") or "").strip()


def source_xmltv_id(stream: dict[str, Any]) -> str:
    return str(stream.get("xmltv_channel_id") or output_tvg_id(stream)).strip()


def build_m3u(streams: list[dict[str, Any]], base_url: str) -> str:
    base = base_url.rstrip("/")
    lines = ["#EXTM3U"]
    for stream in streams:
        if not stream.get("enabled", True):
            continue
        sid = str(stream.get("id") or "").strip()
        if not sid:
            continue
        name = _m3u_value(stream.get("name") or sid)
        attrs = [
            f'tvg-id="{_m3u_value(output_tvg_id(stream))}"',
            f'tvg-name="{name}"',
        ]
        logo = _m3u_value(stream.get("tvg_logo"))
        group = _m3u_value(stream.get("group_title") or "Stream Lab")
        channel_number = _m3u_value(stream.get("channel_number"))
        if logo:
            attrs.append(f'tvg-logo="{logo}"')
        if group:
            attrs.append(f'group-title="{group}"')
        if channel_number:
            attrs.append(f'tvg-chno="{channel_number}"')
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{name}")
        lines.append(f"{base}/stream/{quote(sid, safe='')}/index.m3u8")
    return "\n".join(lines) + "\n"


def _decode_xmltv(payload: bytes) -> bytes:
    if payload.startswith(b"\x1f\x8b"):
        return gzip.decompress(payload)
    return payload


def _parse_source(payload: bytes | str) -> tuple[dict[str, ET.Element], dict[str, list[ET.Element]]]:
    raw = payload.encode("utf-8") if isinstance(payload, str) else _decode_xmltv(payload)
    root = ET.fromstring(raw)
    channels: dict[str, ET.Element] = {}
    programmes: dict[str, list[ET.Element]] = {}
    for elem in root:
        if elem.tag == "channel":
            cid = str(elem.attrib.get("id") or "")
            if cid:
                channels[cid] = elem
        elif elem.tag == "programme":
            cid = str(elem.attrib.get("channel") or "")
            if cid:
                programmes.setdefault(cid, []).append(elem)
    return channels, programmes


def build_xmltv(
    streams: list[dict[str, Any]],
    source_documents: dict[str, bytes | str],
) -> tuple[bytes, dict[str, Any]]:
    root = ET.Element(
        "tv",
        {
            "generator-info-name": "Samsung TV Plus Stream Lab",
            "generator-info-url": "https://github.com/kody-r/samsung-tvplus-stream-lab",
        },
    )
    parsed: dict[str, tuple[dict[str, ET.Element], dict[str, list[ET.Element]]]] = {}
    errors: list[str] = []
    for url, payload in source_documents.items():
        try:
            parsed[url] = _parse_source(payload)
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    seen_ids: set[str] = set()
    programme_count = 0
    source_match_count = 0

    for stream in streams:
        if not stream.get("enabled", True):
            continue
        tvg_id = output_tvg_id(stream)
        if not tvg_id or tvg_id in seen_ids:
            continue
        seen_ids.add(tvg_id)
        name = str(stream.get("name") or stream.get("id") or tvg_id)
        logo = str(stream.get("tvg_logo") or "").strip()
        source_url = str(stream.get("xmltv_url") or "").strip()
        source_id = source_xmltv_id(stream)

        # Always create our own channel record. This keeps the generated XMLTV aligned
        # with the generated M3U even if an upstream guide is missing or temporarily down.
        channel = ET.SubElement(root, "channel", {"id": tvg_id})
        display = ET.SubElement(channel, "display-name")
        display.text = name
        if logo:
            ET.SubElement(channel, "icon", {"src": logo})

        source = parsed.get(source_url) if source_url else None
        if not source:
            continue
        _, programmes = source
        matches = programmes.get(source_id, [])
        if matches:
            source_match_count += 1
        for item in matches:
            clone = copy.deepcopy(item)
            clone.set("channel", tvg_id)
            root.append(clone)
            programme_count += 1

    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    stats = {
        "channels": len(seen_ids),
        "programmes": programme_count,
        "streams_with_programmes": source_match_count,
        "errors": errors,
    }
    return payload, stats


@dataclass
class GuideCache:
    payload: bytes | None = None
    signature: str = ""
    generated_at: str | None = None
    expires_at: float = 0.0
    stats: dict[str, Any] | None = None


class GuideService:
    def __init__(self, config):
        self.config = config
        self.lock = threading.RLock()
        self.cache = GuideCache(stats={"channels": 0, "programmes": 0, "streams_with_programmes": 0, "errors": []})

    def invalidate(self) -> None:
        with self.lock:
            self.cache.expires_at = 0.0
            self.cache.signature = ""

    def _signature(self, streams: list[dict[str, Any]]) -> str:
        relevant = [
            {
                key: stream.get(key)
                for key in (
                    "id", "name", "enabled", "tvg_id", "tvg_logo", "xmltv_url", "xmltv_channel_id"
                )
            }
            for stream in streams
        ]
        blob = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _fetch_sources(self, streams: list[dict[str, Any]]) -> tuple[dict[str, bytes], list[str]]:
        by_url: dict[str, str] = {}
        for stream in streams:
            if not stream.get("enabled", True):
                continue
            url = str(stream.get("xmltv_url") or "").strip()
            if url and url not in by_url:
                by_url[url] = str(stream.get("user_agent") or "")

        docs: dict[str, bytes] = {}
        errors: list[str] = []
        timeout_seconds = float(self.config.settings().get("guide_fetch_timeout_seconds", 30))
        for url, ua in by_url.items():
            headers = {"User-Agent": ua} if ua else {}
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds)),
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    docs[url] = response.content
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        return docs, errors

    def xml(self, *, force: bool = False) -> bytes:
        streams = self.config.streams()
        signature = self._signature(streams)
        now = time.time()
        with self.lock:
            if (
                not force
                and self.cache.payload is not None
                and self.cache.signature == signature
                and now < self.cache.expires_at
            ):
                return self.cache.payload

        docs, fetch_errors = self._fetch_sources(streams)
        payload, stats = build_xmltv(streams, docs)
        stats["errors"] = fetch_errors + list(stats.get("errors") or [])
        ttl = max(30, int(self.config.settings().get("guide_cache_seconds", 900)))
        with self.lock:
            self.cache = GuideCache(
                payload=payload,
                signature=signature,
                generated_at=utc(),
                expires_at=time.time() + ttl,
                stats=stats,
            )
        return payload

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "generated_at": self.cache.generated_at,
                "cache_seconds_remaining": max(0, round(self.cache.expires_at - time.time())),
                **(self.cache.stats or {}),
            }
