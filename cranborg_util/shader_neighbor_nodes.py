# cranborg_util/shader_neighbor_nodes.py
from __future__ import annotations

import bpy

# Attr names (devono matchare ciò che scrive shader_neighbor_attrs.py)
DEFAULT_COLOR_ATTR = "floretion_color"

COLOR_EDGES = "color_edges"
COLOR_VERTS = "color_verts"
COLOR_BOTH  = "color_edges_and_verts"

BASE_COEFF         = "base_coeff"
BASE_COEFF_MIN     = "base_coeff_min"
BASE_COEFF_MAX     = "base_coeff_max"
BASE_COEFF_ABS_MAX = "base_coeff_abs_max"

# Nuovo: quantili
BASE_COEFF_Q       = "base_coeff_q"
BASE_COEFF_ABS_Q   = "base_coeff_abs_q"

# Datablock names (stabili => niente duplicati)
BASE_GROUP_NAME   = "FLORET_BASE_VECTOR_GROUP"
COLORS_GROUP_NAME = "FLORET_COLORS_GROUP"

# Node instance names (stabili => niente duplicati)
NODE_BASE_NAME   = "FLORET_NODE_BASE_VECTOR"
NODE_COLORS_NAME = "FLORET_NODE_COLORS"

# Legacy nodes/frames da ripulire
_LEGACY_NODE_NAMES = {
    "FLORET_COLOR_ATTR_MAIN",
    "FLORET_COLOR_READER",
    "Attr_floretion_color",
    "Attr_color_edges",
    "Attr_color_verts",
    "Attr_color_both",
    "Attr_neighbors_edges",
    "Attr_neighbors_verts",
    "Attr_neighbors_both",
    "base_vector",
    "FLORET_BASE_DEC",
    "FLORET_BASE_COEFF",
    "FLORET_BASE_ABS",
    "FLORET_BASE_ABS_NORM",
    "FLORET_BASE_MIN",
    "FLORET_BASE_MAX",
    "FLORET_BASE_ABS_MAX",
    "FLORET_COLOR_EDGES",
    "FLORET_COLOR_VERTS",
    "FLORET_COLOR_BOTH",
    "FLORET_NEI_EDGES",
    "FLORET_NEI_VERTS",
    "FLORET_NEI_BOTH",
    "FLORET_ABS_SCALE",
    "FLORET_ABS_POWER",
}
_LEGACY_FRAME_LABELS = {
    "Floretion Active Color",
    "Floretion Color Attributes",
    "Floretion Neighbor Attributes",
    "Floretion Base Vector",
}

# compat con versioni precedenti in cui c'erano i nodi "Multiply / Exponent"
NODE_ABS_SCALE_NAME = "FLORET_ABS_SCALE"
NODE_ABS_POWER_NAME = "FLORET_ABS_POWER"


def _get_or_create_output(nt: bpy.types.NodeTree) -> bpy.types.Node:
    for n in nt.nodes:
        if n.type == "OUTPUT_MATERIAL":
            return n
    n = nt.nodes.new("ShaderNodeOutputMaterial")
    n.location = (500, 0)
    return n


def _get_or_create_principled(nt: bpy.types.NodeTree) -> bpy.types.Node:
    for n in nt.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    n = nt.nodes.new("ShaderNodeBsdfPrincipled")
    n.location = (200, 0)
    return n


def _ensure_group_socket(ng: bpy.types.NodeTree, name: str, socket_type: str, *, in_out: str = "OUTPUT") -> None:
    try:
        if hasattr(ng, "interface"):
            for it in ng.interface.items_tree:
                try:
                    if getattr(it, "name", None) == name and getattr(it, "in_out", None) == in_out:
                        return
                except Exception:
                    pass
            ng.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
            return
    except Exception:
        pass

    try:
        if in_out == "OUTPUT":
            for s in ng.outputs:
                if s.name == name:
                    return
            ng.outputs.new(socket_type, name)
    except Exception:
        pass


