from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any
from urllib.parse import urljoin

import httpx

_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_RESOLUTION = re.compile(r'^(\d+)x(\d+)$')


@dataclass(frozen=True)
class Variant:
    url: str
    width: int = 0
    height: int = 0
    bandwidth: int = 0
    codecs: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _attrs(line: str) -> dict[str, str]:
    payload = line.split(":", 1)[1] if ":" in line else line
    result: dict[str, str] = {}
    for key, raw in _ATTR.findall(payload):
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        result[key] = value
    return result


def parse_master_variants(text: str, base_url: str) -> list[Variant]:
    lines = text.splitlines()
    variants: list[Variant] = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = _attrs(line)
        uri = ""
        for nxt in lines[index + 1 :]:
            nxt = nxt.strip()
            if not nxt:
                continue
            if nxt.startswith("#"):
                continue
            uri = nxt
            break
        if not uri:
            continue
        width = height = 0
        match = _RESOLUTION.match(attrs.get("RESOLUTION", ""))
        if match:
            width, height = int(match.group(1)), int(match.group(2))
        try:
            bandwidth = int(attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH") or 0)
        except ValueError:
            bandwidth = 0
        variants.append(
            Variant(
                url=urljoin(base_url, uri),
                width=width,
                height=height,
                bandwidth=bandwidth,
                codecs=attrs.get("CODECS", ""),
            )
        )
    return variants


def select_variant(variants: list[Variant], quality: str = "auto") -> Variant | None:
    if not variants:
        return None
    ordered = sorted(variants, key=lambda v: (v.height, v.width, v.bandwidth))
    quality = str(quality or "auto").strip().lower()
    if quality in {"auto", "best", "highest"}:
        return ordered[-1]
    target_map = {"720p": 720, "540p": 540, "360p": 360}
    target = target_map.get(quality)
    if target is None:
        return ordered[-1]
    at_or_below = [v for v in ordered if v.height and v.height <= target]
    if at_or_below:
        return at_or_below[-1]
    with_height = [v for v in ordered if v.height]
    return with_height[0] if with_height else ordered[-1]


def resolve_variant(
    input_url: str,
    *,
    user_agent: str = "",
    quality: str = "auto",
    enabled: bool = True,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Resolve an HLS master to one concrete media playlist.

    If the input is already a media playlist, or if pinning is disabled, the final
    redirected input URL is returned unchanged. The returned metadata is suitable
    for stream.json and diagnostic display.
    """
    headers = {"User-Agent": user_agent} if user_agent else {}
    with httpx.Client(
        timeout=httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds)),
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = client.get(input_url)
        response.raise_for_status()
        final_url = str(response.url)
        text = response.text
    variants = parse_master_variants(text, final_url)
    selected = select_variant(variants, quality) if enabled else None
    if selected is None:
        return {
            "original_url": input_url,
            "master_url": final_url,
            "resolved_url": final_url,
            "pinned": False,
            "quality": quality,
            "variant": None,
            "variant_count": len(variants),
        }
    return {
        "original_url": input_url,
        "master_url": final_url,
        "resolved_url": selected.url,
        "pinned": True,
        "quality": quality,
        "variant": selected.as_dict(),
        "variant_count": len(variants),
    }


def media_segments_and_boundaries(text: str, base_url: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Return ordered media segment URLs and SSAI/discontinuity boundary contexts."""
    segments: list[str] = []
    boundaries: list[dict[str, Any]] = []
    pending_discontinuity = False
    pending_asset = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-DISCONTINUITY"):
            pending_discontinuity = True
            continue
        if line.startswith("#EXT-X-ASSET"):
            pending_asset = line
            continue
        if line.startswith("#"):
            continue
        url = urljoin(base_url, line)
        segments.append(url)
        if pending_discontinuity:
            boundaries.append(
                {
                    "key": url,
                    "segment_url": url,
                    "segment_index": len(segments) - 1,
                    "asset": pending_asset,
                }
            )
            pending_discontinuity = False
            pending_asset = ""
    return segments, boundaries
