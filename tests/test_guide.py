from app.guide import build_m3u, build_xmltv

STREAMS = [
    {
        "id": "2",
        "name": "A&E Alaska State Troopers",
        "enabled": True,
        "tvg_id": "stvp-US1900023QQ",
        "tvg_logo": "https://example.test/logo.png",
        "group_title": "Samsung TV Plus",
        "channel_number": "139",
        "xmltv_url": "https://guide.test/guide.xml",
        "xmltv_channel_id": "provider.ae",
    }
]

XML = """<?xml version="1.0"?>
<tv>
  <channel id="provider.ae"><display-name>Provider A&amp;E</display-name></channel>
  <channel id="other"><display-name>Other</display-name></channel>
  <programme start="20260817120000 -0500" stop="20260817130000 -0500" channel="provider.ae"><title>Alaska State Troopers</title></programme>
  <programme start="20260817120000 -0500" stop="20260817130000 -0500" channel="other"><title>Other Show</title></programme>
</tv>
"""


def test_generated_m3u_points_at_stream_lab_hls():
    text = build_m3u(STREAMS, "http://192.168.8.122:8091")
    assert text.startswith("#EXTM3U\n")
    assert 'tvg-id="stvp-US1900023QQ"' in text
    assert 'group-title="Samsung TV Plus"' in text
    assert 'tvg-chno="139"' in text
    assert "http://192.168.8.122:8091/stream/2/index.m3u8" in text


def test_generated_xmltv_filters_and_remaps_programmes():
    payload, stats = build_xmltv(STREAMS, {"https://guide.test/guide.xml": XML})
    text = payload.decode()
    assert '<channel id="stvp-US1900023QQ">' in text
    assert 'channel="stvp-US1900023QQ"' in text
    assert "Alaska State Troopers" in text
    assert "Other Show" not in text
    assert stats["channels"] == 1
    assert stats["programmes"] == 1


def test_xmltv_without_source_still_has_channel():
    stream = dict(STREAMS[0], xmltv_url="")
    payload, stats = build_xmltv([stream], {})
    text = payload.decode()
    assert '<channel id="stvp-US1900023QQ">' in text
    assert stats["programmes"] == 0


def test_gzipped_xmltv_source_is_supported():
    import gzip
    payload, stats = build_xmltv(STREAMS, {"https://guide.test/guide.xml": gzip.compress(XML.encode())})
    text = payload.decode()
    assert "Alaska State Troopers" in text
    assert stats["programmes"] == 1
