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

# Datablock names (stabili => niente duplicati)
BASE_GROUP_NAME   = "FLORET_BASE_VECTOR_GROUP"
COLORS_GROUP_NAME = "FLORET_COLORS_GROUP"

# Node instance names (stabili => niente duplicati)
NODE_BASE_NAME   = "FLORET_NODE_BASE_VECTOR"
NODE_COLORS_NAME = "FLORET_NODE_COLORS"

# Legacy nodes/frames da ripulire (solo roba nostra storica)
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
}
_LEGACY_FRAME_LABELS = {
    "Floretion Active Color",
    "Floretion Color Attributes",
    "Floretion Neighbor Attributes",
    "Floretion Base Vector",
}


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
    """
    Blender 4/5: interface API.
    Fallback: ng.outputs.
    """
    # Interface API (Blender recente)
    try:
        if hasattr(ng, "interface"):
            # esiste già?
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

    # Vecchia API
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


def _get_or_create_base_vector_group() -> bpy.types.NodeTree:
    """
    7 outputs:
      Coeff, AbsCoeff, MinCoeff, MaxCoeff, MaxAbsCoeff, NormedCoeff, NormedAbsCoeff
    """
    ng = bpy.data.node_groups.get(BASE_GROUP_NAME)
    if ng is None or ng.bl_idname != "ShaderNodeTree":
        ng = bpy.data.node_groups.new(BASE_GROUP_NAME, "ShaderNodeTree")

    # outputs
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

    # rebuild interno (non crea nuovi nodi nel materiale, solo dentro il datablock)
    _clear_nodes(ng)
    nodes = ng.nodes
    links = ng.links

    n_out = nodes.new("NodeGroupOutput")
    n_out.location = (520, 0)

    def out(name: str):
        return n_out.inputs.get(name)

    # Attribute nodes
    n_c = nodes.new("ShaderNodeAttribute")
    n_c.location = (-420, 140)
    n_c.attribute_name = BASE_COEFF

    n_min = nodes.new("ShaderNodeAttribute")
    n_min.location = (-420, 20)
    n_min.attribute_name = BASE_COEFF_MIN

    n_max = nodes.new("ShaderNodeAttribute")
    n_max.location = (-420, -60)
    n_max.attribute_name = BASE_COEFF_MAX

    n_amax = nodes.new("ShaderNodeAttribute")
    n_amax.location = (-420, -140)
    n_amax.attribute_name = BASE_COEFF_ABS_MAX

    # Abs
    n_abs = nodes.new("ShaderNodeMath")
    n_abs.location = (-180, 140)
    n_abs.operation = "ABSOLUTE"
    links.new(_fac_out(n_c), n_abs.inputs[0])

    # safe denom = max(MaxAbsCoeff, eps)
    n_eps = nodes.new("ShaderNodeValue")
    n_eps.location = (-180, -220)
    n_eps.outputs[0].default_value = 1e-12

    n_safe = nodes.new("ShaderNodeMath")
    n_safe.location = (40, -140)
    n_safe.operation = "MAXIMUM"
    links.new(_fac_out(n_amax), n_safe.inputs[0])
    links.new(n_eps.outputs[0], n_safe.inputs[1])

    # NormedCoeff = Coeff / safe
    n_div_c = nodes.new("ShaderNodeMath")
    n_div_c.location = (40, 60)
    n_div_c.operation = "DIVIDE"
    links.new(_fac_out(n_c), n_div_c.inputs[0])
    links.new(_fac_out(n_safe), n_div_c.inputs[1])

    # NormedAbsCoeff = Abs / safe
    n_div_a = nodes.new("ShaderNodeMath")
    n_div_a.location = (40, -20)
    n_div_a.operation = "DIVIDE"
    links.new(n_abs.outputs.get("Value"), n_div_a.inputs[0])
    links.new(_fac_out(n_safe), n_div_a.inputs[1])

    # outputs wiring
    links.new(_fac_out(n_c), out("Coeff"))
    links.new(n_abs.outputs.get("Value"), out("AbsCoeff"))
    links.new(_fac_out(n_min), out("MinCoeff"))
    links.new(_fac_out(n_max), out("MaxCoeff"))
    links.new(_fac_out(n_amax), out("MaxAbsCoeff"))
    links.new(n_div_c.outputs.get("Value"), out("NormedCoeff"))
    links.new(n_div_a.outputs.get("Value"), out("NormedAbsCoeff"))

    return ng


