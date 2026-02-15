# color_adapter.py
# -*- coding: utf-8 -*-

"""
Compatibilità + punto unico di accesso per:
- setup nodi neighbor-colors su materiale
- assegnazione materiale all'oggetto

Questo file NON calcola i vicini e NON scrive layer bmesh: quello è in shader_neighbor_attrs.py
"""

from __future__ import annotations

from typing import Optional

try:
    import bpy  # type: ignore
except Exception:
    bpy = None  # fuori Blender

from .shader_neighbor_nodes import ensure_neighbor_color_nodes, set_neighbor_color_mode


DEFAULT_MATERIAL_NAME = "Cranborg_NeighborColors"


def ensure_neighbor_color_material(
    obj: "bpy.types.Object",
    *,
    material_name: str = DEFAULT_MATERIAL_NAME,
    mode: int = 0,
) -> Optional["bpy.types.Material"]:
    """
    Assicura che l'oggetto abbia un materiale con nodi neighbor-colors.
    mode: 0 edges, 1 verts, 2 both
    """
    if bpy is None or obj is None:
        return None

    mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = bpy.data.materials.new(material_name)
        mat.use_nodes = True

    ensure_neighbor_color_nodes(mat, mode=mode)

    # assegna a obj (slot 0)
    if obj.data is not None and hasattr(obj.data, "materials"):
        mats = obj.data.materials
        if len(mats) == 0:
            mats.append(mat)
        else:
            mats[0] = mat

    return mat


def set_neighbor_mode_on_object(obj: "bpy.types.Object", mode: int = 0, *, material_name: str = DEFAULT_MATERIAL_NAME) -> None:
    """Comodo se vuoi cambiare modalità dopo la build."""
    if bpy is None or obj is None:
        return
    mat = None
    if obj.data is not None and hasattr(obj.data, "materials") and len(obj.data.materials) > 0:
        mat = obj.data.materials[0]
    if mat is None:
        mat = bpy.data.materials.get(material_name)
    if mat is not None:
        set_neighbor_color_mode(mat, mode)