def _clear_nodes(ng: bpy.types.NodeTree) -> None:
    try:
        ng.nodes.clear()
    except Exception:
        for n in list(ng.nodes):
            try:
                ng.nodes.remove(n)
            except Exception:
                pass


def _fac_out(node: bpy.types.Node):
    return node.outputs.get("Fac") or node.outputs.get("Factor") or node.outputs.get("Value")


_QUANTILE_MODE_OUTPUTS = {
    "QUANTILE_2": "Quantile 2 colors",
    "QUANTILE_4": "Quantile 4 colors",
    "QUANTILE_8": "Quantile 8 colors",
}

_DEFAULT_Q2 = [
    (0.12, 0.16, 0.22, 1.0),
    (0.95, 0.78, 0.18, 1.0),
]
_DEFAULT_Q4 = [
    (0.10, 0.12, 0.16, 1.0),
    (0.20, 0.52, 0.92, 1.0),
    (0.20, 0.78, 0.40, 1.0),
    (0.96, 0.88, 0.18, 1.0),
]
_DEFAULT_Q8 = [
    (0.07, 0.08, 0.11, 1.0),
    (0.16, 0.26, 0.55, 1.0),
    (0.15, 0.45, 0.82, 1.0),
    (0.18, 0.70, 0.78, 1.0),
    (0.22, 0.82, 0.40, 1.0),
    (0.90, 0.82, 0.20, 1.0),
    (0.94, 0.54, 0.18, 1.0),
    (0.86, 0.20, 0.22, 1.0),
]


def _set_group_input_default(ng: bpy.types.NodeTree, name: str, value) -> None:
    try:
        if hasattr(ng, "interface"):
            for it in ng.interface.items_tree:
                try:
                    if getattr(it, "name", None) == name and getattr(it, "in_out", None) == "INPUT":
                        it.default_value = value
                        return
                except Exception:
                    pass
    except Exception:
        pass
    try:
        sock = ng.inputs.get(name)
        if sock is not None:
            sock.default_value = value
    except Exception:
        pass


def _build_step_palette(
    nodes: bpy.types.Nodes,
    links,
    q_sock,
    color_socks,
    thresholds: list[float],
    *,
    origin=(0, 0),
):
    prev = color_socks[0]
    x0, y0 = origin
    for i, (thr, col_sock) in enumerate(zip(thresholds, color_socks[1:]), start=1):
        cmpn = nodes.new("ShaderNodeMath")
        cmpn.operation = 'GREATER_THAN'
        cmpn.location = (x0 + 180 * (i - 1), y0 - 140 * (i - 1))
        try:
            cmpn.inputs[1].default_value = float(thr)
        except Exception:
            pass
        try:
            links.new(q_sock, cmpn.inputs[0])
        except Exception:
            pass

        mix = nodes.new("ShaderNodeMixRGB")
        mix.blend_type = 'MIX'
        mix.location = (x0 + 180 * (i - 1) + 110, y0 - 140 * (i - 1))
        try:
            links.new(cmpn.outputs[0], mix.inputs[0])
        except Exception:
            pass
        try:
            links.new(prev, mix.inputs[1])
        except Exception:
            try:
                mix.inputs[1].default_value = prev.default_value
            except Exception:
                pass
        try:
            links.new(col_sock, mix.inputs[2])
        except Exception:
            try:
                mix.inputs[2].default_value = col_sock.default_value
            except Exception:
                pass

        prev = mix.outputs.get("Color") or mix.outputs[0]
    return prev


