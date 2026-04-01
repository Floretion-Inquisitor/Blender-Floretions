# color_adapter.py
# -*- coding: utf-8 -*-

"""
Compatibilità + punto unico di accesso per:
- setup nodi neighbor-colors su materiale
- calcolo colori coeff-based in modo quantile-based
"""

from __future__ import annotations

from typing import Optional
from bisect import bisect_left, bisect_right

try:
    import bpy  # type: ignore
except Exception:
    bpy = None  # fuori Blender

import numpy as np

from .shader_neighbor_nodes import ensure_neighbor_color_nodes, set_neighbor_color_mode


DEFAULT_MATERIAL_NAME = "Cranborg_NeighborColors"


def resolve_effective_color_mode(props) -> str:
    """
    Mappa la nuova UI "famiglia + sottomodo" sul mode-id effettivo usato
    dal calcolo colori / shader routing.
    Fallback morbido verso la property legacy `color_mode`.
    """
    if props is None:
        return "ABS_HSV"

    fam = str(getattr(props, "color_family", "") or "").strip().upper()
    if fam == "NEIGHBOR":
        return str(getattr(props, "neighbor_color_mode", "NEIGH_EDGE_SAT") or "NEIGH_EDGE_SAT")
    if fam == "QUANTILE":
        return str(getattr(props, "quantile_color_mode", "QUANTILE_8") or "QUANTILE_8")
    if fam == "STATIC":
        return str(getattr(props, "static_color_mode", "ABS_HSV") or "ABS_HSV")

    legacy = str(getattr(props, "color_mode", "ABS_HSV") or "ABS_HSV")
    return legacy



def mode_id_to_str(mode_id: str) -> str:
    s = str(mode_id or "").strip()
    if s and (("-" in s) or s.islower()):
        return s

    return {
        'ABS_HSV':          "abs-hsv",
        'DIVERGING':        "diverging",
        'GRAY':             "gray",
        'LOG_HSV':          "log-hsv",
        'BANDED':           "banded",
        'LEGACY':           "legacy",
        'QUANTILE_2':       "quantile-2",
        'QUANTILE_4':       "quantile-4",
        'QUANTILE_8':       "quantile-8",
        'PASTEL':           "pastel",
        'PASTEL_DIVERGING': "pastel-diverging",
        'COOLWARM':         "coolwarm",
        'HEAT':             "heat",
        'NEON':             "neon",
        'SAT_ONLY':         "sat-only",
        'DISTANCE_HSV':     "distance-hsv",
        'BANDED_PASTEL':    "banded-pastel",
        'INK':              "ink",
        'STATIC':           "abs-hsv",
        'NEIGHBOR':         "legacy",
        'QUANTILE':         "quantile-8",
    }.get(s, "abs-hsv")


def neg_policy_id_to_str(neg_id: str) -> str:
    s = str(neg_id or "").strip()
    if s and (("-" in s) or s.islower()):
        return s

    return {
        'HUE_180': "hue-180",
        'HUE_90':  "hue-90",
        'NONE':    "none",
    }.get(s, "hue-180")


def _quantile_abs_and_signed(coeffs: np.ndarray, eps: float = 1e-12):
    """
    Restituisce:
      abs_q  in [0..1]
      signed_q in [-1..1]
    basati sul rank empirico di abs(coeff).
    """
    coeffs = np.asarray(coeffs, dtype=float)
    abs_vals = np.abs(coeffs)
    active = abs_vals > eps

    abs_q = np.zeros_like(abs_vals, dtype=float)
    signed_q = np.zeros_like(abs_vals, dtype=float)

    active_abs = abs_vals[active]
    if active_abs.size == 0:
        return abs_q, signed_q

    sorted_abs = np.sort(active_abs)
    n = int(sorted_abs.size)

    for i, a in enumerate(abs_vals):
        if a <= eps:
            continue

        if n == 1:
            q = 1.0
        else:
            left = bisect_left(sorted_abs, a)
            right = bisect_right(sorted_abs, a)
            midrank = 0.5 * (left + right - 1)
            q = midrank / float(n - 1)

        q = max(0.0, min(1.0, q))
        abs_q[i] = q
        signed_q[i] = q if coeffs[i] >= 0.0 else -q

    return abs_q, signed_q


