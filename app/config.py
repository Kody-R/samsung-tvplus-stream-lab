from __future__ import annotations
import json, os, threading
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
    },
    "streams": []
}

class ConfigStore:
    def __init__(self):
        self.lock=threading.RLock(); DATA_DIR.mkdir(parents=True, exist_ok=True); self.data=self._load()
    def _load(self):
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2)+"\n")
            return json.loads(json.dumps(DEFAULTS))
        try: data=json.loads(CONFIG_PATH.read_text())
        except Exception: data=json.loads(json.dumps(DEFAULTS))
        data.setdefault("settings",{}); data.setdefault("streams",[])
        merged=dict(DEFAULTS["settings"]); merged.update(data["settings"]); data["settings"]=merged
        return data
    def save(self):
        with self.lock:
            tmp=CONFIG_PATH.with_suffix('.json.tmp'); tmp.write_text(json.dumps(self.data,indent=2)+"\n"); os.replace(tmp,CONFIG_PATH)
    def settings(self): return dict(self.data["settings"])
    def streams(self): return [dict(x) for x in self.data["streams"]]
    def stream(self, sid: str):
        return next((dict(x) for x in self.data["streams"] if str(x.get("id"))==str(sid)), None)
    def upsert(self, item: dict[str,Any]):
        with self.lock:
            sid=str(item["id"]).strip()
            if not sid: raise ValueError("Stream ID is required")
            clean={
                "id":sid,
                "name":str(item.get("name") or sid),
                "input_url":str(item.get("input_url") or "").strip(),
                "user_agent":str(item.get("user_agent") or DEFAULT_UA),
                "play_profile":str(item.get("play_profile") or "normalize-hls"),
                "enabled":bool(item.get("enabled",True)),
            }
            if not clean["input_url"]: raise ValueError("Input URL is required")
            for i,old in enumerate(self.data["streams"]):
                if str(old.get("id"))==sid: self.data["streams"][i]=clean; self.save(); return clean
            self.data["streams"].append(clean); self.save(); return clean
    def delete(self,sid:str):
        with self.lock:
            self.data["streams"]=[x for x in self.data["streams"] if str(x.get("id"))!=str(sid)]; self.save()