def _link_color_output_to_material(
    nt: bpy.types.NodeTree,
    n_col: bpy.types.Node,
    bsdf: bpy.types.Node,
    output_name: str,
) -> None:
    hsv = nt.nodes.get("FLORET_HSV_TWEAK")
    if hsv is None or hsv.type != "HUE_SAT":
        if hsv is not None:
            try:
                nt.nodes.remove(hsv)
            except Exception:
                pass
        hsv = nt.nodes.new("ShaderNodeHueSaturation")
        hsv.name = "FLORET_HSV_TWEAK"
        hsv.label = "HSV tweak"
        hsv.location = (bsdf.location.x - 220, bsdf.location.y + 10)
        try:
            hsv.inputs.get("Hue").default_value = 0.5
            hsv.inputs.get("Saturation").default_value = 1.0
            hsv.inputs.get("Value").default_value = 1.0
            hsv.inputs.get("Fac").default_value = 1.0
        except Exception:
            pass

    def _force_link(from_sock, to_sock):
        if from_sock is None or to_sock is None:
            return
        if getattr(to_sock, "is_linked", False):
            try:
                for lk in list(to_sock.links):
                    nt.links.remove(lk)
            except Exception:
                pass
        try:
            nt.links.new(from_sock, to_sock)
        except Exception:
            pass

    from_out = n_col.outputs.get(output_name) or n_col.outputs.get("From color mode")
    _force_link(from_out, hsv.inputs.get("Color"))

    col_out = hsv.outputs.get("Color")
    _force_link(col_out, bsdf.inputs.get("Emission") or bsdf.inputs.get("Base Color"))
    _force_link(col_out, bsdf.inputs.get("Base Color"))
def _get_or_create_base_vector_group() -> bpy.types.NodeTree:
    """
    7 outputs:
      Coeff, AbsCoeff, MinCoeff, MaxCoeff, MaxAbsCoeff, NormedCoeff, NormedAbsCoeff

    Nota importante:
    - NormedCoeff / NormedAbsCoeff ora leggono gli attributi quantile-based
      base_coeff_q / base_coeff_abs_q
    - MaxAbsCoeff resta disponibile come informazione/debug
    """
    ng = bpy.data.node_groups.get(BASE_GROUP_NAME)
    if ng is None or ng.bl_idname != "ShaderNodeTree":
        ng = bpy.data.node_groups.new(BASE_GROUP_NAME, "ShaderNodeTree")

    outs = [
        ("Coeff", "NodeSocketFloat"),
        ("AbsCoeff", "NodeSocketFloat"),
        ("MinCoeff", "NodeSocketFloat"),
        ("MaxCoeff", "NodeSocketFloat"),
        ("MaxAbsCoeff", "NodeSocketFloat"),
        ("NormedCoeff", "NodeSocketFloat"),
        ("NormedAbsCoeff", "NodeSocketFloat"),
    ]
    for name, st in outs:
        _ensure_group_socket(ng, name, st, in_out="OUTPUT")

    _clear_nodes(ng)
    nodes = ng.nodes
    links = ng.links

    n_out = nodes.new("NodeGroupOutput")
    n_out.location = (520, 0)

    def out(name: str):
        return n_out.inputs.get(name)

    # Attribute nodes
    n_c = nodes.new("ShaderNodeAttribute")
    n_c.location = (-480, 180)
    n_c.attribute_name = BASE_COEFF

    n_cq = nodes.new("ShaderNodeAttribute")
    n_cq.location = (-480, 60)
    n_cq.attribute_name = BASE_COEFF_Q

    n_aq = nodes.new("ShaderNodeAttribute")
    n_aq.location = (-480, -40)
    n_aq.attribute_name = BASE_COEFF_ABS_Q

    n_min = nodes.new("ShaderNodeAttribute")
    n_min.location = (-480, -160)
    n_min.attribute_name = BASE_COEFF_MIN

    n_max = nodes.new("ShaderNodeAttribute")
    n_max.location = (-480, -260)
    n_max.attribute_name = BASE_COEFF_MAX

    n_amax = nodes.new("ShaderNodeAttribute")
    n_amax.location = (-480, -360)
    n_amax.attribute_name = BASE_COEFF_ABS_MAX

    # Abs(base_coeff)
    n_abs = nodes.new("ShaderNodeMath")
    n_abs.location = (-220, 180)
    n_abs.operation = "ABSOLUTE"
    links.new(_fac_out(n_c), n_abs.inputs[0])

    # outputs wiring
    links.new(_fac_out(n_c), out("Coeff"))
    links.new(n_abs.outputs.get("Value"), out("AbsCoeff"))
    links.new(_fac_out(n_min), out("MinCoeff"))
    links.new(_fac_out(n_max), out("MaxCoeff"))
    links.new(_fac_out(n_amax), out("MaxAbsCoeff"))

    # qui i "Normed*" sono quantile-based
    links.new(_fac_out(n_cq), out("NormedCoeff"))
    links.new(_fac_out(n_aq), out("NormedAbsCoeff"))

    return ng


