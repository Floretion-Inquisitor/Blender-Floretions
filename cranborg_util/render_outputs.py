# cranborg_util/render_outputs.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import bpy

# ---------------------------------------------------------------------------
# AOV setup per Floretion Triangle Mesh
#
# Importante: questo modulo NON tocca le Output Properties (file format ecc.).
# Si limita a:
#   - creare AOV sul View Layer (FloColor/FloHeight/FloMaskZero)
#   - aggiungere nodi "AOV Output" nei materiali FloretionMaterial / ZeroMaterial
#
# Così l’add-on resta stabile e non “rompe” pipeline o preset di render.
# ---------------------------------------------------------------------------

DEFAULT_MAT_NONZERO = "FloretionMaterial"
DEFAULT_MAT_ZERO    = "FloretionZeroMaterial"

AOV_FLO_COLOR  = "FloColor"      # COLOR: floretion_color
AOV_FLO_HEIGHT = "FloHeight"     # VALUE: object Z (raw)
AOV_FLO_MASK0  = "FloMaskZero"   # VALUE: 1 per zero-material, 0 altrimenti


def _ensure_view_layer_aov(scene: bpy.types.Scene, view_layer: bpy.types.ViewLayer, name: str, aov_type: str):
    for a in getattr(view_layer, "aovs", []):
        try:
            if a.name == name:
                try:
                    a.type = aov_type
                except Exception:
                    pass
                return a
        except Exception:
            continue

    aov_coll = getattr(view_layer, "aovs", None)
    if aov_coll is None:
        raise RuntimeError("view_layer.aovs non disponibile (Shader AOV).")

    fn = getattr(aov_coll, "new", None)
    if callable(fn):
        a = fn()
        a.name = name
        try:
            a.type = aov_type
        except Exception:
            pass
        return a

    # fallback operator
    with bpy.context.temp_override(scene=scene, view_layer=view_layer):
        bpy.ops.scene.view_layer_add_aov()
    a = view_layer.active_aov
    a.name = name
    try:
        a.type = aov_type
    except Exception:
        pass
    return a


def _find_or_create_node(nt, bl_idname: str, name: str):
    n = nt.nodes.get(name)
    if n is not None:
        return n
    n = nt.nodes.new(bl_idname)
    n.name = name
    n.label = name
    return n


def _ensure_output_aov_node(mat: bpy.types.Material, aov_name: str, node_name: str):
    if not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree

    for n in nt.nodes:
        if getattr(n, "bl_idname", "") == "ShaderNodeOutputAOV":
            try:
                if getattr(n, "aov_name", "") == aov_name:
                    return n
            except Exception:
                pass

    n = _find_or_create_node(nt, "ShaderNodeOutputAOV", node_name)
    try:
        n.aov_name = aov_name
    except Exception:
        pass
    return n


def _link(nt, out_socket, in_socket):
    try:
        nt.links.new(out_socket, in_socket)
    except Exception:
        pass


def _ensure_aov_flo_color(mat: bpy.types.Material, attr_name: str = "floretion_color"):
    if not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree

    # Attribute node (preferito)
    try:
        attr = _find_or_create_node(nt, "ShaderNodeAttribute", "FLO__Attr_floretion_color")
        attr.attribute_name = attr_name
        col_out = attr.outputs.get("Color") or attr.outputs[0]
    except Exception:
        attr = _find_or_create_node(nt, "ShaderNodeVertexColor", "FLO__VCol_floretion_color")
        try:
            attr.layer_name = attr_name
        except Exception:
            pass
        col_out = attr.outputs.get("Color") or attr.outputs[0]

    out = _ensure_output_aov_node(mat, AOV_FLO_COLOR, "FLO__AOV_FloColor")
    col_in = out.inputs.get("Color") if hasattr(out, "inputs") else None
    if col_in is None and hasattr(out, "inputs") and len(out.inputs):
        col_in = out.inputs[0]
    if col_in is not None:
        _link(nt, col_out, col_in)


def _ensure_aov_height_raw(mat: bpy.types.Material):
    if not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree

    tex = _find_or_create_node(nt, "ShaderNodeTexCoord", "FLO__TexCoord")
    sep = _find_or_create_node(nt, "ShaderNodeSeparateXYZ", "FLO__SepXYZ")
    out = _ensure_output_aov_node(mat, AOV_FLO_HEIGHT, "FLO__AOV_FloHeight")

    _link(nt, tex.outputs.get("Object") or tex.outputs[0], sep.inputs.get("Vector") or sep.inputs[0])
    val_in = out.inputs.get("Value") if hasattr(out, "inputs") else None
    if val_in is None and hasattr(out, "inputs") and len(out.inputs):
        val_in = out.inputs[0]
    if val_in is not None:
        _link(nt, sep.outputs.get("Z") or sep.outputs[2], val_in)


def _ensure_aov_mask_zero(mat: bpy.types.Material, is_zero: bool):
    if not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree

    v = _find_or_create_node(nt, "ShaderNodeValue", "FLO__Value_MaskZero")
    try:
        v.outputs[0].default_value = 1.0 if is_zero else 0.0
    except Exception:
        pass

    out = _ensure_output_aov_node(mat, AOV_FLO_MASK0, "FLO__AOV_FloMaskZero")
    val_in = out.inputs.get("Value") if hasattr(out, "inputs") else None
    if val_in is None and hasattr(out, "inputs") and len(out.inputs):
        val_in = out.inputs[0]
    if val_in is not None:
        _link(nt, v.outputs[0], val_in)


def setup_floretion_exr_outputs(
    *,
    scene: bpy.types.Scene | None = None,
    view_layer: bpy.types.ViewLayer | None = None,
    mat_nonzero_name: str = DEFAULT_MAT_NONZERO,
    mat_zero_name: str = DEFAULT_MAT_ZERO,
):
    scene = scene or bpy.context.scene
    view_layer = view_layer or bpy.context.view_layer

    # AOV sul view layer
    _ensure_view_layer_aov(scene, view_layer, AOV_FLO_COLOR, "COLOR")
    _ensure_view_layer_aov(scene, view_layer, AOV_FLO_HEIGHT, "VALUE")
    _ensure_view_layer_aov(scene, view_layer, AOV_FLO_MASK0, "VALUE")

    # materiali
    mat_nonzero = bpy.data.materials.get(mat_nonzero_name)
    mat_zero = bpy.data.materials.get(mat_zero_name)

    if mat_nonzero is not None:
        _ensure_aov_flo_color(mat_nonzero)
        _ensure_aov_height_raw(mat_nonzero)
        _ensure_aov_mask_zero(mat_nonzero, is_zero=False)

    if mat_zero is not None:
        _ensure_aov_mask_zero(mat_zero, is_zero=True)

    try:
        view_layer.update()
    except Exception:
        pass

    return True
