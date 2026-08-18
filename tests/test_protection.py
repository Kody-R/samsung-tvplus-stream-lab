from pathlib import Path

from app.hls import media_segments_and_boundaries, parse_master_variants, select_variant
from app.session import Runtime, SessionManager

MASTER = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:CODECS=\"avc1.4d0029,mp4a.40.2\",RESOLUTION=640x360,BANDWIDTH=921600
0.m3u8
#EXT-X-STREAM-INF:CODECS=\"avc1.4d0029,mp4a.40.2\",RESOLUTION=960x540,BANDWIDTH=2048000
1.m3u8
#EXT-X-STREAM-INF:CODECS=\"avc1.4d0029,mp4a.40.2\",RESOLUTION=1280x720,BANDWIDTH=3072000
2.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:100
#EXTINF:3,
seg100.ts
#EXTINF:3,
seg101.ts
#EXT-X-DISCONTINUITY
#EXT-X-ASSET:CAID=\"ad-1\"
#EXTINF:3,
https://cdn.example/ad/0/102
#EXTINF:3,
https://cdn.example/ad/0/103
"""


def test_master_variant_selection_pins_expected_quality():
    variants = parse_master_variants(MASTER, "https://cdn.example/live/master.m3u8")
    assert [v.height for v in variants] == [360, 540, 720]
    assert select_variant(variants, "auto").height == 720
    assert select_variant(variants, "720p").url.endswith("/2.m3u8")
    assert select_variant(variants, "540p").url.endswith("/1.m3u8")
    assert select_variant(variants, "360p").url.endswith("/0.m3u8")


def test_media_boundary_parser_identifies_ssai_transition_and_extensionless_segment():
    segments, boundaries = media_segments_and_boundaries(MEDIA, "https://cdn.example/live/2.m3u8")
    assert len(segments) == 4
    assert boundaries[0]["segment_url"] == "https://cdn.example/ad/0/102"
    assert boundaries[0]["segment_index"] == 2
    assert "ad-1" in boundaries[0]["asset"]


def test_recovery_playlist_uses_generation_specific_segments_and_monotonic_sequence(tmp_path: Path):
    class FakeConfig:
        def settings(self):
            return {"hls_idle_timeout_seconds": 30}

    root = tmp_path / "s"
    out = root / "output"
    out.mkdir(parents=True)
    (out / "index.m3u8").write_text(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:4\n#EXTINF:3,\nsegment_000004.ts\n#EXTINF:3,\nsegment_000005.ts\n"
    )
    manager = SessionManager(FakeConfig())
    rt = Runtime(
        "chan-normalize-hls-sync-permissive-test",
        "chan",
        "normalize-hls-sync-permissive",
        root,
        managed_relay=True,
        published=True,
        recovery_generation=2,
        sequence_base=100000,
    )
    text = manager.render_playlist(rt)
    assert "#EXT-X-MEDIA-SEQUENCE:100004" in text
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:2" in text
    assert "segments/chan-normalize-hls-sync-permissive-test/segment_000004.ts" in text


def test_warm_recovery_promotes_ready_generation(tmp_path: Path, monkeypatch):
    class FakeConfig:
        def settings(self):
            return {
                "hls_idle_timeout_seconds": 30,
                "auto_recovery_enabled": True,
                "recovery_startup_timeout_seconds": 2,
                "recovery_max_5min": 3,
            }

    class FakeProc:
        pid = 999999
        def poll(self):
            return None

    manager = SessionManager(FakeConfig())
    old_root = tmp_path / "old"
    (old_root / "output").mkdir(parents=True)
    old = Runtime(
        "old-session", "chan", "normalize-hls-sync-permissive", old_root,
        proc=FakeProc(), managed_relay=True, published=True,
    )
    manager.runtimes[old.session_id] = old
    manager.active_relays[(old.stream_id, old.profile)] = old.session_id

    new_root = tmp_path / "new"
    output = new_root / "output"
    output.mkdir(parents=True)
    (output / "index.m3u8").write_text("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n")
    (output / "segment_000000.ts").write_bytes(b"a")
    (output / "segment_000001.ts").write_bytes(b"b")
    new = Runtime(
        "new-session", "chan", old.profile, new_root,
        proc=FakeProc(), managed_relay=True, published=False, recovery_generation=1,
    )
    manager.runtimes[new.session_id] = new

    monkeypatch.setattr(manager, "start", lambda *a, **k: new)
    stopped = []
    monkeypatch.setattr(manager, "stop", lambda session_id, reason="manual": stopped.append((session_id, reason)))

    manager._recover(old, "av_desync:95s")
    assert manager.active_relays[(old.stream_id, old.profile)] == new.session_id
    assert new.published is True
    assert old.published is False
    assert stopped and stopped[-1][0] == old.session_id