def compute_colors(
    coeffs: np.ndarray,
    basevec_at_pct: np.ndarray,
    dist_norm: np.ndarray,
    *,
    color_mode_id: str,
    max_val_config: float,
    auto_clip_pct: float,
    gamma: float,
    sat_dist_weight: float,
    neg_policy_id: str,
    band_count: int = 8,
):
    """
    Calcolo colori QUANTILE-BASED.

    Invece di usare |coeff| / max(|coeff|), usiamo il rank empirico di |coeff|
    (quantile), così pochi outlier enormi non fanno "schiacciare" tutti gli
    altri valori vicino al nero.
    """
    try:
        from lib.triangleize_utils.coloring import map_color
    except Exception:
        map_color = None

    coeffs = np.asarray(coeffs, dtype=float)
    basevec_at_pct = np.asarray(basevec_at_pct, dtype=float)
    dist_norm = np.asarray(dist_norm, dtype=float)

    n = int(coeffs.size)
    colors = np.zeros((n, 3), dtype=float)
    brightness = np.zeros(n, dtype=float)

    abs_q, signed_q = _quantile_abs_and_signed(coeffs, eps=1e-12)

    # Per il mapping "coeff-based" passiamo una versione firmata ma già
    # quantile-normalizzata in [-1..1].
    coeff_for_map = signed_q

    # Niente max-val assoluto dei coeff: la dinamica è già contenuta nei quantili.
    max_val_eff = 1.0

    if map_color is None:
        # fallback molto semplice: grayscale quantile-based
        colors[:, 0] = abs_q
        colors[:, 1] = abs_q
        colors[:, 2] = abs_q
        brightness[:] = abs_q
        return colors, brightness

    mode_str = mode_id_to_str(color_mode_id)
    neg_policy_str = neg_policy_id_to_str(neg_policy_id)

    for i in range(n):
        bgr, _ = map_color(
            coeff=float(coeff_for_map[i]),
            basevec_at_pct=float(basevec_at_pct[i]) if len(basevec_at_pct) > i else 0.0,
            dist_norm=float(dist_norm[i]) if len(dist_norm) > i else 0.0,
            mode=mode_str,
            max_val=float(max_val_eff),
            gamma=float(gamma),
            sat_dist_weight=float(sat_dist_weight),
            neg_policy=neg_policy_str,
            band_count=int(band_count),
        )
        colors[i, :] = bgr

    # brightness pure quantile-based
    brightness[:] = abs_q
    return colors, brightness


def ensure_neighbor_color_material(
    obj: "bpy.types.Object",
    *,
    material_name: str = DEFAULT_MATERIAL_NAME,
    mode: int | str = 0,
) -> Optional["bpy.types.Material"]:
    """
    Assicura che l'oggetto abbia un materiale con nodi neighbor-colors.
    mode può essere int legacy o string id.
    """
    if bpy is None or obj is None:
        return None

    mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = bpy.data.materials.new(material_name)
        mat.use_nodes = True

    ensure_neighbor_color_nodes(mat)

    # assegna a obj (slot 0)
    if obj.data is not None and hasattr(obj.data, "materials"):
        mats = obj.data.materials
        if len(mats) == 0:
            mats.append(mat)
        else:
            mats[0] = mat

    # applica mode se passato
    try:
        if isinstance(mode, str):
            set_neighbor_color_mode(mat, mode)
    except Exception:
        pass

    return mat


def set_neighbor_mode_on_object(obj: "bpy.types.Object", mode: int | str = 0, *, material_name: str = DEFAULT_MATERIAL_NAME) -> None:
    """Comodo se vuoi cambiare modalità dopo la build."""
    if bpy is None or obj is None:
        return
    mat = None
    if obj.data is not None and hasattr(obj.data, "materials") and len(obj.data.materials) > 0:
        mat = obj.data.materials[0]
    if mat is None:
        mat = bpy.data.materials.get(material_name)
    if mat is not None:
        try:
            if isinstance(mode, str):
                set_neighbor_color_mode(mat, mode)
        except Exception:
            pass
