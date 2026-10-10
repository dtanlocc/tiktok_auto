"""Opt-in preferences for sites that process user media in a 2D canvas."""

from __future__ import annotations

from typing import Any, Mapping


_CANVAS_SUBSTITUTION_PREF = "zoom.stealth.canvas.substitute_pixels"


def merge_faithful_canvas_readback(
    extra_prefs: Mapping[str, Any] | None,
    enabled: bool,
) -> dict[str, Any] | None:
    """Return prefs with faithful web-content Canvas2D readback when enabled.

    The patched engine normally substitutes pixels returned to web content so
    canvas fingerprints are host-independent. That is unsuitable for upload
    editors which legitimately call ``getImageData`` and then encode those
    bytes: the substituted fingerprint pixels become part of the user's media.

    This opt-in only changes Canvas2D readback. WebGL substitution and the rest
    of the stealth profile remain unchanged. Callers should use one setting
    consistently for a browser identity instead of toggling it per navigation.
    """
    if not enabled:
        return dict(extra_prefs) if extra_prefs is not None else None
    merged = dict(extra_prefs or {})
    merged[_CANVAS_SUBSTITUTION_PREF] = False
    return merged
