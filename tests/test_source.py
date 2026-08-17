from app.source import parse_m3u, xmltv_channel_ids, xmltv_channel_index
from app.guide import build_m3u

M3U = '''#EXTM3U
#EXTINF:-1 tvg-id="alpha" tvg-name="Alpha TV" tvg-logo="https://img/alpha.png" group-title="News" tvg-chno="101",Alpha TV
https://cdn.example/alpha/master.m3u8
#EXTINF:-1 tvg-id="beta" group-title="Movies",Beta Movies
relative/beta.m3u8
'''

XML = b'''<?xml version="1.0"?><tv><channel id="alpha"><display-name>Alpha</display-name></channel><channel id="beta"><display-name>Beta</display-name></channel><programme channel="alpha" start="20260817100000 +0000" stop="20260817110000 +0000"><title>News</title></programme></tv>'''


def test_full_m3u_import_parses_channel_metadata_and_relative_urls():
    items = parse_m3u(M3U, "https://provider.example/path/list.m3u")
    assert len(items) == 2
    assert items[0]["tvg_id"] == "alpha"
    assert items[0]["channel_number"] == "101"
    assert items[0]["group_title"] == "News"
    assert items[1]["input_url"] == "https://provider.example/path/relative/beta.m3u8"
    assert items[0]["source_key"] == "tvg:alpha"


def test_xmltv_channel_index_reads_ids_without_needing_programme_body():
    assert xmltv_channel_ids(XML) == {"alpha", "beta"}


def test_export_playlist_only_contains_selected_present_channels():
    streams = [
        {"id":"a","name":"A","input_url":"x","selected":True,"missing":False,"tvg_id":"alpha"},
        {"id":"b","name":"B","input_url":"x","selected":False,"missing":False,"tvg_id":"beta"},
        {"id":"c","name":"C","input_url":"x","selected":True,"missing":True,"tvg_id":"charlie"},
    ]
    text = build_m3u(streams, "http://lab:8091")
    assert "/stream/a/index.m3u8" in text
    assert "/stream/b/index.m3u8" not in text
    assert "/stream/c/index.m3u8" not in text


def test_xmltv_channel_name_index_supports_fallback_matching():
    ids, names = xmltv_channel_index(XML)
    assert ids == {"alpha", "beta"}
    assert names["alpha"] == ["alpha"]
    assert names["beta"] == ["beta"]
