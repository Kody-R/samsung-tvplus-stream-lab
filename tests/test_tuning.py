import json
from pathlib import Path
from types import SimpleNamespace

import app.config as config_mod
from app.session import Runtime, SessionManager


def test_legacy_default_migrates_to_60_seconds(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "streams.json"
    path.write_text(json.dumps({"settings": {"dts_delta_threshold": 1.0}, "sources": [], "streams": []}))
    monkeypatch.setattr(config_mod, "DATA_DIR", data)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
    store = config_mod.ConfigStore()
    assert store.settings()["dts_delta_threshold"] == 60.0
    assert store.settings()["dts_delta_threshold_user_set"] is False


def test_custom_tuning_persists(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "streams.json"
    monkeypatch.setattr(config_mod, "DATA_DIR", data)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
    store = config_mod.ConfigStore()
    settings = store.update_settings(
        dts_delta_threshold=90.5,
        av_sync_probe_seconds=20,
        av_sync_warn_seconds=0.75,
        audio_sync_bitrate_kbps=192,
        hls_idle_timeout_seconds=45,
    )
    assert settings["dts_delta_threshold"] == 90.5
    assert settings["dts_delta_threshold_user_set"] is True
    reloaded = config_mod.ConfigStore()
    assert reloaded.settings()["dts_delta_threshold"] == 90.5
    assert reloaded.settings()["av_sync_probe_seconds"] == 20


def test_av_sync_sampler_records_offset(tmp_path: Path, monkeypatch):
    class FakeConfig:
        def settings(self):
            return {
                "ffprobe_path": "ffprobe",
                "av_sync_probe_seconds": 5,
                "av_sync_warn_seconds": 1.0,
                "hls_idle_timeout_seconds": 30,
            }

    root = tmp_path / "session"
    output = root / "output"
    output.mkdir(parents=True)
    (output / "segment_000001.ts").write_bytes(b"fake")

    payload = {
        "streams": [
            {"codec_type": "video", "start_time": "10.000"},
            {"codec_type": "audio", "start_time": "10.125"},
        ]
    }
    monkeypatch.setattr(
        "app.session.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )

    manager = SessionManager(FakeConfig())
    rt = Runtime("sample", "1", "normalize-hls-sync-permissive", root)
    manager._sample_av_sync(rt)
    assert rt.av_sync_samples == 1
    assert rt.av_sync_status == "healthy"
    assert rt.av_sync_offset_seconds == 0.125
    assert rt.av_sync_max_abs_seconds == 0.125
