# cranborg_util/colors_compute.py
from __future__ import annotations

from typing import Tuple
import numpy as np

from . import color_adapter as _color_adapter

try:
    from lib.triangleize_utils.coloring import choose_max_val_for_colors, map_color
except Exception:  # pragma: no cover
    choose_max_val_for_colors = None
    map_color = None


def mode_id_to_str(mode_id: str) -> str:
    fn = getattr(_color_adapter, "mode_id_to_str", None)
    if callable(fn):
        try:
            return str(fn(mode_id))
        except Exception:
            pass

    s = str(mode_id or "").strip()
    if s and (("-" in s) or s.islower()):
        return s

    return {
        "ABS_HSV": "abs-hsv",
        "DIVERGING": "diverging",
        "GRAY": "gray",
        "LOG_HSV": "log-hsv",
        "BANDED": "banded",
        "LEGACY": "legacy",
        "PASTEL": "pastel",
        "PASTEL_DIVERGING": "pastel-diverging",
        "COOLWARM": "coolwarm",
        "HEAT": "heat",
        "NEON": "neon",
        "SAT_ONLY": "sat-only",
        "DISTANCE_HSV": "distance-hsv",
        "BANDED_PASTEL": "banded-pastel",
        "INK": "ink",
    }.get(s, "abs-hsv")


def neg_policy_id_to_str(neg_id: str) -> str:
    fn = getattr(_color_adapter, "neg_policy_id_to_str", None)
    if callable(fn):
        try:
            return str(fn(neg_id))
        except Exception:
            pass

    s = str(neg_id or "").strip()
    if s and (("-" in s) or s.islower()):
        return s

    return {
        "HUE_180": "hue-180",
        "HUE_90": "hue-90",
        "NONE": "none",
    }.get(s, "hue-180")


def compute_colors(
    *,
    coeffs: np.ndarray,
    basevec_at_pct: np.ndarray,
    dist_norm: np.ndarray,
    color_mode_id: str,
    max_val_config: float,
    auto_clip_pct: float,
    gamma: float,
    sat_dist_weight: float,
    neg_policy_id: str,
    band_count: int = 8,
) -> Tuple[np.ndarray, np.ndarray]:

    fn = getattr(_color_adapter, "compute_colors", None)
    if callable(fn):
        return fn(
            coeffs=coeffs,
            basevec_at_pct=basevec_at_pct,
            dist_norm=dist_norm,
            color_mode_id=color_mode_id,
            max_val_config=max_val_config,
            auto_clip_pct=auto_clip_pct,
            gamma=gamma,
            sat_dist_weight=sat_dist_weight,
            neg_policy_id=neg_policy_id,
            band_count=band_count,
        )

    if map_color is None or choose_max_val_for_colors is None:
        raise ImportError("lib.triangleize_utils.coloring non disponibile e compute_colors mancante in color_adapter.py")

    mode_str = mode_id_to_str(color_mode_id)
    neg_policy_str = neg_policy_id_to_str(neg_policy_id)

    coeffs = np.asarray(coeffs, dtype=float)
    basevec_at_pct = np.asarray(basevec_at_pct, dtype=float)
    dist_norm = np.asarray(dist_norm, dtype=float)

    if float(max_val_config) > 0:
        max_val_eff = float(max_val_config)
    else:
        max_val_eff = float(choose_max_val_for_colors(coeffs, None, float(auto_clip_pct)))

    n = int(coeffs.size)
    colors = np.zeros((n, 3), dtype=float)

    for i in range(n):
        bgr, _bright = map_color(
            coeff=float(coeffs[i]),
            basevec_at_pct=float(basevec_at_pct[i]) if basevec_at_pct.size > i else 0.0,
            dist_norm=float(dist_norm[i]) if dist_norm.size > i else 0.0,
            mode=str(mode_str),
            max_val=float(max_val_eff),
            gamma=float(gamma),
            sat_dist_weight=float(sat_dist_weight),
            neg_policy=str(neg_policy_str),
            band_count=int(band_count),
        )
        colors[i, :] = (float(bgr[0]), float(bgr[1]), float(bgr[2]))

    mags = np.abs(coeffs)
    if max_val_eff > 0:
        brightness = np.clip(mags / float(max_val_eff), 0.0, 1.0)
    else:
        brightness = np.zeros_like(mags, dtype=float)

    return colors, brightness