def _get_or_create_colors_group() -> bpy.types.NodeTree:
    """
    Node-group "Floretion Colors":
      - From color mode
      - Edges / Verts / EdgesAndVerts
      - Quantile 2 / 4 / 8 colors (palette pickers nel node group)
    """
    ng = bpy.data.node_groups.get(COLORS_GROUP_NAME)
    if ng is None or ng.bl_idname != "ShaderNodeTree":
        ng = bpy.data.node_groups.new(COLORS_GROUP_NAME, "ShaderNodeTree")

    # Outputs
    for name in (
        "From color mode",
        "Edges",
        "Verts",
        "EdgesAndVerts",
        "Quantile 2 colors",
        "Quantile 4 colors",
        "Quantile 8 colors",
    ):
        _ensure_group_socket(ng, name, "NodeSocketColor", in_out="OUTPUT")

    # Palette inputs shown in shader editor when selecting the group node
    for i in range(2):
        _ensure_group_socket(ng, f"Q2 Color {i+1}", "NodeSocketColor", in_out="INPUT")
    for i in range(4):
        _ensure_group_socket(ng, f"Q4 Color {i+1}", "NodeSocketColor", in_out="INPUT")
    for i in range(8):
        _ensure_group_socket(ng, f"Q8 Color {i+1}", "NodeSocketColor", in_out="INPUT")

    _clear_nodes(ng)
    nodes = ng.nodes
    links = ng.links

    n_in = nodes.new("NodeGroupInput")
    n_in.location = (-760, -260)

    n_out = nodes.new("NodeGroupOutput")
    n_out.location = (900, 0)

    # Frames per rendere il materiale più leggibile in Shader Editor
    fr_static = nodes.new("NodeFrame")
    fr_static.label = "Floretion Colors (Static)"
    fr_static.location = (-640, 180)

    fr_neighbor = nodes.new("NodeFrame")
    fr_neighbor.label = "Floretion Colors (Neighbor)"
    fr_neighbor.location = (-640, -40)

    fr_quant = nodes.new("NodeFrame")
    fr_quant.label = "Floretion Colors (Quantiles)"
    fr_quant.location = (-640, -360)

    def out(name: str):
        return n_out.inputs.get(name)

    # Raw color attributes
    a_active = nodes.new("ShaderNodeAttribute")
    a_active.location = (-580, 260)
    a_active.parent = fr_static
    a_active.name = "FLORET_ACTIVE_COLOR_ATTR"
    a_active.label = "From color mode"
    try:
        a_active.attribute_name = DEFAULT_COLOR_ATTR
    except Exception:
        pass

    a_e = nodes.new("ShaderNodeAttribute")
    a_e.location = (-580, 140)
    a_e.parent = fr_neighbor
    a_e.attribute_name = COLOR_EDGES

    a_v = nodes.new("ShaderNodeAttribute")
    a_v.location = (-580, 40)
    a_v.parent = fr_neighbor
    a_v.attribute_name = COLOR_VERTS

    a_b = nodes.new("ShaderNodeAttribute")
    a_b.location = (-580, -60)
    a_b.parent = fr_neighbor
    a_b.attribute_name = COLOR_BOTH

    # Quantile driver attribute on abs(coeff)
    a_q = nodes.new("ShaderNodeAttribute")
    a_q.location = (-580, -240)
    a_q.parent = fr_quant
    a_q.attribute_name = BASE_COEFF_ABS_Q
    q_sock = _fac_out(a_q)

    try:
        links.new(a_active.outputs.get("Color"), out("From color mode"))
    except Exception:
        pass
    try:
        links.new(a_e.outputs.get("Color"), out("Edges"))
    except Exception:
        pass
    try:
        links.new(a_v.outputs.get("Color"), out("Verts"))
    except Exception:
        pass
    try:
        links.new(a_b.outputs.get("Color"), out("EdgesAndVerts"))
    except Exception:
        pass

    # Palette input sockets
    q2_socks = []
    q4_socks = []
    q8_socks = []
    for i in range(2):
        nm = f"Q2 Color {i+1}"
        q2_socks.append(n_in.outputs.get(nm))
        _set_group_input_default(ng, nm, _DEFAULT_Q2[i])
    for i in range(4):
        nm = f"Q4 Color {i+1}"
        q4_socks.append(n_in.outputs.get(nm))
        _set_group_input_default(ng, nm, _DEFAULT_Q4[i])
    for i in range(8):
        nm = f"Q8 Color {i+1}"
        q8_socks.append(n_in.outputs.get(nm))
        _set_group_input_default(ng, nm, _DEFAULT_Q8[i])

    q2_out = _build_step_palette(nodes, links, q_sock, q2_socks, [0.5], origin=(-320, -240))
    q4_out = _build_step_palette(nodes, links, q_sock, q4_socks, [0.25, 0.50, 0.75], origin=(-320, -480))
    q8_out = _build_step_palette(nodes, links, q_sock, q8_socks, [i/8.0 for i in range(1, 8)], origin=(-320, -860))

    try:
        links.new(q2_out, out("Quantile 2 colors"))
    except Exception:
        pass
    try:
        links.new(q4_out, out("Quantile 4 colors"))
    except Exception:
        pass
    try:
        links.new(q8_out, out("Quantile 8 colors"))
    except Exception:
        pass

    return ng
