# cranborg_util/ops_build_cache.py
# ---------------------------------------------------------------------------
# Cache: evita di ricalcolare X·Y quando cambiano solo i parametri di display.
#
# FIX: evita NameError su annotazioni (Floretion) all'import in Blender.
# Usa "from __future__ import annotations" + TYPE_CHECKING.
# ---------------------------------------------------------------------------

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from floretion import Floretion
    from .ui_props import FloretionMeshSettings


_FLO_CACHE: Dict[str, Any] = {
    "order": None,
    "x_string": None,
    "y_string": None,
    "flo_x": None,
    "flo_y": None,
    "flo_z": None,
}


def _cache_init() -> Dict[str, Any]:
    """Assicura che _FLO_CACHE esista anche dopo reload/undo parziali di Blender."""
    g = globals()
    c = g.get("_FLO_CACHE")
    if not isinstance(c, dict):
        c = {}
    defaults = {
        "order": None,
        "x_string": None,
        "y_string": None,
        "flo_x": None,
        "flo_y": None,
        "flo_z": None,
    }
    for k, v in defaults.items():
        if k not in c:
            c[k] = v
    g["_FLO_CACHE"] = c
    return c


def _cache_set(
    *,
    order: int,
    x_string: str,
    y_string: str,
    flo_x: "Floretion",
    flo_y: "Floretion",
    flo_z: "Floretion",
) -> None:
    c = _cache_init()
    c["order"] = int(order)
    c["x_string"] = str(x_string or "")
    c["y_string"] = str(y_string or "")
    c["flo_x"] = flo_x
    c["flo_y"] = flo_y
    c["flo_z"] = flo_z


def _cache_get() -> Optional[Dict[str, Any]]:
    c = _cache_init()
    if c.get("flo_z") is None:
        return None
    return c


def _cache_matches_props(props: "FloretionMeshSettings", order: int) -> bool:
    c = _cache_get()
    if not c:
        return False
    try:
        if int(c.get("order") or 0) != int(order):
            return False
        if str(c.get("x_string") or "") != str(getattr(props, "x_string", "") or ""):
            return False
        if str(c.get("y_string") or "") != str(getattr(props, "y_string", "") or ""):
            return False
    except Exception:
        return False
    return True
