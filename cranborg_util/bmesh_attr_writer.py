# cranborg_util/bmesh_attr_writer.py
# scrive face_coeff e floretion_color prima di bm.to_mesh
from __future__ import annotations

from typing import Iterable, Sequence, Tuple
import bmesh


def write_face_float_layer(bm: bmesh.types.BMesh, layer_name: str, values: Iterable[float]) -> None:
    bm.faces.ensure_lookup_table()
    layer = bm.faces.layers.float.get(layer_name)
    if layer is None:
        layer = bm.faces.layers.float.new(layer_name)

    vals = list(values)
    for i, f in enumerate(bm.faces):
        f[layer] = float(vals[i]) if i < len(vals) else 0.0


def _to_rgba01(c) -> Tuple[float, float, float, float]:
    try:
        if len(c) == 3:
            r, g, b = c
            a = 1.0
        else:
            r, g, b, a = c
    except Exception:
        return (0.0, 0.0, 0.0, 1.0)

    try:
        mx = max(float(r), float(g), float(b), float(a))
    except Exception:
        mx = 1.0

    if mx > 1.5:
        return (float(r) / 255.0, float(g) / 255.0, float(b) / 255.0, 1.0)

    a = float(a) if a is not None else 1.0
    if a <= 0.0:
        a = 1.0
    return (float(r), float(g), float(b), a)


def _ensure_loop_color_layer(bm: bmesh.types.BMesh, layer_name: str):
    try:
        layer = bm.loops.layers.float_color.get(layer_name)
        if layer is None:
            layer = bm.loops.layers.float_color.new(layer_name)
        return layer
    except Exception:
        pass

    try:
        layer = bm.loops.layers.color.get(layer_name)
        if layer is None:
            layer = bm.loops.layers.color.new(layer_name)
        return layer
    except Exception as e:
        raise RuntimeError(f"Impossibile creare loop color layer '{layer_name}': {e}") from e


def write_loop_color_layer_per_face(bm: bmesh.types.BMesh, layer_name: str, face_colors: Sequence) -> None:
    bm.faces.ensure_lookup_table()
    col_layer = _ensure_loop_color_layer(bm, layer_name)

    for fi, f in enumerate(bm.faces):
        col = _to_rgba01(face_colors[fi]) if fi < len(face_colors) else (0.0, 0.0, 0.0, 1.0)
        for loop in f.loops:
            loop[col_layer] = col