def _cleanup_legacy_nodes(nt: bpy.types.NodeTree) -> None:
    for n in list(nt.nodes):
        try:
            if n.name in _LEGACY_NODE_NAMES:
                nt.nodes.remove(n)
        except Exception:
            pass

    for n in list(nt.nodes):
        try:
            if n.type == "FRAME" and (n.label in _LEGACY_FRAME_LABELS or n.name in _LEGACY_FRAME_LABELS):
                nt.nodes.remove(n)
        except Exception:
            pass


def ensure_neighbor_color_nodes(mat: bpy.types.Material | None) -> None:
    """
    Crea SOLO due node-group nel materiale:
      - Floretion Base Vector
      - Floretion Colors

    Collegamento di default:
      From color mode -> HSV -> Principled Emission (+ Base Color)

    Nota: NON ricolleghiamo più NormedAbsCoeff a Emission Strength.
    """
    if mat is None:
        return
    mat.use_nodes = True
    nt = mat.node_tree
    if nt is None:
        return

    out = _get_or_create_output(nt)
    bsdf = _get_or_create_principled(nt)

    try:
        surf = out.inputs.get("Surface")
        if surf is not None and not surf.is_linked:
            nt.links.new(bsdf.outputs.get("BSDF"), surf)
    except Exception:
        pass

    _cleanup_legacy_nodes(nt)

    ng_base = _get_or_create_base_vector_group()
    ng_col  = _get_or_create_colors_group()

    n_base = nt.nodes.get(NODE_BASE_NAME)
    if n_base is None or n_base.type != "GROUP":
        n_base = nt.nodes.new("ShaderNodeGroup")
        n_base.name = NODE_BASE_NAME
        n_base.label = "Floretion Base Vector"
        n_base.location = (bsdf.location.x - 520, bsdf.location.y - 240)
    n_base.node_tree = ng_base

    n_col = nt.nodes.get(NODE_COLORS_NAME)
    if n_col is None or n_col.type != "GROUP":
        n_col = nt.nodes.new("ShaderNodeGroup")
        n_col.name = NODE_COLORS_NAME
        n_col.label = "Floretion Colors"
        n_col.location = (bsdf.location.x - 420, bsdf.location.y + 0)
    n_col.node_tree = ng_col

    # Allinea anche i default dei picker sul nodo-materiale, non solo nel node-group:
    # Blender a volte mostra i socket dell'istanza tutti neri anche se il group ha default buoni.
    try:
        for i, rgba in enumerate(_DEFAULT_Q2, start=1):
            sock = n_col.inputs.get(f"Q2 Color {i}")
            if sock is not None:
                sock.default_value = rgba
        for i, rgba in enumerate(_DEFAULT_Q4, start=1):
            sock = n_col.inputs.get(f"Q4 Color {i}")
            if sock is not None:
                sock.default_value = rgba
        for i, rgba in enumerate(_DEFAULT_Q8, start=1):
            sock = n_col.inputs.get(f"Q8 Color {i}")
            if sock is not None:
                sock.default_value = rgba
    except Exception:
        pass

    hsv = nt.nodes.get("FLORET_HSV_TWEAK")
    if hsv is None or hsv.type != "HUE_SAT":
        if hsv is not None:
            try:
                nt.nodes.remove(hsv)
            except Exception:
                pass
        hsv = nt.nodes.new("ShaderNodeHueSaturation")
        hsv.name = "FLORET_HSV_TWEAK"
        hsv.label = "HSV tweak"
        hsv.location = (bsdf.location.x - 220, bsdf.location.y + 10)
        try:
            hsv.inputs.get("Hue").default_value = 0.5
            hsv.inputs.get("Saturation").default_value = 1.0
            hsv.inputs.get("Value").default_value = 1.0
            hsv.inputs.get("Fac").default_value = 1.0
        except Exception:
            pass

    _link_color_output_to_material(nt, n_col, bsdf, "From color mode")