def _get_or_create_colors_group() -> bpy.types.NodeTree:
    """
    Node-group "Floretion Colors":
      - From color mode  (layer scelto dal pannello "Color Mode" dell'add-on)
      - Edges
      - Verts
      - EdgesAndVerts

    NOTA: "From color mode" è un Attribute node dentro al gruppo con nome fisso
    "FLORET_ACTIVE_COLOR_ATTR". La funzione set_neighbor_color_mode() aggiorna
    attribute_name di quel nodo, così l'output segue davvero la scelta UI.
    """
    ng = bpy.data.node_groups.get(COLORS_GROUP_NAME)
    if ng is None or ng.bl_idname != "ShaderNodeTree":
        ng = bpy.data.node_groups.new(COLORS_GROUP_NAME, "ShaderNodeTree")

    # outputs (API nuova/vecchia)
    for name in ("From color mode", "Edges", "Verts", "EdgesAndVerts"):
        _ensure_group_socket(ng, name, "NodeSocketColor", in_out="OUTPUT")

    _clear_nodes(ng)
    nodes = ng.nodes
    links = ng.links

    n_out = nodes.new("NodeGroupOutput")
    n_out.location = (420, 0)

    def out(name: str):
        return n_out.inputs.get(name)

    # Active / "from color mode" (viene aggiornato da set_neighbor_color_mode)
    a_active = nodes.new("ShaderNodeAttribute")
    a_active.location = (-260, 240)
    a_active.name = "FLORET_ACTIVE_COLOR_ATTR"
    a_active.label = "From color mode"
    try:
        a_active.attribute_name = DEFAULT_COLOR_ATTR
    except Exception:
        pass

    # Fixed neighbor previews
    a_e = nodes.new("ShaderNodeAttribute")
    a_e.location = (-260, 120)
    try:
        a_e.attribute_name = COLOR_EDGES
    except Exception:
        pass

    a_v = nodes.new("ShaderNodeAttribute")
    a_v.location = (-260, 20)
    try:
        a_v.attribute_name = COLOR_VERTS
    except Exception:
        pass

    a_b = nodes.new("ShaderNodeAttribute")
    a_b.location = (-260, -80)
    try:
        a_b.attribute_name = COLOR_BOTH
    except Exception:
        pass

    # wiring
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

    return ng



def _cleanup_legacy_nodes(nt: bpy.types.NodeTree) -> None:
    # rimuovi nodi per nome
    for n in list(nt.nodes):
        try:
            if n.name in _LEGACY_NODE_NAMES:
                nt.nodes.remove(n)
        except Exception:
            pass

    # rimuovi frame legacy per label/name
    for n in list(nt.nodes):
        try:
            if n.type == "FRAME" and (n.label in _LEGACY_FRAME_LABELS or n.name in _LEGACY_FRAME_LABELS):
                nt.nodes.remove(n)
        except Exception:
            pass


