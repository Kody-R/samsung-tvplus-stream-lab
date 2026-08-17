from pathlib import Path

from app.ffmpeg import HLS_PROFILES, build, build_ts_relay
from app.session import child_playlist_urls, parse_manifest

S = {
    "ffmpeg_path": "ffmpeg",
    "dts_delta_threshold": 1.0,
    "hls_time": 3,
    "hls_list_size": 12,
    "hls_delete_threshold": 4,
    "render_device": "/dev/dri/renderD129",
}
C = {"id": "139", "input_url": "https://example.test/live.m3u8", "user_agent": "browser"}


def test_normalized_hls_is_timestamp_cleaner(tmp_path: Path):
    c = build("normalize-hls", C, S, tmp_path)
    assert "-copyts" not in c and "-re" not in c
    assert "-dts_delta_threshold" in c
    assert c[c.index("-c:v") + 1] == "copy"
    assert "-hls_start_number_source" not in c
    assert c[c.index("-start_number") + 1] == "0"


def test_permissive_copy_puts_extension_picky_before_input(tmp_path: Path):
    c = build("copy-null-permissive", C, S, tmp_path)
    assert c[c.index("-extension_picky") + 1] == "0"
    assert c.index("-extension_picky") < c.index("-i")
    assert c[-3:] == ["-f", "null", "-"]


def test_permissive_hls_profiles_are_playable(tmp_path: Path):
    assert "normalize-hls-permissive" in HLS_PROFILES
    assert "transcode-hls-permissive" in HLS_PROFILES
    for profile in ("normalize-hls-permissive", "transcode-hls-permissive"):
        c = build(profile, C, S, tmp_path / profile)
        assert c[c.index("-extension_picky") + 1] == "0"
        assert c[-1].endswith("index.m3u8")


def test_ts_relay_has_no_hls_muxer():
    c = build_ts_relay(C, S)
    assert c[-2:] == ["mpegts", "pipe:1"]
    assert "-copyts" not in c and "-re" not in c
    assert c[c.index("-progress") + 1] == "pipe:2"


def test_permissive_ts_relay_disables_extension_picky():
    c = build_ts_relay(C, S, permissive=True)
    assert c[c.index("-extension_picky") + 1] == "0"
    assert c.index("-extension_picky") < c.index("-i")


def test_child_manifest_discovery_and_extensionless_segments():
    master = """#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nvideo/720.m3u8\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",URI=\"audio/main.m3u8\"\n"""
    children = child_playlist_urls(master, "https://cdn.example/live/master.m3u8")
    assert children == [
        "https://cdn.example/live/video/720.m3u8",
        "https://cdn.example/live/audio/main.m3u8",
    ]
    media = """#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:1925\n#EXT-X-DISCONTINUITY\n#EXT-X-ASSET:CAID=\"ad\"\n#EXTINF:6.0,\nhttps://cdn.example/v1/segment/token/0/1925\n"""
    parsed = parse_manifest(media, "https://cdn.example/live/720.m3u8")
    assert parsed["playlist_type"] == "media"
    assert parsed["media_sequence"] == 1925
    assert parsed["discontinuities"] == 1
    assert parsed["assets"] == 1
    assert parsed["extensionless_segments"] == 1
    assert parsed["extensionless_segment_urls"][0].endswith("/0/1925")