def set_neighbor_color_mode(mat: bpy.types.Material | None, color_mode_id: str) -> None:
    """
    Aggiorna quale layer / output viene letto da:
      - Floretion Colors -> output collegato al materiale
    """
    if mat is None or mat.node_tree is None:
        return

    nt = mat.node_tree
    mode = str(color_mode_id or "").strip()

    if mode == "NEIGH_EDGE_HUE":
        target_attr = COLOR_EDGES
        target_output = "Edges"
    elif mode == "NEIGH_VERT_HUE":
        target_attr = COLOR_VERTS
        target_output = "Verts"
    elif mode == "NEIGH_EDGE_SAT":
        target_attr = COLOR_BOTH
        target_output = "EdgesAndVerts"
    elif mode in _QUANTILE_MODE_OUTPUTS:
        target_attr = DEFAULT_COLOR_ATTR
        target_output = _QUANTILE_MODE_OUTPUTS[mode]
    else:
        target_attr = DEFAULT_COLOR_ATTR
        target_output = "From color mode"

    try:
        ng = bpy.data.node_groups.get(COLORS_GROUP_NAME)
        if ng is not None and getattr(ng, "nodes", None) is not None:
            n = ng.nodes.get("FLORET_ACTIVE_COLOR_ATTR")
            if n is not None and getattr(n, "type", "") == "ATTRIBUTE":
                try:
                    n.attribute_name = target_attr
                except Exception:
                    pass
    except Exception:
        pass

    for name in ("FLORET_COLOR_ATTR_MAIN", "FLORET_COLOR_READER"):
        n = nt.nodes.get(name)
        if n is None:
            continue
        if getattr(n, "type", "") == "ATTRIBUTE":
            try:
                n.attribute_name = target_attr
            except Exception:
                pass
        if getattr(n, "type", "") == "VERTEX_COLOR":
            try:
                n.layer_name = target_attr
            except Exception:
                pass

    try:
        n_col = nt.nodes.get(NODE_COLORS_NAME)
        bsdf = _get_or_create_principled(nt)
        if n_col is not None and bsdf is not None:
            _link_color_output_to_material(nt, n_col, bsdf, target_output)
    except Exception:
        pass

    try:
        mat["floret_color_attr"] = target_attr
        mat["floret_color_mode"] = mode
    except Exception:
        pass
