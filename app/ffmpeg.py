from __future__ import annotations

import shlex
from pathlib import Path

BASE_HLS_PROFILES = {"normalize-hls", "normalize-hls-sync", "transcode-hls", "vaapi-hls", "qsv-hls"}
PERMISSIVE_HLS_PROFILES = {f"{name}-permissive" for name in BASE_HLS_PROFILES}
HLS_PROFILES = BASE_HLS_PROFILES | PERMISSIVE_HLS_PROFILES
NULL_PROFILES = {"copy-null", "copy-null-permissive", "ts-null"}


def is_permissive(profile: str) -> bool:
    return profile.endswith("-permissive")


def canonical_profile(profile: str) -> str:
    return profile.removesuffix("-permissive")


def base_input(stream: dict, settings: dict, *, progress: str = "pipe:1") -> list[str]:
    cmd = [
        str(settings.get("ffmpeg_path") or "ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "verbose",
        "-progress",
        progress,
        "-nostats",
    ]
    ua = str(stream.get("user_agent") or "").strip()
    if ua:
        cmd += ["-user_agent", ua]
    return cmd


def build(profile: str, stream: dict, settings: dict, out_dir: Path) -> list[str]:
    base = canonical_profile(profile)
    cmd = base_input(stream, settings)
    threshold = str(settings.get("dts_delta_threshold", 60.0))

    if base in BASE_HLS_PROFILES or base in {"copy-null", "ts-null"}:
        cmd += ["-dts_delta_threshold", threshold]
    if base in {"normalize-hls-sync", "transcode-hls", "vaapi-hls", "qsv-hls"}:
        cmd += ["-fflags", "+genpts"]
    if is_permissive(profile):
        # Samsung/Akamai SSAI can expose valid HLS segment URLs without a normal file
        # extension. This disables FFmpeg's HLS extension-picky input validation only.
        cmd += ["-extension_picky", "0"]

    if base == "vaapi-hls":
        dev = str(settings.get("render_device") or "/dev/dri/renderD128")
        cmd += ["-hwaccel", "vaapi", "-hwaccel_device", dev, "-hwaccel_output_format", "vaapi"]
    elif base == "qsv-hls":
        dev = str(settings.get("render_device") or "/dev/dri/renderD128")
        cmd += [
            "-init_hw_device", f"vaapi=va:{dev},driver=iHD",
            "-init_hw_device", "qsv=qs@va",
            "-filter_hw_device", "qs",
            "-hwaccel", "vaapi",
            "-hwaccel_output_format", "vaapi",
        ]

    cmd += ["-i", str(stream.get("resolved_input_url") or stream["input_url"]), "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]

    if base in {"copy-null", "ts-null"}:
        cmd += ["-c", "copy", "-f", "null", "-"]
        return cmd
    if base == "normalize-hls":
        cmd += ["-c:v", "copy", "-c:a", "copy"]
    elif base == "normalize-hls-sync":
        # Preserve the original H.264 video while giving audio its own timestamp
        # correction path. This is intended for FAST/SSAI feeds that stay alive
        # but develop A/V drift after timestamp discontinuities.
        bitrate = max(64, min(320, int(settings.get("audio_sync_bitrate_kbps", 160))))
        cmd += [
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", f"{bitrate}k",
            "-af", "aresample=async=1:first_pts=0",
        ]
    elif base == "transcode-hls":
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-af", "aresample=async=1:first_pts=0",
        ]
    elif base == "vaapi-hls":
        cmd += [
            "-vf", "scale_vaapi=format=nv12", "-c:v", "h264_vaapi", "-rc_mode", "VBR",
            "-b:v", "4M", "-maxrate", "6M", "-c:a", "aac", "-b:a", "160k",
            "-af", "aresample=async=1:first_pts=0",
        ]
    elif base == "qsv-hls":
        cmd += [
            "-vf", "hwmap=derive_device=qsv,format=qsv", "-c:v", "h264_qsv", "-preset", "veryfast",
            "-b:v", "4M", "-maxrate", "6M", "-c:a", "aac", "-b:a", "160k",
            "-af", "aresample=async=1:first_pts=0",
        ]
    else:
        raise ValueError(f"Unknown profile: {profile}")

    out_dir.mkdir(parents=True, exist_ok=True)
    # Deliberately no -re and no -copyts. Use monotonically increasing start number
    # inside each session only.
    cmd += [
        "-f", "hls",
        "-hls_segment_type", "mpegts",
        "-hls_time", str(settings.get("hls_time", 3)),
        "-hls_list_size", str(settings.get("hls_list_size", 12)),
        "-hls_delete_threshold", str(settings.get("hls_delete_threshold", 4)),
        "-hls_flags", "delete_segments+omit_endlist+temp_file",
        "-start_number", "0",
        "-hls_segment_filename", str(out_dir / "segment_%06d.ts"),
        "-y", str(out_dir / "index.m3u8"),
    ]
    return cmd


def build_ts_relay(stream: dict, settings: dict, *, permissive: bool = False) -> list[str]:
    # Progress shares stderr with FFmpeg diagnostics. SessionManager tees the raw stream
    # to ffmpeg.log and extracts progress key/value blocks while stdout stays pure MPEG-TS.
    cmd = base_input(stream, settings, progress="pipe:2")
    cmd += ["-dts_delta_threshold", str(settings.get("dts_delta_threshold", 60.0))]
    if permissive:
        cmd += ["-extension_picky", "0"]
    cmd += [
        "-i", str(stream.get("resolved_input_url") or stream["input_url"]),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
        "-c", "copy", "-f", "mpegts", "pipe:1",
    ]
    return cmd


def shell_join(cmd: list[str]) -> str:
    return shlex.join(cmd)
