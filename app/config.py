from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("STREAM_LAB_DATA_DIR", "/app/data"))
CONFIG_PATH = DATA_DIR / "streams.json"

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
DEFAULTS = {
    "settings": {
        "ffmpeg_path": "ffmpeg",
        "ffprobe_path": "ffprobe",
        "manifest_poll_seconds": 1.0,
        "freeze_seconds": 8.0,
        "dts_delta_threshold": 1.0,
        "hls_time": 3,
        "hls_list_size": 12,
        "hls_delete_threshold": 4,
        "render_device": "/dev/dri/renderD129",
        "guide_cache_seconds": 900,
        "guide_fetch_timeout_seconds": 30,
        "source_refresh_poll_seconds": 60,
        "hls_idle_timeout_seconds": 30,
    },
    "sources": [],
    "streams": [],
}


class ConfigStore:
    def __init__(self):
        self.lock = threading.RLock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
            return copy.deepcopy(DEFAULTS)
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = copy.deepcopy(DEFAULTS)
        data.setdefault("settings", {})
        data.setdefault("sources", [])
        data.setdefault("streams", [])
        merged = dict(DEFAULTS["settings"])
        merged.update(data["settings"])
        data["settings"] = merged

        # Migration from v0.1.x: manual streams were enabled/disabled rather than selected.
        for stream in data["streams"]:
            stream.setdefault("selected", bool(stream.get("enabled", True)))
            stream.setdefault("enabled", bool(stream.get("selected", True)))
            stream.setdefault("source_id", "")
            stream.setdefault("source_key", str(stream.get("tvg_id") or stream.get("id") or ""))
            stream.setdefault("missing", False)
            stream.setdefault("epg_matched", bool(stream.get("xmltv_url")))
            stream.setdefault("imported", False)
        return data

    def save(self) -> None:
        with self.lock:
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)

    def settings(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.data["settings"])

    def streams(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(x) for x in self.data["streams"]]

    def stream(self, sid: str) -> dict[str, Any] | None:
        with self.lock:
            found = next((x for x in self.data["streams"] if str(x.get("id")) == str(sid)), None)
            return dict(found) if found else None

    def sources(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(x) for x in self.data["sources"]]

    def source(self, source_id: str) -> dict[str, Any] | None:
        with self.lock:
            found = next((x for x in self.data["sources"] if str(x.get("id")) == str(source_id)), None)
            return dict(found) if found else None

    def upsert_source(self, item: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            source_id = str(item.get("id") or "").strip()
            if not source_id:
                raise ValueError("Source ID is required")
            m3u_url = str(item.get("m3u_url") or "").strip()
            if not m3u_url:
                raise ValueError("M3U URL is required")
            old = next((x for x in self.data["sources"] if str(x.get("id")) == source_id), {})
            clean = {
                "id": source_id,
                "name": str(item.get("name") or old.get("name") or source_id).strip(),
                "m3u_url": m3u_url,
                "xmltv_url": str(item.get("xmltv_url") or "").strip(),
                "user_agent": str(item.get("user_agent") or old.get("user_agent") or DEFAULT_UA),
                "default_profile": str(item.get("default_profile") or old.get("default_profile") or "normalize-hls-permissive"),
                "refresh_hours": max(1.0, float(item.get("refresh_hours") or old.get("refresh_hours") or 6)),
                "enabled": bool(item.get("enabled", old.get("enabled", True))),
                "last_refresh_utc": old.get("last_refresh_utc"),
                "last_refresh_epoch": old.get("last_refresh_epoch", 0),
                "last_error": old.get("last_error", ""),
                "stats": old.get("stats", {}),
            }
            for i, existing in enumerate(self.data["sources"]):
                if str(existing.get("id")) == source_id:
                    self.data["sources"][i] = clean
                    self.save()
                    return dict(clean)
            self.data["sources"].append(clean)
            self.save()
            return dict(clean)

    def update_source_runtime(self, source_id: str, **fields: Any) -> None:
        with self.lock:
            for source in self.data["sources"]:
                if str(source.get("id")) == str(source_id):
                    source.update(fields)
                    self.save()
                    return

    def delete_source(self, source_id: str, *, delete_channels: bool = True) -> None:
        with self.lock:
            self.data["sources"] = [x for x in self.data["sources"] if str(x.get("id")) != str(source_id)]
            if delete_channels:
                self.data["streams"] = [x for x in self.data["streams"] if str(x.get("source_id") or "") != str(source_id)]
            self.save()

    def upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        """Create/update a manual or imported stream while retaining v0.1.x compatibility."""
        with self.lock:
            sid = str(item.get("id") or "").strip()
            if not sid:
                raise ValueError("Stream ID is required")
            old = next((x for x in self.data["streams"] if str(x.get("id")) == sid), {})
            selected = bool(item.get("selected", item.get("enabled", old.get("selected", True))))
            clean = dict(old)
            clean.update({
                "id": sid,
                "name": str(item.get("name") or old.get("name") or sid),
                "input_url": str(item.get("input_url") or old.get("input_url") or "").strip(),
                "user_agent": str(item.get("user_agent") or old.get("user_agent") or DEFAULT_UA),
                "play_profile": str(item.get("play_profile") or old.get("play_profile") or "normalize-hls-permissive"),
                "selected": selected,
                "enabled": selected,
                "tvg_id": str(item.get("tvg_id") or old.get("tvg_id") or sid).strip(),
                "tvg_logo": str(item.get("tvg_logo") or old.get("tvg_logo") or "").strip(),
                "group_title": str(item.get("group_title") or old.get("group_title") or "Stream Lab").strip(),
                "channel_number": str(item.get("channel_number") or old.get("channel_number") or "").strip(),
                "xmltv_url": str(item.get("xmltv_url") or old.get("xmltv_url") or "").strip(),
                "xmltv_channel_id": str(item.get("xmltv_channel_id") or old.get("xmltv_channel_id") or item.get("tvg_id") or old.get("tvg_id") or sid).strip(),
                "source_id": str(item.get("source_id") or old.get("source_id") or ""),
                "source_key": str(item.get("source_key") or old.get("source_key") or item.get("tvg_id") or sid),
                "missing": bool(item.get("missing", old.get("missing", False))),
                "epg_matched": bool(item.get("epg_matched", old.get("epg_matched", False))),
                "imported": bool(item.get("imported", old.get("imported", False))),
            })
            if not clean["input_url"]:
                raise ValueError("Input URL is required")
            for i, existing in enumerate(self.data["streams"]):
                if str(existing.get("id")) == sid:
                    self.data["streams"][i] = clean
                    self.save()
                    return dict(clean)
            self.data["streams"].append(clean)
            self.save()
            return dict(clean)

    def replace_source_streams(self, source_id: str, imported: list[dict[str, Any]], seen_keys: set[str]) -> None:
        """Apply an import while preserving user selection/profile choices for stable channel keys."""
        with self.lock:
            existing_by_key = {
                str(x.get("source_key")): x
                for x in self.data["streams"]
                if str(x.get("source_id") or "") == str(source_id)
            }
            merged: list[dict[str, Any]] = [
                x for x in self.data["streams"] if str(x.get("source_id") or "") != str(source_id)
            ]
            imported_ids: set[str] = set()
            for fresh in imported:
                key = str(fresh.get("source_key") or "")
                old = existing_by_key.get(key)
                if old:
                    keep = dict(old)
                    # Provider-owned metadata may refresh; user-owned selection/profile survive.
                    keep.update(fresh)
                    keep["selected"] = bool(old.get("selected", old.get("enabled", False)))
                    keep["enabled"] = keep["selected"]
                    keep["play_profile"] = str(old.get("play_profile") or fresh.get("play_profile") or "normalize-hls-permissive")
                    fresh = keep
                imported_ids.add(str(fresh.get("id")))
                merged.append(fresh)

            # Preserve missing selected/configured channels so a later provider refresh can revive them.
            for key, old in existing_by_key.items():
                if key in seen_keys:
                    continue
                missing = dict(old)
                missing["missing"] = True
                missing["upstream_seen"] = False
                merged.append(missing)
            self.data["streams"] = merged
            self.save()

    def set_selected(self, ids: list[str], selected: bool) -> int:
        wanted = {str(x) for x in ids}
        count = 0
        with self.lock:
            for stream in self.data["streams"]:
                if str(stream.get("id")) in wanted:
                    stream["selected"] = bool(selected)
                    stream["enabled"] = bool(selected)
                    count += 1
            if count:
                self.save()
        return count

    def update_stream_fields(self, sid: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"selected", "play_profile", "channel_number", "group_title", "tvg_logo", "tvg_id", "xmltv_channel_id"}
        with self.lock:
            for stream in self.data["streams"]:
                if str(stream.get("id")) == str(sid):
                    for key, value in fields.items():
                        if key not in allowed:
                            continue
                        if key == "selected":
                            stream["selected"] = bool(value)
                            stream["enabled"] = bool(value)
                        else:
                            stream[key] = str(value or "")
                    self.save()
                    return dict(stream)
        return None

    def delete(self, sid: str) -> None:
        with self.lock:
            self.data["streams"] = [x for x in self.data["streams"] if str(x.get("id")) != str(sid)]
            self.save()