def ensure_neighbor_color_nodes(mat: bpy.types.Material | None) -> None:
    """
    Crea SOLO due node-group nel materiale:
      - Floretion Base Vector (7 outputs)
      - Floretion Colors (4 outputs: From color mode + 3 neighbor)

    In più:
      - collega di default "From color mode" -> (HueSatValue) -> Principled Emission (+ Base Color)
      - evita duplicati, e non mette più valanghe di nodi sparsi
    """
    if mat is None:
        return
    mat.use_nodes = True
    nt = mat.node_tree
    if nt is None:
        return

    out = _get_or_create_output(nt)
    bsdf = _get_or_create_principled(nt)

    # Collega BSDF -> Output se manca
    try:
        surf = out.inputs.get("Surface")
        if surf is not None and not surf.is_linked:
            nt.links.new(bsdf.outputs.get("BSDF"), surf)
    except Exception:
        pass

    # pulizia “soft” (solo roba nostra vecchia)
    _cleanup_legacy_nodes(nt)

    # gruppi datablock
    ng_base = _get_or_create_base_vector_group()
    ng_col  = _get_or_create_colors_group()

    # node instances (stabili)
    n_base = nt.nodes.get(NODE_BASE_NAME)
    if n_base is None or n_base.type != "GROUP":
        n_base = nt.nodes.new("ShaderNodeGroup")
        n_base.name = NODE_BASE_NAME
        n_base.label = "Floretion Base Vector"
        n_base.node_tree = ng_base
        n_base.location = (bsdf.location.x - 420, bsdf.location.y - 240)
    else:
        n_base.node_tree = ng_base

    n_col = nt.nodes.get(NODE_COLORS_NAME)
    if n_col is None or n_col.type != "GROUP":
        n_col = nt.nodes.new("ShaderNodeGroup")
        n_col.name = NODE_COLORS_NAME
        n_col.label = "Floretion Colors"
        n_col.node_tree = ng_col
        n_col.location = (bsdf.location.x - 420, bsdf.location.y + 0)
    else:
        n_col.node_tree = ng_col

    # Hue/Sat/Val tweak (opzionale ma utile)
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

        # valori "neutri"
        try:
            # Blender: Hue=0.5 (no shift), Sat=1, Val=1, Fac=1
            hsv.inputs.get("Hue").default_value = 0.5
            hsv.inputs.get("Saturation").default_value = 1.0
            hsv.inputs.get("Value").default_value = 1.0
            hsv.inputs.get("Fac").default_value = 1.0
        except Exception:
            pass

    # link helper (con override solo sul materiale "FloretionMaterial")
    def _force_link(from_sock, to_sock):
        if from_sock is None or to_sock is None:
            return
        try:
            # se è già linkato e non è il nostro materiale standard, non tocchiamo
            if to_sock.is_linked and mat.name != "FloretionMaterial":
                return
        except Exception:
            pass

        # per FloretionMaterial: sovrascrivi sempre, così l'output del gruppo diventa "default"
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

    # Collega: Colors.FromColorMode -> HSV -> Principled Emission (+ BaseColor)
    from_out = n_col.outputs.get("From color mode")
    _force_link(from_out, hsv.inputs.get("Color"))

    col_out = hsv.outputs.get("Color")
    _force_link(col_out, bsdf.inputs.get("Emission") or bsdf.inputs.get("Emission Color"))
    _force_link(col_out, bsdf.inputs.get("Base Color"))



def set_neighbor_color_mode(mat: bpy.types.Material | None, color_mode_id: str) -> None:
    """
    Aggiorna quale layer viene letto da:
      - Floretion Colors -> output "From color mode"

    Non crea nodi nuovi "a cascata": aggiorna solo l'Attribute node interno al gruppo
    (name fisso: "FLORET_ACTIVE_COLOR_ATTR") + mantiene compat per vecchi materiali.
    """
    if mat is None or mat.node_tree is None:
        return

    nt = mat.node_tree
    mode = str(color_mode_id or "").strip()

    if mode == "NEIGH_EDGE_HUE":
        target = COLOR_EDGES
    elif mode == "NEIGH_VERT_HUE":
        target = COLOR_VERTS
    elif mode == "NEIGH_EDGE_SAT":
        target = COLOR_BOTH
    else:
        target = DEFAULT_COLOR_ATTR

    # 1) aggiorna il nodo interno del group datablock (Floretion Colors)
    try:
        ng = bpy.data.node_groups.get(COLORS_GROUP_NAME)
        if ng is not None and getattr(ng, "nodes", None) is not None:
            n = ng.nodes.get("FLORET_ACTIVE_COLOR_ATTR")
            if n is not None and getattr(n, "type", "") == "ATTRIBUTE":
                try:
                    n.attribute_name = target
                except Exception:
                    pass
    except Exception:
        pass

    # 2) compat: se trovi vecchi main-reader, aggiorna anche loro
    for name in ("FLORET_COLOR_ATTR_MAIN", "FLORET_COLOR_READER"):
        n = nt.nodes.get(name)
        if n is None:
            continue
        if getattr(n, "type", "") == "ATTRIBUTE":
            try:
                n.attribute_name = target
            except Exception:
                pass
        if getattr(n, "type", "") == "VERTEX_COLOR":
            try:
                n.layer_name = target
            except Exception:
                pass

    # 3) salva setting sul materiale (utile per debug/inspect)
    try:
        mat["floret_color_attr"] = target
    except Exception:
        pass
